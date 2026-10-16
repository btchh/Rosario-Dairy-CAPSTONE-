from typing import cast

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin
from accounts.throttles import ExpensiveReadRateThrottle
from config.data_cache import cache_api_response
from .serving import report


class Query(serializers.Serializer):
    period = serializers.ChoiceField(choices=['weekly', 'monthly', 'yearly'], default='monthly')
    scope = serializers.ChoiceField(choices=['real'], default='real')


class ForecastView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self, request):
        query = Query(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = cast(dict[str, str], query.validated_data)
        try:
            return Response(report(scope=values['scope'], period=values['period']))
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=409)
