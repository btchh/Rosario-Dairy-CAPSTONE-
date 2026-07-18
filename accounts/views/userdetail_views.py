from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin
from accounts.models import Users
from accounts.serializers import UserDetailSerializer
from accounts.services import user_service


class UserDetailView(APIView):
  permission_classes = [IsAdmin]

  def get(self, request, pk):
    try:
      user = user_service.get_user_detail(pk)
    except Users.DoesNotExist:
      return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    return Response(UserDetailSerializer(user).data)

  def patch(self, request, pk):
    try:
      user_service.update_user(pk, request.data, request.user)
    except Users.DoesNotExist:
      return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as e:
      return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': 'User updated successfully.'})

  def delete(self, request, pk):
    reason = request.data.get('reason', 'suspended')
    try:
      user_service.deactivate_user(pk, request.user, reason)
    except Users.DoesNotExist:
      return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as e:
      return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': f'User deactivated ({reason}).'})