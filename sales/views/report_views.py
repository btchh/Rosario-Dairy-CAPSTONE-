from datetime import datetime
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdmin
from ..services import SalesService


def _parse_date_param(value, param_name):
    """Parses a 'YYYY-MM-DD' query param into a date, or None if not supplied.
    Raises ValueError with a friendly message on bad format."""
    if value is None or value == '':
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(f"'{param_name}' must be in YYYY-MM-DD format.")


class RevenueReportView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        period = request.query_params.get('period', 'monthly')

        try:
            start_date = _parse_date_param(request.query_params.get('start_date'), 'start_date')
            end_date = _parse_date_param(request.query_params.get('end_date'), 'end_date')
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        if start_date and end_date and start_date > end_date:
            return Response({'error': "'start_date' must be on or before 'end_date'."}, status=400)

        try:
            data = SalesService.get_revenue_report(period, start_date, end_date)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        return Response([
            {'date': row['bucket'], 'rev': float(row['total'])}
            for row in data
        ])

class BestSellersReportView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        try:
            limit = int(request.query_params.get('limit', 10))
        except ValueError:
            return Response({'error': 'limit must be an integer.'}, status=400)

        if limit <= 0:
            return Response({'error': 'limit must be greater than zero.'}, status=400)

        try:
            start_date = _parse_date_param(request.query_params.get('start_date'), 'start_date')
            end_date = _parse_date_param(request.query_params.get('end_date'), 'end_date')
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        if start_date and end_date and start_date > end_date:
            return Response({'error': "'start_date' must be on or before 'end_date'."}, status=400)

        data = SalesService.get_best_sellers(limit, start_date, end_date)
        return Response([
            {'product': f"{row['product_name']} {row['product_variant'] or ''}".strip(),
             'sales': float(row['total_sold'])}
            for row in data
        ])

class SalesByCategoryReportView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        try:
            start_date = _parse_date_param(request.query_params.get('start_date'), 'start_date')
            end_date = _parse_date_param(request.query_params.get('end_date'), 'end_date')
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        if start_date and end_date and start_date > end_date:
            return Response({'error': "'start_date' must be on or before 'end_date'."}, status=400)

        data = SalesService.get_sales_by_category(start_date, end_date)
        total = sum(float(row['total_sold']) for row in data)
        return Response([
            {'name': row['category_name'],
             'value': round((float(row['total_sold']) / total) * 100, 1) if total else 0}
            for row in data
        ])