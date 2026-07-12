from rest_framework.routers import DefaultRouter, path
from .views import CustomerViewSet, OrderViewSet, CheckoutView, RevenueReportView, BestSellersReportView, SalesByCategoryReportView

router = DefaultRouter()
router.register('customers', CustomerViewSet)
router.register('orders', OrderViewSet)
router.register('checkout', CheckoutView, basename='checkout')

urlpatterns = router.urls + [
  path('reports/revenue/', RevenueReportView.as_view(), name='revenue-report'),
  path('reports/best-sellers/', BestSellersReportView.as_view(), name='best-sellers-report'),
  path('reports/sales-by-category/', SalesByCategoryReportView.as_view(), name='sales-by-category-report'),
]