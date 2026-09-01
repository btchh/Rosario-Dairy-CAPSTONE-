from rest_framework import viewsets, status
from rest_framework.response import Response
from ..models import StockCount, ProductBatch, IngredientBatch
from ..serializers import StockCountSerializer
from ..services import batch_service
from accounts.permissions import IsAdmin, IsStaff
from decimal import Decimal, InvalidOperation
from django.db.models import Q

class StockCountViewSet(viewsets.ModelViewSet):
    queryset = StockCount.objects.all()
    serializer_class = StockCountSerializer
    permission_classes = [IsAdmin | IsStaff]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role == 'staff':
            qs = qs.filter(
                Q(product_batch__isnull=True) |
                Q(product_batch__product__category__is_visible_to_staff=True)
            )
        return qs

    def create(self, request, *args, **kwargs):
        product_batch_id = request.data.get('product_batch_id')
        ingredient_batch_id = request.data.get('ingredient_batch_id')
        counted_quantity = request.data.get('counted_quantity')
        notes = request.data.get('notes', '')

        try:
            counted_quantity = Decimal(str(counted_quantity))
        except (InvalidOperation, TypeError):
            return Response({'error': 'counted_quantity must be a valid number.'}, status=status.HTTP_400_BAD_REQUEST)

        if counted_quantity < 0:
            return Response({'error': 'counted_quantity cannot be negative.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            product_batch = ProductBatch.objects.get(id=product_batch_id) if product_batch_id else None
            ingredient_batch = IngredientBatch.objects.get(id=ingredient_batch_id) if ingredient_batch_id else None
        except (ProductBatch.DoesNotExist, IngredientBatch.DoesNotExist):
            return Response({'error': 'product_batch_id or ingredient_batch_id does not exist.'}, status=status.HTTP_400_BAD_REQUEST)

        if (request.user.role == 'staff' and product_batch and
                not product_batch.product.category.is_visible_to_staff):
            return Response({'error': 'product_batch_id or ingredient_batch_id does not exist.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            count = batch_service.BatchService.reconcile(
                counted_quantity=counted_quantity,
                counted_by=request.user,
                notes=notes,
                product_batch=product_batch,
                ingredient_batch=ingredient_batch
            )
            return Response(StockCountSerializer(count).data, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
