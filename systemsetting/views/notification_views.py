from rest_framework import viewsets
from rest_framework.response import Response
from ..models import NotificationSettings
from ..serializers import NotificationSettingsSerializer
from accounts.permissions import IsAdmin
from typing import Any

class NotificationSettingsViewSet(viewsets.ModelViewSet):
    queryset = NotificationSettings.objects.all()
    serializer_class = NotificationSettingsSerializer
    permission_classes = [IsAdmin]
    http_method_names = ['get', 'put', 'patch']

    def get_object(self) -> Any:
        return NotificationSettings.get_config()

    def list(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)