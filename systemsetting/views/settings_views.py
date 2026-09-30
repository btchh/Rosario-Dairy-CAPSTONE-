from rest_framework.generics import RetrieveUpdateAPIView
from django.http import Http404

from accounts.permissions import IsAdmin
from ..models import SystemSettings
from ..serializers import SystemSettingsSerializer


class SystemSettingsView(RetrieveUpdateAPIView):
    """Singleton business settings endpoint, accessible only to admins."""

    serializer_class = SystemSettingsSerializer
    permission_classes = [IsAdmin]
    http_method_names = ['get', 'put', 'patch', 'head', 'options']

    def get_object(self):
        if self.kwargs.get('pk') not in (None, 1):
            raise Http404
        instance = SystemSettings.get_config()
        self.check_object_permissions(self.request, instance)
        return instance
