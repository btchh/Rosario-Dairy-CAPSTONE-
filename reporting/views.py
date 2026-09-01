from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsStaff
from .serializers import REPORT_SERIALIZERS, ReportPreviewSerializer, ReportTypeQuerySerializer
from .services import generate_pdf, get_report, refresh_reports


class ReportTypeMixin:
    def get_report_type(self, request):
        serializer = ReportTypeQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data['type']

    @staticmethod
    def serialized_data(report_type, data):
        return REPORT_SERIALIZERS[report_type](data).data

    @staticmethod
    def staff_scope(request):
        return request.user.role == 'staff'


class ReportPreviewView(ReportTypeMixin, APIView):
    permission_classes = [IsAdmin | IsStaff]

    def get(self, request):
        report_type = self.get_report_type(request)
        payload = {
            'report_type': report_type,
            'generated_at': timezone.now(),
            'data': self.serialized_data(
                report_type,
                get_report(report_type, visible_to_staff=self.staff_scope(request)),
            ),
        }
        return Response(ReportPreviewSerializer(payload).data)


class ReportPDFExportView(ReportTypeMixin, APIView):
    permission_classes = [IsAdmin | IsStaff]

    def get(self, request):
        report_type = self.get_report_type(request)
        data = self.serialized_data(
            report_type,
            get_report(report_type, visible_to_staff=self.staff_scope(request)),
        )
        filename = f'rosario-dairy-{report_type}-{timezone.localdate():%Y%m%d}.pdf'
        return FileResponse(
            generate_pdf(report_type, data), as_attachment=True,
            filename=filename, content_type='application/pdf',
        )


class ReportRefreshView(APIView):
    permission_classes = [IsAdmin | IsStaff]

    def post(self, request):
        generated_at, refreshed = refresh_reports(
            visible_to_staff=request.user.role == 'staff'
        )
        return Response({
            'message': 'Report metrics refreshed successfully.',
            'generated_at': generated_at,
            'report_types': list(refreshed),
        }, status=status.HTTP_200_OK)
