from rest_framework.generics import RetrieveUpdateAPIView

from accounts.permissions import IsAdmin
from inventory.models import FEFOConf
from inventory.serializers import FEFOConfSerializer


class InventorySettingsView(RetrieveUpdateAPIView):
    """Global stock and expiry alert thresholds (expiry values are in days)."""

    serializer_class = FEFOConfSerializer
    permission_classes = [IsAdmin]
    http_method_names = ['get', 'put', 'patch', 'head', 'options']

    def get_object(self):
        instance = FEFOConf.get_config()
        self.check_object_permissions(self.request, instance)
        return instance
