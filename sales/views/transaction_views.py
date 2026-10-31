from config.data_cache import cache_api_response
from config.renderers import CachedJSONRenderer
from django.conf import settings
from django.db.models import Count, Prefetch, Q, Sum
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.renderers import BrowsableAPIRenderer
from accounts.permissions import IsAdmin, IsStaff
from inventory.models import ProductBatch
from inventory.services.stock_check_service import _available_stock
from ..models import Transaction, TransactionItem
from ..serializers import TransactionSerializer
from ..visibility import visible_transactions
from ..services.order_service import void_transaction
from accounts.throttles import SaleWriteRateThrottle
from sales.history import filter_history, order_history


class CancellationInput(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000, default='')


class TransactionPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


def transaction_history_queryset(user):
    """Load receipt details with the same visibility rules for every history API."""
    batches = ProductBatch.objects.select_related('product__category').annotate(
        available_product_stock=_available_stock('product__batches', timezone.localdate())
    )
    items = TransactionItem.objects.prefetch_related(Prefetch(
        'product_batch', queryset=batches,
    ))
    queryset = Transaction.objects.select_related(
        'handled_by', 'customer', 'payment_updated_by'
    ).prefetch_related(Prefetch('items', queryset=items)).order_by('-created_at', '-id')
    return visible_transactions(user.role == 'staff', queryset)


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Sales history with an explicit admin cancellation action. Transactions are
    created through checkout, orders or manual encoding; arbitrary updates and
    deletion remain unavailable.

    Query params (all optional, shared by list and summary):
      start_date=YYYY-MM-DD
      end_date=YYYY-MM-DD
      payment_method=cash|online
      payment_status=pending|paid|unknown
      handled_by=<user id>
      customer_id=<customer id>
      include_voided=true   (defaults to false — voided sales hidden by default)
      search=<invoice number, customer name or handler username>
    Lists also accept ordering (see sales.history.order_history).
    """
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer
    permission_classes = [IsAdmin | IsStaff]
    pagination_class = TransactionPagination
    renderer_classes = [CachedJSONRenderer, BrowsableAPIRenderer]
    data_cache_timeout = settings.HISTORY_CACHE_TIMEOUT

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin],
            throttle_classes=[SaleWriteRateThrottle])
    def cancel(self, request, pk=None):
        sale = self.get_object()
        values = CancellationInput(data=request.data)
        values.is_valid(raise_exception=True)
        try:
            sale, _ = void_transaction(sale.pk, voided_by=request.user, **values.validated_data)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)
        return Response(self.get_serializer(sale).data)

    def get_queryset(self):
        # A batch can appear on hundreds of invoice lines. Fetch it once and
        # compute its product's current stock in the same query.
        return transaction_history_queryset(self.request.user)

    def filtered_history(self):
        return filter_history(self.get_queryset(), self.request.query_params)

    @action(detail=False, methods=['get'])
    @cache_api_response
    def summary(self, request):
        """Full filtered totals, independent of pagination and receipt details."""
        try:
            queryset = self.filtered_history()
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)
        aggregates = {'transaction_count': Count('pk'), 'total_revenue': Sum('total_amount')}
        for state in ('paid', 'pending', 'unknown'):
            condition = Q(payment_status=state)
            aggregates[state + '_count'] = Count('pk', filter=condition)
            aggregates[state + '_amount'] = Sum('total_amount', filter=condition)
        values = queryset.aggregate(**aggregates)
        # Explicit strings preserve cents and totals larger than one invoice.
        for field in ('total_revenue', 'paid_amount', 'pending_amount', 'unknown_amount'):
            values[field] = format(values[field], '.2f') if values[field] is not None else '0.00'
        return Response(values)

    @cache_api_response
    def list(self, request, *args, **kwargs):
        try:
            queryset = order_history(self.filtered_history(), request.query_params)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(self.get_serializer(queryset, many=True).data)
