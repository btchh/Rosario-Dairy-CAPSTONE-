from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from accounts.services import user_service

class LogoutView(APIView):
  permission_classes = [IsAdmin | IsStaff]
  
  def post(self, request):
    refresh_token = request.data.get('refresh_token')
    try:
      user_service.logout(refresh_token)
      return Response({'message': 'Logged out successfully'}, status=status.HTTP_200_OK)
    except Exception as e:
      return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)