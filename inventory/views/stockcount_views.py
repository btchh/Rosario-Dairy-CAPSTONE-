# cSpell:words viewsets
from config.data_cache import CachedListMixin
from config.api_inputs import object_body
from rest_framework import viewsets, status
from rest_framework.response import Response
from ..models import StockCount, ProductBatch
from ..serializers import StockCountSerializer
from ..services import batch_service
from accounts.permissions import IsAdmin, IsStaff
from decimal import Decimal
from config.api_inputs import parse_decimal, parse_optional_text, parse_positive_id

class StockCountViewSet(CachedListMixin, viewsets.ModelViewSet):
    queryset = StockCount.objects.all()
    serializer_class = StockCountSerializer
    permission_classes = [IsAdmin | IsStaff]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        qs = super().get_queryset().select_related('counted_by', 'product_batch__product__category', 'resulting_adjustment__adjusted_by', 'resulting_adjustment__product_batch__product__category')
        if self.request.user.role == 'staff':
            qs = qs.filter(
                product_batch__product__is_active=True,
                  product_batch__product__category__is_active=True,
                  product_batch__product__category__is_visible_to_staff=True
            )
        return qs

    @object_body
    def create(self, request, *args, **kwargs):
        product_batch_id = request.data.get('product_batch_id')
        if request.data.get('ingredient_batch_id') is not None:
            return Response({'error': 'Ingredient inventory has been removed. Use product_batch_id.'}, status=status.HTTP_400_BAD_REQUEST)
        counted_quantity = request.data.get('counted_quantity')
        notes = request.data.get('notes', '')

        try:
            product_batch_id = parse_positive_id(product_batch_id, 'product_batch_id')
            notes = parse_optional_text(notes, 'notes')
            counted_quantity = parse_decimal(
                counted_quantity, 'counted_quantity', min_value=Decimal('0.00')
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            product_batch = ProductBatch.objects.select_related('product__category').get(id=product_batch_id) if product_batch_id else None
        except (ProductBatch.DoesNotExist, ValueError, TypeError):
            return Response({'error': 'product_batch_id does not exist.'}, status=status.HTTP_400_BAD_REQUEST)

        if (request.user.role == 'staff' and product_batch and
                (not product_batch.product.is_active or
                 not product_batch.product.category.is_active or
                 not product_batch.product.category.is_visible_to_staff)):
            return Response({'error': 'product_batch_id does not exist.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            count = batch_service.BatchService.reconcile(
                counted_quantity=counted_quantity,
                counted_by=request.user,
                notes=notes,
                product_batch=product_batch,
            )
            return Response(StockCountSerializer(count).data, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
