from rest_framework import viewsets, status
from rest_framework.response import Response
from ..models import Supplier
from ..serializers import SupplierSerializer
from accounts.permissions import IsAdmin, IsStaff

class SupplierViewSet(viewsets.ModelViewSet):
  queryset = Supplier.objects.filter(is_active=True)
  serializer_class = SupplierSerializer
  permission_classes = [IsAdmin | IsStaff]

  def destroy(self, request, *args, **kwargs):
    supplier = self.get_object()
    supplier.is_active = False
    supplier.save()
    return Response({'message': 'Supplier deactivated successfully.'}, status=status.HTTP_200_OK)