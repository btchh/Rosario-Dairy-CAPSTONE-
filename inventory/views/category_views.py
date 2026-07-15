from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from ..models import Category
from ..serializers import CategorySerializer
from accounts.permissions import IsAdmin, IsStaff

class CategoryViewSet(viewsets.ModelViewSet):
  queryset = Category.objects.filter(is_active=True)
  serializer_class = CategorySerializer
  permission_classes = [IsAdmin | IsStaff]

  def destroy(self, request, *args, **kwargs):
    category = self.get_object()
    category.is_active = False
    category.save()
    return Response({'message': 'Category deactivated successfully.'}, status=status.HTTP_200_OK)

  @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
  def reactivate(self, request, pk=None):
    try:
        category = Category.objects.get(pk=pk, is_active=False)
    except Category.DoesNotExist:
        return Response({'error': 'Category not found or already active.'}, status=status.HTTP_404_NOT_FOUND)
    category.is_active = True
    category.save()
    return Response({'message': 'Category reactivated successfully.'}, status=status.HTTP_200_OK)