from rest_framework import viewsets
from ..models import Ingredient
from ..serializers import IngredientSerializer
from accounts.permissions import IsAdmin, IsStaff
from .mixins import SoftDeleteMixin

class IngredientViewSet(SoftDeleteMixin, viewsets.ModelViewSet):
    queryset = Ingredient.objects.filter(is_active=True)
    serializer_class = IngredientSerializer
    permission_classes = [IsAdmin | IsStaff]
    model_label = "Ingredient"