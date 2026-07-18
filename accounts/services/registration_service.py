from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from accounts.models import Users


def register_user(username, password, email, role, first_name, last_name, phone_number, address):
  valid_roles = [choice[0] for choice in Users.ROLE_CHOICES]
  if role not in valid_roles:
    raise ValueError(f"Invalid role '{role}'. Must be one of {valid_roles}.")
  try:
    validate_password(password)
  except ValidationError as e:
    raise ValueError(" ".join(str(m) for m in e.messages))

  user = Users.objects.create_user(
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