from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsAdmin, IsStaff
from systemsetting.models import NotificationState
from systemsetting.notifications import live_notifications, notification_feed


class NotificationReference(serializers.Serializer):
    id = serializers.CharField(max_length=100)
    revision = serializers.RegexField(r'^[0-9a-f]{64}$')


class NotificationAction(serializers.Serializer):
    action = serializers.ChoiceField(choices=['read', 'dismiss', 'restore'])
    notifications = NotificationReference(many=True, allow_empty=False, max_length=1000)


class NotificationInboxView(APIView):
    permission_classes = [IsAdmin | IsStaff]

    def get(self, request):
        return Response(notification_feed(request.user))

    def post(self, request):
        serializer = NotificationAction(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested = serializer.validated_data['notifications']
        valid = {(n['id'], n['revision']) for n in live_notifications(request.user)}
        keys = {(n['id'], n['revision']) for n in requested}
        if not keys.issubset(valid):
            return Response({'detail': 'Some alerts have changed or are no longer available. Refresh and try again.'}, status=409)
        action = serializer.validated_data['action']
        now = timezone.now()
        with transaction.atomic():
            for key, revision in sorted(keys):
                state, _ = NotificationState.objects.select_for_update().get_or_create(
                    user=request.user, notification_key=key, revision=revision)
                if action in ('read', 'dismiss'):
                    state.read_at = state.read_at or now
                if action == 'dismiss':
                    state.dismissed_at = state.dismissed_at or now
                elif action == 'restore':
                    state.dismissed_at = None
                state.save(update_fields=['read_at', 'dismissed_at', 'updated_at'])
        return Response({'updated': len(keys), 'action': action})
