from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from ..models import Customer
from ..serializers import CustomerSerializer
from accounts.permissions import IsAdmin


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer

    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return [IsAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)