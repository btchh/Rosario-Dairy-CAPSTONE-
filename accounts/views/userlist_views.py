from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin
from accounts.models import Users
from accounts.serializers import UserDetailSerializer


class UserListView(APIView):
  permission_classes = [IsAdmin]

  def get(self, request):
    users = Users.objects.all()
    return Response(UserDetailSerializer(users, many=True).data)