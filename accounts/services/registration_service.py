from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from accounts.models import Users


def register_user(username, password, email, role, first_name, last_name, phone_number, address):
    if not isinstance(password, str):
        raise ValueError('Password must be text.')
    if not 8 <= len(password) <= 25:
        raise ValueError('Password must be between 8 and 25 characters.')

    # Check the values Django will store, including NFKC username expansion.
    username = Users.normalize_username(str(username).strip())
    first_name = '' if first_name is None else str(first_name)
    last_name = '' if last_name is None else str(last_name)
    for label, value in (
        ('Username', username), ('First name', first_name), ('Last name', last_name),
    ):
        if len(value) > 50:
            raise ValueError(f'{label} must be at most 50 characters.')

    email = Users.objects.normalize_email(str(email).strip())
    user = Users(
        username=username,
        email=email,
        role=role,
        first_name=first_name,
        last_name=last_name,
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
