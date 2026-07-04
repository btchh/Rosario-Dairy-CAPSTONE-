from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..services import batch_service
from ..serializers import LowStockProductSerializer, ProdBatchSerializer
from accounts.permissions import IsAdmin, IsStaff

class LowStockProductView (APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    low_stock = batch_service.check_product_stock()
    serializer = LowStockProductSerializer(low_stock, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)

class ExpiringProductView (APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    expiring = batch_service.check_product_expiration()
    serializer = ProdBatchSerializer(expiring, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)