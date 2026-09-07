from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..services import batch_service
from ..serializers import LowStockIngredientSerializer, IngBatchSerializer
from accounts.permissions import IsAdmin, IsStaff
from systemsetting.models import NotificationSettings

class LowStockIngredientView (APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    if not NotificationSettings.get_config().low_stock_alerts:
      return Response([], status=status.HTTP_200_OK)
    low_stock = batch_service.BatchService.check_ingredient_stock()
    serializer = LowStockIngredientSerializer(low_stock, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)
  
class ExpiringIngredientView (APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    if not NotificationSettings.get_config().near_expiry_alerts:
      return Response([], status=status.HTTP_200_OK)
    expiring = batch_service.BatchService.check_ingredient_expiration()
    serializer = IngBatchSerializer(expiring, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)
