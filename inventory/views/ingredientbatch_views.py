from config.data_cache import CachedListMixin
from rest_framework import viewsets
from ..models import IngredientBatch
from ..serializers import IngBatchSerializer
from accounts.permissions import IsAdmin, IsStaff
from .mixins import BatchCreateDestroyMixin
from django.db.models import Prefetch
from django.utils import timezone
from ..models import Ingredient
from ..services.stock_check_service import _available_stock

class IngredientBatchViewSet(CachedListMixin, BatchCreateDestroyMixin, viewsets.ModelViewSet):
    queryset = IngredientBatch.objects.all()
    serializer_class = IngBatchSerializer
    permission_classes = [IsAdmin | IsStaff]

    def get_queryset(self):
        return super().get_queryset().prefetch_related(Prefetch(
            'ingredient', queryset=Ingredient.objects.annotate(
                available_stock=_available_stock('batches', timezone.localdate())
            ),
        ))
