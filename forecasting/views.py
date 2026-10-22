from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsAdmin
from accounts.throttles import ExpensiveReadRateThrottle
from config.data_cache import cache_api_response
from .serving import patterns_report, models_report, report
from .demo import demo_report
from .feeding import feeding_report
from .chart import chart_report


class ChartQuery(serializers.Serializer):
    period = serializers.ChoiceField(choices=['weekly', 'monthly', 'yearly'], default='monthly')
    scope = serializers.ChoiceField(choices=['real'], default='real')


class ForecastChartView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self, request):
        query = ChartQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(chart_report(period=query.validated_data['period']))


class FeedingStudyView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self, request):
        return Response(feeding_report())


class Query(serializers.Serializer):
    period=serializers.ChoiceField(choices=['weekly','monthly','yearly'],default='monthly')
    scope=serializers.ChoiceField(choices=['real'],default='real')


class ForecastView(APIView):
    permission_classes=[IsAdmin]
    throttle_classes=[ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self,request):
        query=Query(data=request.query_params);query.is_valid(raise_exception=True)
        return Response(report(**query.validated_data))


class PatternStudyView(APIView):
    permission_classes=[IsAdmin]
    throttle_classes=[ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self,request):return Response(patterns_report())


class ModelStatusView(APIView):
    permission_classes=[IsAdmin]
    throttle_classes=[ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self,request):return Response(models_report())


class DemoForecastView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    # Bypass cached synthetic payloads after the actual-only policy change.
    def get(self, request):
        return Response(demo_report(), status=410)
