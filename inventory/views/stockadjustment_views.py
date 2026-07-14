from rest_framework import viewsets, status
from rest_framework.response import Response
from ..models import StockAdjustment, ProductBatch, IngredientBatch
from ..serializers import StockAdjustmentSerializer
from ..services import batch_service
from accounts.permissions import IsAdmin, IsStaff
from decimal import Decimal, InvalidOperation

class StockAdjustmentViewSet(viewsets.ModelViewSet):
    queryset = StockAdjustment.objects.all()
    serializer_class = StockAdjustmentSerializer
    permission_classes = [IsAdmin | IsStaff]
    
    def create(self, request, *args, **kwargs):
        product_batch_id = request.data.get('product_batch_id')
        ingredient_batch_id = request.data.get('ingredient_batch_id')
        adjustment_type = request.data.get('adjustment_type')
        unit_cost = request.data.get('unit_cost')
        reason = request.data.get('reason')

        try:
            quantity = Decimal(str(request.data.get('quantity')))
            unit_cost = Decimal(str(unit_cost)) if unit_cost not in (None, '') else None
        except (InvalidOperation, TypeError):
            return Response({'error': 'quantity and unit_cost must be valid numbers'}, status=status.HTTP_400_BAD_REQUEST)

        if unit_cost is None:
            return Response({'error': 'unit_cost is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            product_batch = ProductBatch.objects.get(id=product_batch_id) if product_batch_id else None
            ingredient_batch = IngredientBatch.objects.get(id=ingredient_batch_id) if ingredient_batch_id else None
        except (ProductBatch.DoesNotExist, IngredientBatch.DoesNotExist):
            return Response({'error': 'product_batch_id or ingredient_batch_id does not exist.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            adjustment = batch_service.BatchService.create_stock_adjustment(
                adjustment_type=adjustment_type,
                quantity=quantity,
                unit_cost=unit_cost,
                adjusted_by=request.user,
                reason=reason,
                product_batch=product_batch,
                ingredient_batch=ingredient_batch
            )
            return Response(StockAdjustmentSerializer(adjustment).data, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)