from rest_framework import viewsets
from rest_framework.response import Response
from ..models import FEFOConf
from ..serializers import FEFOConfSerializer
from accounts.permissions import IsAdmin
from typing import Any

class FEFOConfViewSet(viewsets.ModelViewSet):
    queryset = FEFOConf.objects.all()
    serializer_class = FEFOConfSerializer
    permission_classes = [IsAdmin]
    http_method_names = ['get', 'put', 'patch']

    def get_object(self) -> Any:
        return FEFOConf.get_config()

    def list(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)