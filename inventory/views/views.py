from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import Product, Ingredient, IngredientBatch, ProductBatch
from ..serializers import ProductSerializer, IngredientSerializer, ProdBatchSerializer, IngBatchSerializer
from ..services import batch_service

class IngredientViewSet(viewsets.ModelViewSet):
    queryset = Ingredient.objects.all()
    serializer_class = IngredientSerializer

class ProductBatchViewSet(viewsets.ModelViewSet):
    queryset = ProductBatch.objects.all()
    serializer_class = ProdBatchSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product_batch = batch_service.create_product_batch(serializer.validated_data)
        return Response(ProdBatchSerializer(product_batch).data, status=status.HTTP_201_CREATED)
    
class IngredientBatchViewSet(viewsets.ModelViewSet):
    queryset = IngredientBatch.objects.all()
    serializer_class = IngBatchSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ingredient_batch = batch_service.create_ingredient_batch(serializer.validated_data)
        return Response(IngBatchSerializer(ingredient_batch).data, status=status.HTTP_201_CREATED)  