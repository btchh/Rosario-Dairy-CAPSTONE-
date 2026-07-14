from rest_framework import viewsets, status
from rest_framework.response import Response
from ..models import Product
from ..serializers import ProductSerializer
from accounts.permissions import IsAdmin, IsStaff

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer
    permission_classes = [IsAdmin | IsStaff]

    def destroy(self, request, *args, **kwargs):
        product = self.get_object()
        product.is_active = False
        product.save()
        return Response({'message': 'Product deactivated successfully.'}, status=status.HTTP_200_OK)