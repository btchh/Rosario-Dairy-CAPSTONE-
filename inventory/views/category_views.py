from rest_framework import viewsets
from ..models import Category
from ..serializers import CategorySerializer
from accounts.permissions import IsAdmin, IsStaff

class CategoryViewSet(viewsets.ModelViewSet):
  queryset = Category.objects.filter(is_active=True)
  serializer_class = CategorySerializer
  permission_classes = [IsAdmin, IsStaff]