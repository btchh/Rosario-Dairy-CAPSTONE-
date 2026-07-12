from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from ..permissions import IsAdmin
from ..models import Transaction
from django.db.models import Sum

class RevenueReportView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        total = Transaction.objects.aggregate(total=Sum('total_amount'))['total'] or 0
        return Response({'total_revenue': total})