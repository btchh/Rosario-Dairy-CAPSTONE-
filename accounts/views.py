# TODO: BABES REFACTOR THIS BULLSHITE! I CANNOT FOR THE LOVE OF HOLY MARY MOTHER OF GOD, I CANNOT FIND THE STUFF(S) I NEEED!! -- urs truly, self

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin, IsStaff
from django.contrib.auth import get_user_model
from accounts.services import user_service
from django.db import IntegrityError, transaction as db_transaction
from typing import TYPE_CHECKING, cast
from django.contrib.auth import get_user_model

if TYPE_CHECKING:
    from accounts.models import Users

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

class AdminResetPasswordView(APIView):
  permission_classes = [IsAdmin]

  def post(self, request):
    username = request.data.get('username')
    new_password = request.data.get('new_password')
    try:
      user = User.objects.get(username=username)
      user_service.forgot_password(user, new_password)
      return Response({'message': f"Password reset for {username}."}, status=status.HTTP_200_OK)
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
    
class UserListView(APIView):
  permission_classes = [IsAdmin]

  def get(self, request):
    users = User.objects.all().values('id', 'username', 'email', 'role', 'is_active', 'deactivation_reason', 'first_name', 'last_name')
    return Response(list(users), status=status.HTTP_200_OK)

class UserDetailView(APIView):
  permission_classes = [IsAdmin]

  def get(self, request, pk):
    try:
        user = cast("Users", User.objects.get(pk=pk))
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    return Response({
        'id': user.pk, 'username': user.username, 'email': user.email,
        'role': user.role, 'is_active': user.is_active,
        'first_name': user.first_name, 'last_name': user.last_name,
        'phone_number': user.phone_number, 'address': user.address
    })

  def patch(self, request, pk):
    with db_transaction.atomic():
        try:
            user = cast("Users", User.objects.select_for_update().get(pk=pk))
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        data = request.data
        is_self = user.pk == request.user.pk

        if is_self and ('role' in data or 'is_active' in data):
            return Response({'error': "You cannot change your own role or active status."}, status=status.HTTP_400_BAD_REQUEST)

        demoting = user.role == 'admin' and data.get('role', user.role) != 'admin'
        deactivating = user.role == 'admin' and user.is_active and data.get('is_active', True) is False
        if demoting or deactivating:
            # Same fix: force the lock to actually take effect.
            list(User.objects.select_for_update().filter(role='admin', is_active=True))
            remaining_admins = User.objects.filter(role='admin', is_active=True).exclude(pk=user.pk).count()
            if remaining_admins == 0:
                return Response({'error': "Cannot remove the last active admin."}, status=status.HTTP_400_BAD_REQUEST)

        NON_REACTIVATABLE = ['terminated', 'resigned']
        if data.get('is_active') is True and not user.is_active:
            if user.deactivation_reason in NON_REACTIVATABLE:
                reason_label = dict(Users.DEACTIVATION_REASONS).get(user.deactivation_reason, user.deactivation_reason)
                return Response({'error': f"This user was {reason_label} and cannot be reactivated directly."}, status=status.HTTP_400_BAD_REQUEST)
            user.deactivation_reason = 'none'

        for field in ['role', 'is_active', 'email', 'first_name', 'last_name', 'phone_number', 'address']:
            if field in data:
                setattr(user, field, data[field])
        user.save()
        return Response({'message': 'User updated successfully.'})

  def delete(self, request, pk):
      reason = request.data.get('reason', 'suspended')
      valid_reasons = [choice[0] for choice in Users.DEACTIVATION_REASONS if choice[0] != 'none']
      if reason not in valid_reasons:
          return Response({'error': f"Invalid reason. Must be one of {valid_reasons}."}, status=status.HTTP_400_BAD_REQUEST)

      with db_transaction.atomic():
          list(User.objects.select_for_update().filter(role='admin', is_active=True))

          try:
              user = cast("Users", User.objects.select_for_update().get(pk=pk))
          except User.DoesNotExist:
              return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

          if user.pk == request.user.pk:
              return Response({'error': "You cannot deactivate your own account."}, status=status.HTTP_400_BAD_REQUEST)

          if not user.is_active:
              return Response({'error': "User is already deactivated."}, status=status.HTTP_400_BAD_REQUEST)

          if user.role == 'admin':
              remaining_admins = User.objects.filter(role='admin', is_active=True).exclude(pk=user.pk).count()
              if remaining_admins == 0:
                  return Response({'error': "Cannot deactivate the last active admin."}, status=status.HTTP_400_BAD_REQUEST)

          user.is_active = False
          user.deactivation_reason = reason
          user.save()
          return Response({'message': f'User deactivated ({reason}).'}, status=status.HTTP_200_OK)