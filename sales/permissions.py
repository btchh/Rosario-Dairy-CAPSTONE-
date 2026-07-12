from rest_framework import permissions


class IsAdminOrReadOnlyCancel(permissions.BasePermission):
    """
    Staff can cancel an order in any non-terminal state (placed/confirmed).
    Only admin can cancel/void an order that's already fulfilled —
    that's voiding a real, completed sale, not just calling off a pending one.
    """
    def has_object_permission(self, request, view, obj):
        if obj.status == 'fulfilled':
            return request.user.role == 'admin'
        return True


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'