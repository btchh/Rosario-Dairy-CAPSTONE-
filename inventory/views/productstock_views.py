from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..services import batch_service
from ..serializers import LowStockProductSerializer, ProdBatchSerializer
from accounts.permissions import IsAdmin, IsStaff
from systemsetting.models import NotificationSettings

class LowStockProductView (APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    if not NotificationSettings.get_config().low_stock_alerts:
      return Response([], status=status.HTTP_200_OK)
    low_stock = batch_service.BatchService.check_product_stock(
      visible_to_staff=request.user.role == 'staff'
    )
    serializer = LowStockProductSerializer(low_stock, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)

class ExpiringProductView (APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    if not NotificationSettings.get_config().near_expiry_alerts:
      return Response([], status=status.HTTP_200_OK)
    expiring = batch_service.BatchService.check_product_expiration(
      visible_to_staff=request.user.role == 'staff'
    )
    serializer = ProdBatchSerializer(expiring, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)
