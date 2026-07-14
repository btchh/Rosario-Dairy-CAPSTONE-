from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import IngredientBatch
from ..serializers import IngBatchSerializer
from accounts.permissions import IsAdmin, IsStaff

class IngredientBatchViewSet(viewsets.ModelViewSet):
    queryset = IngredientBatch.objects.all()
    serializer_class = IngBatchSerializer
    permission_classes = [IsAdmin | IsStaff]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ingredient_batch = serializer.save()
        return Response(self.get_serializer(ingredient_batch).data, status=status.HTTP_201_CREATED)