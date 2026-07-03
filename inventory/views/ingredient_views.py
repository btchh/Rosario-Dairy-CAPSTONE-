from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import Ingredient
from ..serializers import IngredientSerializer
from ..services import batch_service
from accounts.permissions import IsAdmin, IsStaff

class IngredientViewSet(viewsets.ModelViewSet):
    queryset = Ingredient.objects.all()
    serializer_class = IngredientSerializer
    permission_classes = [IsAdmin | IsStaff]