from rest_framework import viewsets
from ..models import FEFOConf
from ..serializers import FEFOConfSerializer
from accounts.permissions import IsAdmin, IsStaff

class FEFOConfViewSet(viewsets.ModelViewSet):
    queryset = FEFOConf.objects.all()
    serializer_class = FEFOConfSerializer
    permission_classes = [IsAdmin]