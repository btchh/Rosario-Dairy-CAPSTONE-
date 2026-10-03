from typing import TYPE_CHECKING
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import ProtectedError
from accounts.permissions import IsAdmin, IsStaff

if TYPE_CHECKING:
    from rest_framework.viewsets import GenericViewSet
    _Base = GenericViewSet
else:
    _Base = object


class SoftDeleteMixin(_Base):
    """
    For ViewSets whose queryset is filtered to is_active=True and whose model
    has an `is_active` boolean field. Deletes unused rows permanently and
    deactivates rows protected by related records. Set `model_label` for messages,
    e.g. model_label = "Product".

    list() is active-only by default; pass ?include_inactive=true to see
    everything (needed so the frontend can find and reactivate deactivated
    rows). retrieve/update/reactivate always operate against the full table
    so a deactivated object is still reachable by id.
    """
    model_label = "Item"

    def get_permissions(self):
        if self.action == 'destroy':
            return [IsAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        model = self.queryset.model  # type: ignore[attr-defined]
        qs = model.objects.all()
        if self.action == 'list':
            include_inactive = self.request.query_params.get('include_inactive', 'false').lower() == 'true'
            if not include_inactive:
                qs = qs.filter(is_active=True)
        return qs

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        try:
            obj.delete()
        except ProtectedError:
            obj.is_active = False
            obj.save(update_fields=['is_active', 'updated_at'])
            return Response(
                {'message': f'{self.model_label} deactivated because it has linked records.',
                 'deletion_type': 'deactivated'},
                status=status.HTTP_200_OK,
            )
        return Response(
            {'message': f'{self.model_label} permanently deleted.',
             'deletion_type': 'permanent'},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin | IsStaff])
    def deactivate(self, request, pk=None):
        obj = self.get_object()
        obj.is_active = False
        obj.save(update_fields=['is_active', 'updated_at'])
        return Response({'message': f'{self.model_label} deactivated successfully.'})

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def reactivate(self, request, pk=None):
        model = self.get_queryset().model
        try:
            obj = model.objects.get(pk=pk, is_active=False)
        except model.DoesNotExist:
            return Response(
                {'error': f'{self.model_label} not found or already active.'},
                status=status.HTTP_404_NOT_FOUND
            )
        obj.is_active = True
        obj.save()
        return Response(
            {'message': f'{self.model_label} reactivated successfully.'},
            status=status.HTTP_200_OK
        )


class BatchCreateDestroyMixin(_Base):
    """
    For ProductBatch/IngredientBatch ViewSets: create() returns the fresh
    serialized object (not just validated_data), and destroy() converts a
    ProtectedError (batch has linked transactions/adjustments) into a clean 400.
    """
    protected_error_message = (
        'This batch has linked transactions or adjustments and cannot be '
        'deleted. Adjust its status instead.'
    )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        obj = serializer.save()
        return Response(self.get_serializer(obj).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)  # type: ignore
        except ProtectedError:
            return Response({'error': self.protected_error_message}, status=status.HTTP_400_BAD_REQUEST)
