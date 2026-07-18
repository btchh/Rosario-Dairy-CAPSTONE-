from rest_framework import viewsets
from ..models import ProductBatch
from ..serializers import ProdBatchSerializer
from accounts.permissions import IsAdmin, IsStaff
from .mixins import BatchCreateDestroyMixin

class ProductBatchViewSet(BatchCreateDestroyMixin, viewsets.ModelViewSet):
    queryset = ProductBatch.objects.all()
    serializer_class = ProdBatchSerializer
    permission_classes = [IsAdmin | IsStaff]