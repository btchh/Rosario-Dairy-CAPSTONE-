from rest_framework import viewsets
from rest_framework.response import Response
from ..models import SystemSettings
from ..serializers import SystemSettingsSerializer
from accounts.permissions import IsAdmin
from typing import Any

class SystemSettingsViewSet(viewsets.ModelViewSet):
    queryset = SystemSettings.objects.all()
    serializer_class = SystemSettingsSerializer
    permission_classes = [IsAdmin]
    http_method_names = ['get', 'put', 'patch']

    def get_object(self) -> Any:
        return SystemSettings.get_config()

    def list(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)