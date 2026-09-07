from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import SAFE_METHODS
from django.http import Http404

from accounts.permissions import IsAdmin, IsStaff
from ..models import NotificationSettings
from ..serializers import NotificationSettingsSerializer


class NotificationSettingsView(RetrieveUpdateAPIView):
    """Singleton notification preferences: staff read, admin update."""

    serializer_class = NotificationSettingsSerializer
    http_method_names = ['get', 'put', 'patch', 'head', 'options']

    def get_permissions(self):
        permission = (IsAdmin | IsStaff) if self.request.method in SAFE_METHODS else IsAdmin
        return [permission()]

    def get_object(self):
        if self.kwargs.get('pk') not in (None, 1):
            raise Http404
        instance = NotificationSettings.get_config()
        self.check_object_permissions(self.request, instance)
        return instance
