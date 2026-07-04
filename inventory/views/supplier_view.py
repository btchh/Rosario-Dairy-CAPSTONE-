from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import Supplier
from ..serializers import SupplierSerializer
from ..services import batch_service
from accounts.permissions import IsAdmin, IsStaff

class SupplierViewSet(viewsets.ModelViewSet):
  queryset = Supplier.objects.filter(is_active=True)
  serializer_class = SupplierSerializer
  permission_classes = [IsAdmin, IsStaff]