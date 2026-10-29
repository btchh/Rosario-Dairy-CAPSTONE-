from collections.abc import Mapping
import logging

from django.core.cache import cache
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import Throttled, ValidationError

from accounts.permissions import IsAdmin, IsStaff
from config.data_cache import invalidate_data_cache
from .serializers import REPORT_SERIALIZERS, ReportPreviewSerializer, ReportTypeQuerySerializer
from .services import generate_pdf, get_report, refresh_reports, report_refresh_slot
from accounts.throttles import ReportExportRateThrottle, ReportPreviewRateThrottle, ReportRefreshRateThrottle


logger = logging.getLogger(__name__)
LAST_REFRESH_KEY = 'reporting:refresh:last-success:v1'


class ReportTypeMixin:
    def report_options(self, request):
        serializer = ReportTypeQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    @staticmethod
    def serialized_data(report_type, data):
        return REPORT_SERIALIZERS[report_type](data).data

    @staticmethod
    def staff_scope(request):
        return request.user.role == 'staff'


class ReportPreviewView(ReportTypeMixin, APIView):
    permission_classes = [IsAdmin | IsStaff]
    throttle_classes = [ReportPreviewRateThrottle]

    def get(self, request):
        options = self.report_options(request)
        report_type = options['type']
        payload = {
            'report_type': report_type,
            'generated_at': timezone.now(),
            'data': self.serialized_data(
                report_type,
                get_report(report_type, visible_to_staff=self.staff_scope(request),
                       period=options.get('period', 'monthly'),
                       start_date=options.get('start_date'), end_date=options.get('end_date')),
            ),
        }
        return Response(ReportPreviewSerializer(payload).data)


class ReportPDFExportView(ReportTypeMixin, APIView):
    permission_classes = [IsAdmin | IsStaff]
    throttle_classes = [ReportExportRateThrottle]

    def get(self, request):
        options = self.report_options(request)
        report_type = options['type']
        data = self.serialized_data(
            report_type,
            get_report(report_type, visible_to_staff=self.staff_scope(request),
                       period=options.get('period', 'monthly'),
                       start_date=options.get('start_date'), end_date=options.get('end_date')),
        )
        date_label = (f"{data['start_date']}-to-{data['end_date']}"
                      if 'start_date' in data else f'{timezone.localdate():%Y%m%d}')
        filename = f'rosario-dairy-{report_type}-{date_label}.pdf'
        return FileResponse(
            generate_pdf(report_type, data), as_attachment=True,
            filename=filename, content_type='application/pdf',
        )


class ReportRefreshView(APIView):
    permission_classes = [IsAdmin | IsStaff]
    throttle_classes = [ReportRefreshRateThrottle]

    def check_throttles(self, request):
        # A duplicate refresh is an accepted no-op. Cache availability errors
        # still propagate as 503, and the global rebuild lock remains in force.
        if request.method != 'POST':
            return
        if not isinstance(request.data, Mapping) or request.data:
            raise ValidationError('Report refresh accepts an empty JSON object only.')
        self.refresh_wait = None
        try:
            super().check_throttles(request)
        except Throttled as exc:
            self.refresh_wait = exc.wait

    @staticmethod
    def coalesced(wait):
        try:
            previous = cache.get(LAST_REFRESH_KEY)
        except Exception:
            logger.exception('Unable to read last report refresh metadata')
            previous = None
        return Response({
            'message': 'A full refresh was already requested. Current report previews and downloads remain available.',
            'generated_at': previous.get('generated_at') if isinstance(previous, dict) else None,
            'report_types': [],
            'refresh_status': 'coalesced',
            'retry_after_seconds': wait,
        }, status=status.HTTP_200_OK)

    def post(self, request):
        if self.refresh_wait is not None:
            return self.coalesced(self.refresh_wait)
        visible_to_staff = request.user.role == 'staff'
        try:
            with report_refresh_slot():
                invalidate_data_cache()
                generated_at, refreshed = refresh_reports(visible_to_staff=visible_to_staff)
                if not visible_to_staff:
                    refresh_reports(visible_to_staff=True)
                try:
                    cache.set(LAST_REFRESH_KEY, {'generated_at': generated_at.isoformat()}, timeout=300)
                except Exception:
                    # Metadata failure must not report a completed rebuild as failed.
                    logger.exception('Unable to store last report refresh metadata')
        except Throttled as exc:
            return self.coalesced(exc.wait)
        return Response({
            'message': 'Report metrics refreshed successfully.',
            'generated_at': generated_at,
            'report_types': list(refreshed),
            'refresh_status': 'refreshed',
        }, status=status.HTTP_200_OK)
