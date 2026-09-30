from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsStaff
from ..models import NotificationSettings, SystemSettings
from ..serializers import NotificationSettingsSerializer, SystemSettingsSerializer


class SettingsOverviewView(APIView):
    """Settings bootstrap with business settings visible only to admins."""

    permission_classes = [IsAdmin | IsStaff]

    def get(self, request):
        is_admin = request.user.role == 'admin'
        data = {
            'notifications': NotificationSettingsSerializer(
                NotificationSettings.get_config()
            ).data,
            'permissions': {
                'can_manage_settings': is_admin,
            },
        }
        if is_admin:
            data['system'] = SystemSettingsSerializer(SystemSettings.get_config()).data
        return Response(data)
