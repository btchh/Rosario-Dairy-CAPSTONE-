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

def change_password(user, old_password,new_password):
  if not user.check_password(old_password):
    raise ValueError("Old Password is Incorrect")
  user.set_password(new_password)
  user.save()

def forgot_password(user, new_password):
  user.set_password(new_password)
  user.save()
