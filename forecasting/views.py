from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsAdmin,IsStaff
from .serving import report

class Query(serializers.Serializer):
    period=serializers.ChoiceField(choices=['weekly','monthly','yearly'],default='monthly')
    scope=serializers.RegexField(regex=r'^(real|(simulation|mixed):[1-9][0-9]*)$',default='real',max_length=80)

class ForecastView(APIView):
    permission_classes=[IsAdmin|IsStaff]
    def get(self,request):
        query=Query(data=request.query_params);query.is_valid(raise_exception=True)
        scope=query.validated_data['scope']
        if scope!='real' and request.user.role!='admin':return Response({'detail':'Admin access required for simulated or mixed data.'},status=403)
        try:return Response(report(**query.validated_data))
        except ValueError as exc:return Response({'detail':str(exc)},status=409)
