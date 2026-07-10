from rest_framework import viewsets
from ..models import Product
from ..serializers import ProductSerializer
from accounts.permissions import IsAdmin, IsStaff
from rest_framework.permissions import AllowAny #REMEMBER TO REMOVE THIS THIS IS FOR SANDBOX

# TODO: REMOVE ALLOW ANY

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [IsAdmin | IsStaff | AllowAny]