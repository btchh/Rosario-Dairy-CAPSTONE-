from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsStaff
from ..models import NotificationSettings, SystemSettings
from ..serializers import NotificationSettingsSerializer, SystemSettingsSerializer


class SettingsOverviewView(APIView):
    """One-call settings bootstrap for admin and staff clients."""

    permission_classes = [IsAdmin | IsStaff]

    def get(self, request):
        return Response({
            'system': SystemSettingsSerializer(SystemSettings.get_config()).data,
            'notifications': NotificationSettingsSerializer(
                NotificationSettings.get_config()
            ).data,
            'permissions': {
                'can_manage_settings': request.user.role == 'admin',
            },
        })
