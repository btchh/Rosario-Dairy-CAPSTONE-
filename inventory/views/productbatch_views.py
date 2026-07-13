from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import ProductBatch
from ..serializers import ProdBatchSerializer
from ..services import batch_service
from accounts.permissions import IsAdmin, IsStaff

class ProductBatchViewSet(viewsets.ModelViewSet):
    queryset = ProductBatch.objects.all()
    serializer_class = ProdBatchSerializer
    permission_classes = [IsAdmin | IsStaff]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        product_batch = batch_service.BatchService.create_product_batch(
            product=data['product'],
            quantity=data['quantity'],
            expiration_date=data['expiration_date'],
            date_received=data.get('date_received'),
            notes=data.get('notes', '')
        )
        return Response(ProdBatchSerializer(product_batch).data, status=status.HTTP_201_CREATED)