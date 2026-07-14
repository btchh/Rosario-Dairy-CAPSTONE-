from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import IngredientBatch
from ..serializers import IngBatchSerializer
from ..services import batch_service
from accounts.permissions import IsAdmin, IsStaff

class IngredientBatchViewSet(viewsets.ModelViewSet):
    queryset = IngredientBatch.objects.all()
    serializer_class = IngBatchSerializer
    permission_classes = [IsAdmin | IsStaff]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        ingredient_batch = batch_service.BatchService.create_ingredient_batch(
            ingredient=data['ingredient'],
            supplier=data.get('supplier'),
            unit_price=data.get('unit_price'),
            quantity=data['quantity'],
            expiration_date=data['expiration_date'],
            date_received=data.get('date_received'),
            notes=data.get('notes', '')
        )
        return Response(IngBatchSerializer(ingredient_batch).data, status=status.HTTP_201_CREATED)