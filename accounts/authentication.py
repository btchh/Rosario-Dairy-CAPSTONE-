from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


def check_token_version(user, token):
    # Pre-upgrade tokens belong to generation zero and are revoked on logout.
    if token.get('token_version', 0) != user.token_version:
        raise AuthenticationFailed('This session has been revoked.', code='session_revoked')


class RevocableJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        from accounts.services.session_service import check_session

        user = super().get_user(validated_token)
        check_token_version(user, validated_token)
        check_session(user, validated_token)
        return user
