from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework import status
from ..models import Category
from ..serializers import CategorySerializer
from ..services import batch_service
from accounts.permissions import IsAdmin, IsStaff

class CategoryViewSet(viewsets.ModelViewSet):
  queryset = Category.objects.filter(is_active=True)
  serializer_class = CategorySerializer
  permission_classes = [IsAdmin, IsStaff]