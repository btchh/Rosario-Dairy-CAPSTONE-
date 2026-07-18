from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from accounts.services import user_service
from django.contrib.auth import get_user_model

User = get_user_model()

class GetUserView(APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    try:
      user = user_service.get_user(request.user)
      return Response({
        'username': user.username,
        'email': user.email,
        'role': user.role,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'phone_number': user.phone_number,
        'address': user.address,
        'last_login': user.last_login
      }, status=status.HTTP_200_OK)
    except User.DoesNotExist:
      return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)