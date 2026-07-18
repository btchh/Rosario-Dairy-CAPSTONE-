from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin
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

    required = {'username': username, 'password': password, 'email': email, 'role': role}
    missing = [field for field, value in required.items() if not value]
    if missing:
      return Response({'error': f"Missing required field(s): {','.join(missing)}"}, status=status.HTTP_400_BAD_REQUEST)

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
    except ValueError as e:
      return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
      return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)