from rest_framework import viewsets, status
from rest_framework.decorators import action
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

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def reactivate(self, request, pk=None):
        try:
            ingredient = Ingredient.objects.get(pk=pk, is_active=False)
        except Ingredient.DoesNotExist:
            return Response({'error': 'Ingredient not found or already active.'}, status=status.HTTP_404_NOT_FOUND)
        ingredient.is_active = True
        ingredient.save()
        return Response({'message': 'Ingredient reactivated successfully.'}, status=status.HTTP_200_OK)