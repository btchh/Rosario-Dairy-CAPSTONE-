from rest_framework import viewsets, status
from rest_framework.response import Response
from ..models import Ingredient
from ..serializers import IngredientSerializer
from accounts.permissions import IsAdmin, IsStaff

class IngredientViewSet(viewsets.ModelViewSet):
    queryset = Ingredient.objects.filter(is_active=True)
    serializer_class = IngredientSerializer
    permission_classes = [IsAdmin | IsStaff]

    def destroy(self, request, *args, **kwargs):
        ingredient = self.get_object()
        ingredient.is_active = False
        ingredient.save()
        return Response({'message': 'Ingredient deactivated successfully.'}, status=status.HTTP_200_OK)