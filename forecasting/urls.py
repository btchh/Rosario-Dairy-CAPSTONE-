from django.urls import path
from .views import ForecastView, PatternStudyView, ModelStatusView

urlpatterns=[
    path('forecast/',ForecastView.as_view(),name='forecast-status'),
    path('patterns/',PatternStudyView.as_view(),name='forecast-patterns'),
    path('models/',ModelStatusView.as_view(),name='forecast-models'),
]
