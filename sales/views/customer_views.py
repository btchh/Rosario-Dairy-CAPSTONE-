from config.data_cache import CachedListMixin, cache_api_response
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.response import Response
from django.db.models import ProtectedError
from django.db.models import Count, Max, Q
from rest_framework.decorators import action
from ..models import Customer
from ..serializers import CustomerSummarySerializer, TransactionSerializer
from accounts.permissions import IsAdmin, IsStaff
from .transaction_views import TransactionPagination, transaction_history_queryset
from ..visibility import visible_transactions


class CustomerViewSet(CachedListMixin, viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSummarySerializer
    data_cache_timeout = settings.API_DATA_CACHE_TIMEOUT

    @action(detail=True, methods=['get'], pagination_class=TransactionPagination,
            data_cache_timeout=settings.HISTORY_CACHE_TIMEOUT)
    @cache_api_response
    def transactions(self, request, pk=None):
        """GET /sales/customers/{id}/transactions/, newest receipts first.

        Returns {count, next, previous, results} with full transaction details.
        Accepts page, page_size (default 50, maximum 200), and include_voided
        (default false). Inactive customers retain access to their history.
        Staff receive only transactions allowed by sales visibility rules.
        """
        customer = self.get_object()
        queryset = transaction_history_queryset(request.user).filter(customer_id=customer.pk)
        if request.query_params.get('include_voided', 'false').lower() != 'true':
            queryset = queryset.filter(is_voided=False)
        page = self.paginate_queryset(queryset)
        serializer = TransactionSerializer(
            page if page is not None else queryset, many=True,
            context=self.get_serializer_context(),
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def get_queryset(self):
        active_sales = Q(transactions__is_voided=False)
        if self.request.user.role == 'staff':
            active_sales &= Q(transactions__in=visible_transactions(True).values('pk'))
        queryset = super().get_queryset()
        if self.action == 'list' and self.request.query_params.get('include_inactive', 'false').lower() != 'true':
            queryset = queryset.filter(is_active=True)
        return queryset.annotate(
            transaction_count=Count('transactions', filter=active_sales, distinct=True),
            last_sale=Max('transactions__created_at', filter=active_sales),
        )

    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy', 'reactivate']:
            return [IsAdmin()]
        return [(IsAdmin | IsStaff)()]

    def perform_create(self, serializer):
        customer = serializer.save(created_by=self.request.user)
        serializer.instance = self.get_queryset().get(pk=customer.pk)

    def destroy(self, request, *args, **kwargs):
        customer = self.get_object()
        try:
            customer.delete()
        except ProtectedError:
            customer.is_active = False
            customer.save(update_fields=['is_active', 'updated_at'])
            return Response(
                {'message': 'Customer deactivated because it has linked sales records.',
                 'deletion_type': 'deactivated'},
                status=status.HTTP_200_OK,
            )
        return Response({'message': 'Customer permanently deleted.',
                         'deletion_type': 'permanent'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def reactivate(self, request, pk=None):
        customer = self.get_object()
        customer.is_active = True
        customer.save(update_fields=['is_active', 'updated_at'])
        return Response({'message': 'Customer reactivated successfully.'})
