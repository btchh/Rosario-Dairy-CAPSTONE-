from django.urls import path
from .views import ForecastView, PatternStudyView, ModelStatusView, DemoForecastView, FeedingStudyView, ForecastChartView, WeeklyModelView, ForecastRefreshView

urlpatterns=[
    path('refresh/', ForecastRefreshView.as_view(), name='forecast-refresh'),
    path('refresh/<int:job_id>/', ForecastRefreshView.as_view(), name='forecast-refresh-job'),
    path('weekly/',WeeklyModelView.as_view(),name='weekly-model-status'),
    path('chart/',ForecastChartView.as_view(),name='forecast-chart'),
    path('feeding/',FeedingStudyView.as_view(),name='feeding-study'),
    path('demo-2026/',DemoForecastView.as_view(),name='demo-2026-forecasts'),
    path('forecast/',ForecastView.as_view(),name='forecast-status'),
    path('patterns/',PatternStudyView.as_view(),name='forecast-patterns'),
    path('models/',ModelStatusView.as_view(),name='forecast-models'),
]
