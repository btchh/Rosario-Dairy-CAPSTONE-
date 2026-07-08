from rest_framework import viewsets
from ..models import Supplier
from ..serializers import SupplierSerializer
from accounts.permissions import IsAdmin, IsStaff

class SupplierViewSet(viewsets.ModelViewSet):
  queryset = Supplier.objects.filter(is_active=True)
  serializer_class = SupplierSerializer
  permission_classes = [IsAdmin | IsStaff]