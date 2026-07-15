from rest_framework import viewsets, status
from rest_framework.decorators import action
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

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def reactivate(self, request, pk=None):
        try:
            product = Product.objects.get(pk=pk, is_active=False)
        except Product.DoesNotExist:
            return Response({'error': 'Product not found or already active.'}, status=status.HTTP_404_NOT_FOUND)
        product.is_active = True
        product.save()
        return Response({'message': 'Product reactivated successfully.'}, status=status.HTTP_200_OK)