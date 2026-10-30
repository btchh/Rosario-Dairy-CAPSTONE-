"""Compatibility responses only; retired inventory has no models or storage."""
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff


class RetiredInventoryView(APIView):
    permission_classes = [IsAdmin | IsStaff]

    def get(self, request, legacy_detail=''):
        if not legacy_detail:
            return Response([])
        return self.retired(request)

    def retired(self, request, **kwargs):
        return Response({'error': 'Ingredient and supplier management has been removed. Use product inventory.'}, status=410)

    post = put = patch = delete = retired
