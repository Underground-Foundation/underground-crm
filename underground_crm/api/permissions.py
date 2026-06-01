from rest_framework.permissions import BasePermission


class IsCRMStaff(BasePermission):
    """
    Allows access only to authenticated CRM staff/admin users.

    This serves as a centralized hook for future Role-Based Access Control (RBAC)
    checks on administrative endpoints.
    """

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)
