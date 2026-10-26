from config.data_cache import CachedListMixin
from rest_framework import viewsets
from rest_framework.permissions import SAFE_METHODS
from ..models import Ingredient
from ..serializers import IngredientSerializer
from accounts.permissions import IsAdmin, IsStaff
from .mixins import SoftDeleteMixin
from django.utils import timezone
from ..services.stock_check_service import _available_stock

class IngredientViewSet(CachedListMixin, SoftDeleteMixin, viewsets.ModelViewSet):
    queryset = Ingredient.objects.filter(is_active=True)
    serializer_class = IngredientSerializer
    permission_classes = [IsAdmin | IsStaff]

    def get_permissions(self):
        permission = (IsAdmin | IsStaff) if self.request.method in SAFE_METHODS or self.action == 'deactivate' else IsAdmin
        return [permission()]

    def get_queryset(self):
        return super().get_queryset().annotate(
            available_stock=_available_stock('batches', timezone.localdate())
        )
    model_label = "Ingredient"
