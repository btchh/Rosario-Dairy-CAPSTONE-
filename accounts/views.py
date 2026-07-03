from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from django.contrib.auth import get_user_model
from accounts.services import user_service


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
    return Response({'message': 'User registered successfully'}, status=status.HTTP_201_CREATED)
  
class GetUserView(APIView):
  permission_classes = [IsAdmin | IsStaff]

  def get(self, request):
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