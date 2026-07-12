from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdmin
from ..services import get_revenue_report
from ..services import get_best_sellers
from ..services import get_sales_by_category

class RevenueReportView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        period = request.query_params.get('period', 'monthly')
        try:
            data = get_revenue_report(period)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        return Response([
            {'date': row['bucket'], 'rev': float(row['total'])}
            for row in data
        ])

class BestSellersReportView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        limit = int(request.query_params.get('limit', 10))
        data = get_best_sellers(limit)
        return Response([
            {'product': f"{row['product_name']} {row['product_variant'] or ''}".strip(),
             'sales': float(row['total_sold'])}
            for row in data
        ])

class SalesByCategoryReportView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        data = get_sales_by_category()
        total = sum(float(row['total_sold']) for row in data)
        return Response([
            {'name': row['category_name'],
             'value': round((float(row['total_sold']) / total) * 100, 1) if total else 0}
            for row in data
        ])