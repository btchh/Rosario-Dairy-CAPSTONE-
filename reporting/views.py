from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsStaff
from config.data_cache import invalidate_data_cache
from .serializers import REPORT_SERIALIZERS, ReportPreviewSerializer, ReportTypeQuerySerializer
from .services import generate_pdf, get_report, refresh_reports, report_refresh_slot
from accounts.throttles import ExpensiveReadRateThrottle, ReportRefreshRateThrottle


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
    throttle_classes = [ExpensiveReadRateThrottle]

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
    throttle_classes = [ExpensiveReadRateThrottle]

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

    def post(self, request):
        visible_to_staff = request.user.role == 'staff'
        with report_refresh_slot():
            invalidate_data_cache()
            generated_at, refreshed = refresh_reports(visible_to_staff=visible_to_staff)
            if not visible_to_staff:
                refresh_reports(visible_to_staff=True)
        return Response({
            'message': 'Report metrics refreshed successfully.',
            'generated_at': generated_at,
            'report_types': list(refreshed),
        }, status=status.HTTP_200_OK)
