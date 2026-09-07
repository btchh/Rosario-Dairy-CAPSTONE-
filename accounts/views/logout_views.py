from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.utils import get_md5_hash_password
from accounts.services import user_service


User = get_user_model()


class LogoutJWTAuthentication(JWTAuthentication):
  """Authenticate a valid access token even after its user is deactivated."""

  def get_user(self, validated_token):
    try:
      user_id = validated_token[api_settings.USER_ID_CLAIM]
    except KeyError as exc:
      raise AuthenticationFailed(
        _('Token contained no recognizable user identification'),
        code='token_not_valid',
      ) from exc

    try:
      user = User.objects.get(**{api_settings.USER_ID_FIELD: user_id})
    except User.DoesNotExist as exc:
      raise AuthenticationFailed(_('User not found'), code='user_not_found') from exc

    if api_settings.CHECK_REVOKE_TOKEN and validated_token.get(
      api_settings.REVOKE_TOKEN_CLAIM
    ) != get_md5_hash_password(user.password):
      raise AuthenticationFailed(
        _('The user password has been changed.'), code='password_changed'
      )
    return user


class LogoutView(APIView):
  authentication_classes = [LogoutJWTAuthentication]
  permission_classes = [IsAuthenticated]
  
  def post(self, request):
    refresh_token = request.data.get('refresh_token')
    try:
      user_service.logout(refresh_token)
      return Response({'message': 'Logged out successfully'}, status=status.HTTP_200_OK)
    except Exception as e:
      return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
