from rest_framework import viewsets
from ..models import StockAdjustment
from ..serializers import StockAdjustmentSerializer
from accounts.permissions import IsAdmin, IsStaff

class StockAdjustmentViewSet(viewsets.ModelViewSet):
    queryset = StockAdjustment.objects.all()
    serializer_class = StockAdjustmentSerializer
    permission_classes = [IsAdmin | IsStaff]
    