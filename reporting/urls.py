from django.urls import path

from .views import ReportPDFExportView, ReportPreviewView, ReportRefreshView


app_name = 'reporting'

urlpatterns = [
    path('preview/', ReportPreviewView.as_view(), name='preview'),
    path('export-pdf/', ReportPDFExportView.as_view(), name='export-pdf'),
    path('refresh/', ReportRefreshView.as_view(), name='refresh'),
]
