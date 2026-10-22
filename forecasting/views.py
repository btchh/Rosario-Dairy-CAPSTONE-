from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsAdmin
from accounts.throttles import ExpensiveReadRateThrottle, ForecastRefreshRateThrottle
from rest_framework.exceptions import NotFound
from config.data_cache import cache_api_response
from .serving import patterns_report, models_report, report
from .demo import demo_report
from .feeding import feeding_report
from .chart import chart_report
from .weekly import weekly_report
from .refresh_jobs import enqueue, launch_after_commit, lookup, payload


class ForecastRefreshView(APIView):
    permission_classes = [IsAdmin]

    def get_throttles(self):
        return [ForecastRefreshRateThrottle() if self.request.method == 'POST' else ExpensiveReadRateThrottle()]

    def get(self, request, job_id=None):
        job = lookup(job_id)
        if job_id is not None and job is None:
            raise NotFound('Forecast refresh job not found.')
        return Response(payload(job))

    def post(self, request, job_id=None):
        if job_id is not None:
            return Response({'detail': 'Start refreshes at /api/forecasting/refresh/.'}, status=405)
        if request.data:
            raise serializers.ValidationError('Refresh accepts an empty body only; actual completed-period data is selected by the backend.')
        job, created = enqueue(request.user)
        if job is None:
            return Response({'status': 'busy', 'message': 'Forecast maintenance is starting. Check refresh status shortly.'}, status=409)
        if created:
            launch_after_commit(job)
            job.refresh_from_db()
        response = Response({**payload(job), 'already_running': not created}, status=202)
        response['Location'] = f'/api/forecasting/refresh/{job.pk}/'
        return response


class WeeklyModelView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self, request):
        return Response(weekly_report())


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
