from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.session_service import check_session, record_activity, session_payload
from accounts.throttles import SessionActivityRateThrottle
from config.api_inputs import object_body


class SessionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(session_payload(check_session(request.user, request.auth)),
                        headers={'Cache-Control': 'no-store'})


class SessionActivityView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SessionActivityRateThrottle]

    @object_body
    def post(self, request):
        # Client timestamps/deadlines are deliberately ignored. Call only in
        # response to human interaction, never a background keepalive timer.
        return Response(session_payload(record_activity(request.user, request.auth)),
                        headers={'Cache-Control': 'no-store'})
