from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsAdmin
from accounts.throttles import ExpensiveReadRateThrottle
from .hierarchy import report


class HierarchyQuery(serializers.Serializer):
    period = serializers.ChoiceField(choices=['daily', 'weekly', 'monthly'], default='weekly')


class HierarchyView(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ExpensiveReadRateThrottle]

    def get(self, request):
        query = HierarchyQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(report(query.validated_data['period']))
