from rest_framework import viewsets
from ..models import Product
from ..serializers import ProductSerializer
from accounts.permissions import IsAdmin, IsStaff
from rest_framework.permissions import SAFE_METHODS
from .mixins import SoftDeleteMixin
from django.db.models import Prefetch
from django.utils import timezone
from ..models import ProductBatch

class ProductViewSet(SoftDeleteMixin, viewsets.ModelViewSet):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer
    model_label = "Product"

    def get_permissions(self):
        permission = (IsAdmin | IsStaff) if self.request.method in SAFE_METHODS else IsAdmin
        return [permission()]

    def get_queryset(self):
        qs = super().get_queryset().select_related('category').prefetch_related(Prefetch(
            'batches', queryset=ProductBatch.objects.filter(
                status='available', expiration_date__gte=timezone.localdate()
            ).only('product_id', 'remaining_quantity'), to_attr='available_batches_for_total',
        ))
        if self.request.user.role == 'staff':
            qs = qs.filter(is_active=True, category__is_active=True,
                           category__is_visible_to_staff=True)
        return qs
