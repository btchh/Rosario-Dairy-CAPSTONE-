from config.api_inputs import object_body
from rest_framework import viewsets, status
from rest_framework.response import Response
from ..models import StockAdjustment, ProductBatch, IngredientBatch
from ..serializers import StockAdjustmentSerializer
from ..services import batch_service
from accounts.permissions import IsAdmin, IsStaff
from decimal import Decimal
from django.db.models import Q
from config.api_inputs import parse_decimal

class StockAdjustmentViewSet(viewsets.ModelViewSet):
    queryset = StockAdjustment.objects.all()
    serializer_class = StockAdjustmentSerializer
    permission_classes = [IsAdmin | IsStaff]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role == 'staff':
            qs = qs.filter(
                Q(product_batch__isnull=True) |
                Q(product_batch__product__is_active=True,
                  product_batch__product__category__is_active=True,
                  product_batch__product__category__is_visible_to_staff=True)
            )
        return qs
    
    @object_body
    def create(self, request, *args, **kwargs):
        product_batch_id = request.data.get('product_batch_id')
        ingredient_batch_id = request.data.get('ingredient_batch_id')
        adjustment_type = request.data.get('adjustment_type')
        unit_cost = request.data.get('unit_cost')
        reason = request.data.get('reason')

        valid_adjustment_types = [choice[0] for choice in StockAdjustment.ADJUSTMENT_TYPES]
        if adjustment_type not in valid_adjustment_types:
            return Response(
                {'error': f"Invalid adjustment_type. Must be one of {valid_adjustment_types}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if unit_cost is None:
            return Response({'error': 'unit_cost is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            quantity = parse_decimal(request.data.get('quantity'), 'quantity')
            unit_cost = parse_decimal(
                unit_cost, 'unit_cost', min_value=Decimal('0.00')
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            product_batch = ProductBatch.objects.select_related('product__category').get(id=product_batch_id) if product_batch_id else None
            ingredient_batch = IngredientBatch.objects.get(id=ingredient_batch_id) if ingredient_batch_id else None
        except (ProductBatch.DoesNotExist, IngredientBatch.DoesNotExist, ValueError, TypeError):
            return Response({'error': 'product_batch_id or ingredient_batch_id does not exist.'}, status=status.HTTP_400_BAD_REQUEST)

        if (request.user.role == 'staff' and product_batch and
                (not product_batch.product.is_active or
                 not product_batch.product.category.is_active or
                 not product_batch.product.category.is_visible_to_staff)):
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
