from collections.abc import Mapping
from django.conf import settings

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsAdmin
from accounts.throttles import ExpensiveReadRateThrottle, ForecastRefreshRateThrottle
from rest_framework.exceptions import NotFound
from config.data_cache import cache_api_response
from .serving import patterns_report, models_report, report
from .feeding import feeding_report
from .chart import chart_report
from .weekly import weekly_report
from .refresh_jobs import enqueue, launch_after_commit, lookup, payload
from .prospective import evidence_report
from .graph_forecasts import report as forecast_graph_report
from .graph_range import range_report, calendar
from django.utils import timezone


class ProjectionEvidenceView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    def get(self, request):
        return Response(evidence_report())


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
        if not isinstance(request.data, Mapping) or request.data:
            raise serializers.ValidationError('Refresh accepts an empty JSON object only; actual completed-period data is selected by the backend.')
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


class ForecastQuery(serializers.Serializer):
    period = serializers.ChoiceField(choices=['weekly', 'monthly', 'yearly'], default='monthly')
    scope = serializers.ChoiceField(choices=['real'], default='real')


class ForecastGraphQuery(serializers.Serializer):
    period = serializers.ChoiceField(choices=['weekly', 'monthly', 'yearly'], default='monthly')
    scope = serializers.ChoiceField(choices=['real', 'demo2026'], default='real')
    as_of = serializers.DateField(default=timezone.localdate)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs):
        if 'start_date' in attrs or 'end_date' in attrs:
            if not all(key in attrs for key in ('start_date', 'end_date')):
                raise serializers.ValidationError('Choose both start_date and end_date.')
            if attrs['scope'] != 'real' or 'as_of' in self.initial_data:
                raise serializers.ValidationError('Custom display dates require real scope without as_of.')
            if not all(3 <= attrs[key].year <= 9997 for key in ('start_date', 'end_date')):
                raise serializers.ValidationError('Dates are outside the supported calendar.')
            if attrs['end_date'] < attrs['start_date']:
                raise serializers.ValidationError('end_date must be on or after start_date.')
            try:
                calendar(attrs['period'], attrs['start_date'], attrs['end_date'])
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
            attrs.pop('as_of')
            return attrs
        if attrs['scope'] == 'demo2026' and settings.IS_PRODUCTION:
            raise serializers.ValidationError({'scope': 'Demo forecasting is unavailable in production.'})
        if not 3 <= attrs['as_of'].year <= 9997:
            raise serializers.ValidationError('as_of is outside the supported calendar.')
        if attrs['scope'] == 'real' and attrs['as_of'] > timezone.localdate():
            raise serializers.ValidationError('Real sales cannot use a future as_of date.')
        if attrs['scope'] == 'demo2026' and attrs['as_of'].year < 2026:
            raise serializers.ValidationError('The demo starts in 2026.')
        return attrs


class ForecastGraphView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    def get(self, request):
        query = ForecastGraphQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        handler = range_report if 'start_date' in query.validated_data else forecast_graph_report
        return Response(handler(**query.validated_data))


class ForecastChartView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self, request):
        query = ForecastQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(chart_report(period=query.validated_data['period']))


class FeedingStudyView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self, request):
        return Response(feeding_report())


class ForecastView(APIView):
    permission_classes=[IsAdmin]
    throttle_classes=[ExpensiveReadRateThrottle]

    @cache_api_response
    def get(self,request):
        query=ForecastQuery(data=request.query_params);query.is_valid(raise_exception=True)
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
