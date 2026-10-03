from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from ..models import Category
from ..serializers import CategorySerializer
from accounts.permissions import IsAdmin, IsStaff
from rest_framework.permissions import SAFE_METHODS
from .mixins import SoftDeleteMixin

class CategoryViewSet(SoftDeleteMixin, viewsets.ModelViewSet):
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategorySerializer
    model_label = "Category"

    @action(detail=False, methods=['get'], url_path='icon-options')
    def icon_options(self, request):
        return Response([
            {'value': '', 'label': 'Automatic from category name'},
            *({'value': value, 'label': label} for value, label in Category.ICON_CHOICES),
        ])

    def get_permissions(self):
        permission = (IsAdmin | IsStaff) if self.request.method in SAFE_METHODS else IsAdmin
        return [permission()]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role == 'staff':
            qs = qs.filter(is_active=True, is_visible_to_staff=True)
        return qs
