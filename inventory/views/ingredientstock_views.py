from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..services import batch_service
from ..serializers import LowStockIngredientSerializer, IngBatchSerializer
from accounts.permissions import IsAdmin, IsStaff

class LowStockIngredientView (APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    low_stock = batch_service.check_ingredient_stock()
    serializer = LowStockIngredientSerializer(low_stock, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)
  
class ExpiringIngredientView (APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    expiring = batch_service.check_ingredient_expiration()
    serializer = IngBatchSerializer(expiring, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)