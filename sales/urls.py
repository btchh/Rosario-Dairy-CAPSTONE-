from rest_framework.routers import DefaultRouter
from .views import CustomerViewSet, OrderViewSet, CheckoutView

router = DefaultRouter()
router.register('customers', CustomerViewSet)
router.register('orders', OrderViewSet)
router.register('checkout', CheckoutView, basename='checkout')

urlpatterns = router.urls