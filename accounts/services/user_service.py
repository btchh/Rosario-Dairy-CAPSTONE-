from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.models import Users
User = get_user_model()

def register_user(username, password, email, role, first_name, last_name, phone_number, address):
  valid_roles = [choice[0] for choice in Users.ROLE_CHOICES]
  if role not in valid_roles:
    raise ValueError(f"Invalid role '{role}'. Must be one of {valid_roles}.")
  try:
    validate_password(password)
  except ValidationError as e:
    raise ValueError(" ".join(str(m) for m in e.messages))

  user = User.objects.create_user(
    username=username,
    password=password,
    email=email,
    role=role,
    first_name=first_name,
    last_name=last_name,
    phone_number=phone_number,
    address=address
  )
  return user

def get_user(user):
  return user

def change_password(user, old_password,new_password):
  if not user.check_password(old_password):
    raise ValueError("Old Password is Incorrect")
  try:
    validate_password(new_password, user)
  except ValidationError as e:
    raise ValueError(" ".join(str(m) for m in e.messages))
  user.set_password(new_password)
  user.save()

def forgot_password(user, new_password):
  try:
    validate_password(new_password, user)
  except ValidationError as e:
    raise ValueError(" ".join(str(m) for m in e.messages))
  user.set_password(new_password)
  user.save()

def logout(refresh_token):
  token = RefreshToken(refresh_token)
  token.blacklist()
