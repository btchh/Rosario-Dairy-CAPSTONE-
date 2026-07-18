from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
  class Meta:
    model = User
    fields = ['id', 'username', 'role']
    read_only_fields = ['id', 'username', 'role']

class UserDetailSerializer(serializers.ModelSerializer):
  class Meta:
    model = User
    fields = [
      'id', 'username', 'email', 'role', 'is_active', 'deactivation_reason',
      'first_name', 'last_name', 'phone_number', 'address', 'last_login'
    ]
    read_only_fields = fields