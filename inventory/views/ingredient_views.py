from rest_framework import viewsets
from ..models import Ingredient
from ..serializers import IngredientSerializer
from accounts.permissions import IsAdmin, IsStaff

class IngredientViewSet(viewsets.ModelViewSet):
    queryset = Ingredient.objects.all()
    serializer_class = IngredientSerializer
    permission_classes = [IsAdmin | IsStaff]