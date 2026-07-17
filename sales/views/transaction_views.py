from datetime import datetime
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from ..models import Transaction
from ..serializers import TransactionSerializer


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only sales history. Transactions are created via CheckoutView or
    OrderViewSet.fulfill — never directly through this endpoint, so only
    list/retrieve are exposed.

    Query params (all optional, list endpoint only):
      start_date=YYYY-MM-DD
      end_date=YYYY-MM-DD
      payment_method=cash|online
      handled_by=<user id>
      include_voided=true   (defaults to false — voided sales hidden by default)
    """
    queryset = Transaction.objects.select_related('handled_by').prefetch_related(
        'items__product_batch__product'
    ).order_by('-created_at')
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _parse_date_param(value, field_name):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            raise ValueError(f"{field_name} must be in YYYY-MM-DD format.")

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        params = request.query_params

        start_date_raw = params.get('start_date')
        end_date_raw = params.get('end_date')

        try:
            start_date = self._parse_date_param(start_date_raw, 'start_date') if start_date_raw else None
            end_date = self._parse_date_param(end_date_raw, 'end_date') if end_date_raw else None
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if start_date and end_date and start_date > end_date:
            return Response({'error': 'start_date cannot be after end_date.'}, status=status.HTTP_400_BAD_REQUEST)

        if start_date:
            queryset = queryset.filter(created_at__date__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__date__lte=end_date)

        payment_method = params.get('payment_method')
        if payment_method:
            valid_methods = [choice[0] for choice in Transaction.PAYMENT_CHOICES]
            if payment_method not in valid_methods:
                return Response(
                    {'error': f"Invalid payment_method. Must be one of {valid_methods}."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            queryset = queryset.filter(payment_method=payment_method)

        handled_by = params.get('handled_by')
        if handled_by:
            try:
                handled_by = int(handled_by)
            except ValueError:
                return Response({'error': 'handled_by must be a valid user id.'}, status=status.HTTP_400_BAD_REQUEST)
            queryset = queryset.filter(handled_by_id=handled_by)

        include_voided = params.get('include_voided', 'false').lower() == 'true'
        if not include_voided:
            queryset = queryset.filter(is_voided=False)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)