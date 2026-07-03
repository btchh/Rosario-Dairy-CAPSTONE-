from django.contrib.auth import get_user_model
User = get_user_model()

def register_user(username, password, email, role, first_name, last_name, phone_number, address):
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