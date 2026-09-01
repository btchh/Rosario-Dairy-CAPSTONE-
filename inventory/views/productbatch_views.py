from rest_framework import viewsets
from ..models import ProductBatch
from ..serializers import ProdBatchSerializer
from accounts.permissions import IsAdmin, IsStaff
from .mixins import BatchCreateDestroyMixin

class ProductBatchViewSet(BatchCreateDestroyMixin, viewsets.ModelViewSet):
    queryset = ProductBatch.objects.all()
    serializer_class = ProdBatchSerializer
    permission_classes = [IsAdmin | IsStaff]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role == 'staff':
            qs = qs.filter(product__category__is_visible_to_staff=True)
        return qs
