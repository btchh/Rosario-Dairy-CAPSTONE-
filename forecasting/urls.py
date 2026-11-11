from django.urls import path
from .views import ProjectionEvidenceView, ForecastGraphView
from .views import ForecastView, PatternStudyView, ModelStatusView, FeedingStudyView, ForecastChartView, WeeklyModelView, ForecastRefreshView
from .hierarchy_views import HierarchyView
from .analysis_views import CatalogView, AnalysisRunsView, ExpertForecastView

urlpatterns=[
    path('analysis/catalog/', CatalogView.as_view(), name='forecast-analysis-catalog'),
    path('analysis/runs/', AnalysisRunsView.as_view(), name='forecast-analysis-runs'),
    path('analysis/runs/<int:run_id>/', AnalysisRunsView.as_view(), name='forecast-analysis-run'),
    path('analysis/expert/', ExpertForecastView.as_view(), name='forecast-expert'),
    path('analysis/expert/<int:record_id>/', ExpertForecastView.as_view(), name='forecast-expert-record'),
    path('hierarchy/', HierarchyView.as_view(), name='forecast-hierarchy'),
    path('graph/', ForecastGraphView.as_view(), name='forecast-graph'),
    path('evidence/', ProjectionEvidenceView.as_view(), name='projection-evidence'),
    path('refresh/', ForecastRefreshView.as_view(), name='forecast-refresh'),
    path('refresh/<int:job_id>/', ForecastRefreshView.as_view(), name='forecast-refresh-job'),
    path('weekly/',WeeklyModelView.as_view(),name='weekly-model-status'),
    path('chart/',ForecastChartView.as_view(),name='forecast-chart'),
    path('feeding/',FeedingStudyView.as_view(),name='feeding-study'),
    path('forecast/',ForecastView.as_view(),name='forecast-status'),
    path('patterns/',PatternStudyView.as_view(),name='forecast-patterns'),
    path('models/',ModelStatusView.as_view(),name='forecast-models'),
]
