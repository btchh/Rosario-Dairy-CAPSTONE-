from rest_framework import serializers


class DailySalesItemSerializer(serializers.Serializer):
    product_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    date = serializers.DateField()


class DailySalesSerializer(serializers.Serializer):
    date = serializers.DateField()
    total_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    transaction_count = serializers.IntegerField()
    items = DailySalesItemSerializer(many=True)


class PeriodSalesSerializer(serializers.Serializer):
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    transaction_count = serializers.IntegerField()
    previous_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    growth_rate = serializers.FloatField(allow_null=True)


class StockItemSerializer(serializers.Serializer):
    item_type = serializers.ChoiceField(choices=['product', 'ingredient'])
    id = serializers.IntegerField()
    name = serializers.CharField()
    unit = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2)
    low_stock_threshold = serializers.DecimalField(max_digits=14, decimal_places=2)
    is_low_stock = serializers.BooleanField()
    next_expiration_date = serializers.DateField(allow_null=True)
    fefo_status = serializers.ChoiceField(
        choices=['no_stock', 'expired', 'expiring_soon', 'healthy']
    )


class InventoryReportSerializer(serializers.Serializer):
    as_of = serializers.DateTimeField()
    total_products = serializers.IntegerField()
    total_ingredients = serializers.IntegerField()
    low_stock_count = serializers.IntegerField()
    expired_batch_count = serializers.IntegerField()
    expiring_soon_batch_count = serializers.IntegerField()
    items = StockItemSerializer(many=True)


class ForecastPointSerializer(serializers.Serializer):
    date = serializers.DateField()
    predicted_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    lower_bound = serializers.DecimalField(max_digits=14, decimal_places=2)
    upper_bound = serializers.DecimalField(max_digits=14, decimal_places=2)


class ForecastReportSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    horizon_days = serializers.IntegerField()
    method = serializers.CharField()
    is_placeholder = serializers.BooleanField()
    forecast = ForecastPointSerializer(many=True)


class CustomerReportSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    total_customers = serializers.IntegerField()
    active_customer_count = serializers.IntegerField()
    customers_with_purchases = serializers.IntegerField()
    average_lifetime_value = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_lifetime_value = serializers.DecimalField(max_digits=14, decimal_places=2)


REPORT_SERIALIZERS = {
    'daily_sales': DailySalesSerializer,
    'weekly_sales': PeriodSalesSerializer,
    'monthly_sales': PeriodSalesSerializer,
    'inventory': InventoryReportSerializer,
    'sarima_forecast': ForecastReportSerializer,
    'customer': CustomerReportSerializer,
}


class ReportPreviewSerializer(serializers.Serializer):
    report_type = serializers.ChoiceField(choices=list(REPORT_SERIALIZERS))
    generated_at = serializers.DateTimeField()
    data = serializers.JSONField()

    def validate(self, attrs):
        serializer_class = REPORT_SERIALIZERS[attrs['report_type']]
        serializer_class(data=attrs['data']).is_valid(raise_exception=True)
        return attrs


class ReportTypeQuerySerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=list(REPORT_SERIALIZERS))
