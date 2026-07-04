from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from django.contrib.auth import get_user_model
from accounts.services import user_service
from django.db import IntegrityError


User = get_user_model()

class RegisterView(APIView):
  permission_classes = [IsAdmin]

  def post(self, request):
    username = request.data.get('username')
    password = request.data.get('password')
    email = request.data.get('email')
    role = request.data.get('role')
    first_name = request.data.get('first_name')
    last_name = request.data.get('last_name')
    phone_number = request.data.get('phone_number')
    address = request.data.get('address')

    try:
      user = user_service.register_user(
        username=username,
        password=password,
        email=email,
        role=role,
        first_name=first_name,
        last_name=last_name,
        phone_number=phone_number,
        address=address
      )
      return Response({'message' : 'User registered successfully'}, status=status.HTTP_201_CREATED)
    except IntegrityError:
      return Response({'error': 'Username or email already exists'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
      return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
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
        'address': user.address
      }, status=status.HTTP_200_OK)
    except User.DoesNotExist:
      return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
  
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

class ForgotPasswordView(APIView):
  def post(self, request):
    username = request.data.get('username')
    new_password = request.data.get('new_password')

    try:
      user = User.objects.get(username=username)
      user_service.forgot_password(user, new_password)
      return Response({'message': 'Password reset successfully'}, status=status.HTTP_200_OK)
    except User.DoesNotExist:
      return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

class LogoutView(APIView):
  permission_classes = [IsAdmin | IsStaff]
  
  def post(self, request):
    refresh_token = request.data.get('refresh_token')
    try:
      user_service.logout(refresh_token)
      return Response({'message': 'Logged out successfully'}, status=status.HTTP_200_OK)
    except Exception as e:
      return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)