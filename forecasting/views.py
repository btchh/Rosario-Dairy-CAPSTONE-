from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin,IsStaff
from .serving import report
from config.data_cache import cache_api_response
from accounts.throttles import ExpensiveReadRateThrottle

class Query(serializers.Serializer):
    period=serializers.ChoiceField(choices=['weekly','monthly','yearly'],default='monthly')
    scope=serializers.ChoiceField(choices=['real'],default='real')

class ForecastView(APIView):
    permission_classes=[IsAdmin]
    throttle_classes=[ExpensiveReadRateThrottle]
    @cache_api_response
    def get(self,request):
        query=Query(data=request.query_params);query.is_valid(raise_exception=True)
        scope=query.validated_data['scope']
        try:return Response(report(**query.validated_data))
        except ValueError as exc:return Response({'detail':str(exc)},status=409)
