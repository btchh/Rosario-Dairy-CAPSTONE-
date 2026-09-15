from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from accounts.models import Users


def register_user(username, password, email, role, first_name, last_name, phone_number, address):
    email = Users.objects.normalize_email(str(email).strip())
    user = Users(
        username=str(username).strip(),
        email=email,
        role=role,
        first_name='' if first_name is None else first_name,
        last_name='' if last_name is None else last_name,
        phone_number=phone_number,
        address=address,
    )
    try:
        validate_password(password, user)
        user.full_clean(exclude=['password'])
    except ValidationError as exc:
        raise ValueError(' '.join(exc.messages)) from exc

    if Users.objects.filter(email__iexact=email).exists():
        raise ValueError('Username or email already exists.')

    user.set_password(password)
    try:
        with transaction.atomic():
            user.save()
    except IntegrityError as exc:
        raise ValueError('Username or email already exists.') from exc
    return user
