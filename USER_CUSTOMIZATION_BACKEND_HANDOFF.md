# User customization backend handoff

Updated October 3, 2026. The backend account endpoints now support changing usernames as well as the existing profile fields. No frontend files were changed for this task.

## Admin edits to any user

`PATCH /accounts/users/<id>/` requires an authenticated admin. Send only fields being changed:

```json
{
  "username": "alex.reyes",
  "first_name": "Alex",
  "last_name": "Reyes",
  "email": "alex@example.com",
  "phone_number": "09171234567",
  "address": "Rosario"
}
```

The endpoint also retains its existing `role`, `is_active`, and `deactivation_reason` handling and guards. A successful response is HTTP 200 with `message` and the updated `user` object. Admins cannot use this route to change their own role or active status. Staff cannot use this route.

## Self profile edits

`PATCH /accounts/user/` allows an authenticated admin or staff member to edit their **own** `username`, `first_name`, `last_name`, `email`, `phone_number`, or `address`. The response is the updated profile object, including the new `username`. The existing five minute cooldown still applies to successful self edits. Role, active status, and other privilege fields remain blocked.

`GET /accounts/user/` returns the current profile. After a username change, use the new username for the next login; existing JWTs identify the user by ID and continue to work.

Username updates trim surrounding whitespace and reject blank, invalid, or case insensitive duplicate usernames with HTTP 400. Django's username field limit and character validator still apply. Email validation and existing uniqueness checks remain in place. A failed validation does not save any of the fields in the same request.

## Frontend connection

The existing admin user service calls `/accounts/users/<id>/`, but its update payload currently omits `username`; the Edit User form also displays username as read only. To let an admin rename a user, the frontend teammate should make that field editable and include `username` in the `userService.updateUser` payload. For an account settings form, call `PATCH /accounts/user/` and refresh the current user state from its response. Both calls use the existing authenticated HTTP client and `/backend` development proxy.

Backend files changed for this feature: `accounts/services/admin_service.py`, `accounts/services/profile_service.py`, `accounts/views/userdetail_views.py`, and `accounts/tests.py`. No database migration or new URL is needed.

Verification: `venv/bin/python manage.py test accounts.tests.UserDetailPatchValidationTests accounts.tests.UserDetailPatchGuardTests accounts.tests.ProfileEditCooldownTests --keepdb` passed 25 tests.
