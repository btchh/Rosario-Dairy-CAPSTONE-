from rest_framework import viewsets, status
from rest_framework.response import Response
from django.db.models import ProtectedError
from django.db.models import Count, Max, Q
from rest_framework.decorators import action
from ..models import Customer
from ..serializers import CustomerSummarySerializer
from accounts.permissions import IsAdmin, IsStaff


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSummarySerializer

    def get_queryset(self):
        active_sales = Q(transactions__is_voided=False)
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
