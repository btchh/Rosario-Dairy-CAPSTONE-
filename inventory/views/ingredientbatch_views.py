from rest_framework import viewsets
from ..models import IngredientBatch
from ..serializers import IngBatchSerializer
from accounts.permissions import IsAdmin, IsStaff
from .mixins import BatchCreateDestroyMixin

class IngredientBatchViewSet(BatchCreateDestroyMixin, viewsets.ModelViewSet):
    queryset = IngredientBatch.objects.all()
    serializer_class = IngBatchSerializer
    permission_classes = [IsAdmin | IsStaff]