from rest_framework import viewsets
from ..models import Category
from ..serializers import CategorySerializer
from accounts.permissions import IsAdmin, IsStaff
from rest_framework.permissions import SAFE_METHODS
from .mixins import SoftDeleteMixin

class CategoryViewSet(SoftDeleteMixin, viewsets.ModelViewSet):
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategorySerializer
    model_label = "Category"

    def get_permissions(self):
        permission = (IsAdmin | IsStaff) if self.request.method in SAFE_METHODS else IsAdmin
        return [permission()]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role == 'staff':
            qs = qs.filter(is_visible_to_staff=True)
        return qs
