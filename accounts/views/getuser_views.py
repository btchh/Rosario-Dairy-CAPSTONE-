from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from django.contrib.auth import get_user_model
from accounts.services import user_service

User = get_user_model()


class GetUserView(APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
    try:
      user = user_service.get_user(request.user)
      return Response(self._serialize(user), status=status.HTTP_200_OK)
    except User.DoesNotExist:
      return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

  def patch(self, request):
    try:
      user = user_service.update_own_profile(request.user, request.data)
    except ValueError as e:
      return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(self._serialize(user), status=status.HTTP_200_OK)
  
  @staticmethod
  def _serialize(user):
      return {
        'username': user.username,
        'email': user.email,
        'role': user.role,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'phone_number': user.phone_number,
        'address': user.address,
        'last_login': user.last_login,
        'profile_photo': user.profile_photo.url if user.profile_photo else None,
      }