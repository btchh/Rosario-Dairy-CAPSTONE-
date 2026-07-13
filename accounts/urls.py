from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from accounts.views import ChangePasswordView, AdminResetPasswordView, GetUserView, LogoutView, RegisterView, UserListView

urlpatterns = [
    path('login/', TokenObtainPairView.as_view(), name='login'),
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('register/', RegisterView.as_view(), name='register'),
    path('user/', GetUserView.as_view(), name='get_user'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('admin-reset-password/', AdminResetPasswordView.as_view(), name='admin_reset_password'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('users/', UserListView.as_view(), name='user_list'),
]

