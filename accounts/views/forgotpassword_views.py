from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services import password_reset_service
from accounts.throttles import (
    PasswordResetConfirmRateThrottle,
    PasswordResetRequestRateThrottle,
)


OTP_SENT_MESSAGE = (
    'If the credentials match our records, a password reset code has been sent.'
)
RESET_MESSAGE = 'Password has been reset successfully.'
INVALID_OTP_MESSAGE = 'Invalid or expired password reset code.'


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRequestRateThrottle]

    def post(self, request):
        username = request.data.get('username')
        email = request.data.get('email')
        if not username or not email:
            return Response(
                {'error': "'username' and 'email' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        password_reset_service.request_password_reset(str(username), str(email))
        return Response({'message': OTP_SENT_MESSAGE}, status=status.HTTP_200_OK)


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetConfirmRateThrottle]

    def post(self, request):
        required = ('username', 'email', 'otp', 'new_password')
        values = {field: request.data.get(field) for field in required}
        if any(not values[field] for field in required):
            return Response(
                {
                    'error': (
                        "'username', 'email', 'otp', and 'new_password' "
                        'are required.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reset = password_reset_service.reset_password_with_otp(
                str(values['username']), str(values['email']),
                str(values['otp']), str(values['new_password']),
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if not reset:
            return Response(
                {'error': INVALID_OTP_MESSAGE}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response({'message': RESET_MESSAGE}, status=status.HTTP_200_OK)
