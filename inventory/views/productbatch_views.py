from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import ProductBatch
from ..serializers import ProdBatchSerializer
from accounts.permissions import IsAdmin, IsStaff

class ProductBatchViewSet(viewsets.ModelViewSet):
    queryset = ProductBatch.objects.all()
    serializer_class = ProdBatchSerializer
    permission_classes = [IsAdmin | IsStaff]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product_batch = serializer.save()
        return Response(self.get_serializer(product_batch).data, status=status.HTTP_201_CREATED)