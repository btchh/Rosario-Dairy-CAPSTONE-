from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken


def change_password(user, old_password, new_password):
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