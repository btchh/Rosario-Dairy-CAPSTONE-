from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from django.contrib.auth import get_user_model
from accounts.services import user_service

User = get_user_model()

class ChangePasswordView(APIView):
  permission_classes = [IsAdmin | IsStaff]

  def post(self, request):
    old_password = request.data.get('old_password')
    new_password = request.data.get('new_password')

    try:
      user_service.change_password(request.user, old_password, new_password)
      return Response({'message': 'Password changed successfully'}, status=status.HTTP_200_OK)
    except ValueError as e:
      return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

class AdminResetPasswordView(APIView):
  permission_classes = [IsAdmin]

  def post(self, request):
    username = request.data.get('username')
    new_password = request.data.get('new_password')

    if not username or not new_password:
      return Response({'error': "'username' and 'new_password' are required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
      user = User.objects.get(username=username)
      user_service.forgot_password(user, new_password)
      return Response({'message': f"Password reset for {username}."}, status=status.HTTP_200_OK)
    except User.DoesNotExist:
      return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as e:
      return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)