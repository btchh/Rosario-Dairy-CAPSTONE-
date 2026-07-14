from rest_framework import viewsets, status
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