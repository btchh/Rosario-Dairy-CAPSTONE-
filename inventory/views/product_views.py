from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import Product
from ..serializers import ProductSerializer
from ..services import batch_service

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

# TODO: Implement proper logic for product management