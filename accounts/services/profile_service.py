from typing import cast
from accounts.models import Users


def get_user(user):
  return user

def get_user_detail(pk):
  return cast(Users, Users.objects.get(pk=pk))