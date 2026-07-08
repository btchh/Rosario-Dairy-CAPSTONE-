from rest_framework import viewsets
from ..models import FEFOConf
from ..serializers import FEFOConfSerializer
from accounts.permissions import IsAdmin
from typing import Any

class FEFOConfViewSet(viewsets.ModelViewSet):
    queryset = FEFOConf.objects.all()
    serializer_class = FEFOConfSerializer
    permission_classes = [IsAdmin]

    def get_object(self) -> Any:
        return FEFOConf.get_config()
    
    http_method_names = ['get', 'put', 'patch']