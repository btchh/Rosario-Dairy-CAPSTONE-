from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsAdmin, IsStaff
from accounts.services.email_change_service import request_email_change, confirm_email_change
from accounts.throttles import EmailChangeRateThrottle


class EmailChangeInput(serializers.Serializer):
    email = serializers.EmailField()
    current_password = serializers.CharField(trim_whitespace=False, max_length=1024)
    otp = serializers.RegexField(r'^\d{6}$', required=False)


class EmailChangeView(APIView):
    permission_classes = [IsAdmin | IsStaff]
    throttle_classes = [EmailChangeRateThrottle]

    def post(self, request):
        data = EmailChangeInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        try:
            request_email_change(request.user, values['current_password'], values['email'])
        except ValueError as exc:
            raise serializers.ValidationError({'detail': str(exc)}) from exc
        return Response({'message': 'Verification code sent to the new email.'})

    def patch(self, request):
        data = EmailChangeInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        try:
            changed = confirm_email_change(request.user, values['current_password'],
                                           values['email'], values.get('otp'))
        except ValueError as exc:
            raise serializers.ValidationError({'detail': str(exc)}) from exc
        if not changed:
            raise serializers.ValidationError({'detail': 'Invalid or expired verification code.'})
        return Response({'message': 'Email updated. Please sign in again.'})
