from rest_framework import viewsets, status
from rest_framework.decorators import action
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

  @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
  def reactivate(self, request, pk=None):
    try:
        supplier = Supplier.objects.get(pk=pk, is_active=False)
    except Supplier.DoesNotExist:
        return Response({'error': 'Supplier not found or already active.'}, status=status.HTTP_404_NOT_FOUND)
    supplier.is_active = True
    supplier.save()
    return Response({'message': 'Supplier reactivated successfully.'}, status=status.HTTP_200_OK)