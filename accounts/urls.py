from django.urls import path
from accounts.views.email_change_views import EmailChangeView
from accounts.views.session_views import SessionView, SessionActivityView
from accounts.views import (
    ChangePasswordView, AdminResetPasswordView, GetUserView, LogoutView,
    RegisterView, UserListView, UserDetailView, CooldownTokenObtainPairView,
    ForgotPasswordView, PasswordResetConfirmView, PasswordAwareTokenRefreshView,
)

urlpatterns = [
    path('login/', CooldownTokenObtainPairView.as_view(), name='login'),
    path('refresh/', PasswordAwareTokenRefreshView.as_view(), name='token_refresh'),
    path('session/', SessionView.as_view(), name='session'),
    path('session/activity/', SessionActivityView.as_view(), name='session_activity'),
    path('register/', RegisterView.as_view(), name='register'),
    path('user/', GetUserView.as_view(), name='get_user'),
    path('email-change/', EmailChangeView.as_view(), name='email_change'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('admin-reset-password/', AdminResetPasswordView.as_view(), name='admin_reset_password'),
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot_password'),
    path('reset-password/', PasswordResetConfirmView.as_view(), name='reset_password'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('users/', UserListView.as_view(), name='user_list'),
    path('users/<int:pk>/', UserDetailView.as_view(), name='user_detail'),
]
