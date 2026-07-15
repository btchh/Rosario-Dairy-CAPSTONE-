from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import ProductBatch
from ..serializers import ProdBatchSerializer
from accounts.permissions import IsAdmin, IsStaff
from django.db.models import ProtectedError

class ProductBatchViewSet(viewsets.ModelViewSet):
    queryset = ProductBatch.objects.all()
    serializer_class = ProdBatchSerializer
    permission_classes = [IsAdmin | IsStaff]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product_batch = serializer.save()
        return Response(self.get_serializer(product_batch).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        batch = self.get_object()
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {'error': 'This batch has linked transactions or adjustments and cannot be deleted. Adjust its status instead.'},
                status=status.HTTP_400_BAD_REQUEST
            )