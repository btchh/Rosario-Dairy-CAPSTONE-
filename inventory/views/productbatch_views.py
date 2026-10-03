from rest_framework import viewsets
from ..models import ProductBatch
from ..serializers import ProdBatchSerializer
from accounts.permissions import IsAdmin, IsStaff
from .mixins import BatchCreateDestroyMixin
from django.db.models import Prefetch
from django.utils import timezone

class ProductBatchViewSet(BatchCreateDestroyMixin, viewsets.ModelViewSet):
    queryset = ProductBatch.objects.all()
    serializer_class = ProdBatchSerializer
    permission_classes = [IsAdmin | IsStaff]

    def get_queryset(self):
        qs = super().get_queryset().select_related('product__category').prefetch_related(Prefetch(
            'product__batches', queryset=ProductBatch.objects.filter(
                status='available', expiration_date__gte=timezone.localdate()
            ).only('product_id', 'remaining_quantity'), to_attr='available_batches_for_total',
        ))
        if self.action == 'list' and self.request.query_params.get('include_historical') != 'true':
            qs = qs.filter(is_historical_reference=False)
        if self.request.user.role == 'staff':
            qs = qs.filter(product__is_active=True, product__category__is_active=True,
                           product__category__is_visible_to_staff=True)
        return qs
