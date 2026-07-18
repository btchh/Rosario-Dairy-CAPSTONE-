from rest_framework import viewsets
from ..models import Product
from ..serializers import ProductSerializer
from accounts.permissions import IsAdmin, IsStaff
from .mixins import SoftDeleteMixin

class ProductViewSet(SoftDeleteMixin, viewsets.ModelViewSet):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer
    permission_classes = [IsAdmin | IsStaff]
    model_label = "Product"