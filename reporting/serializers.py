from rest_framework import serializers


class DailySalesItemSerializer(serializers.Serializer):
    product_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=24, decimal_places=2)
    total_revenue = serializers.DecimalField(max_digits=24, decimal_places=2)


class PaymentMixSerializer(serializers.Serializer):
    payment_method = serializers.CharField()
    transaction_count = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=24, decimal_places=2)


class CategoryMixSerializer(serializers.Serializer):
    category = serializers.CharField(allow_null=True)
    quantity = serializers.DecimalField(max_digits=24, decimal_places=2)
    gross_sales = serializers.DecimalField(max_digits=24, decimal_places=2)


class FamilyMixSerializer(serializers.Serializer):
    product_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=24, decimal_places=2)
    gross_sales = serializers.DecimalField(max_digits=24, decimal_places=2)


class SalesDriversSerializer(serializers.Serializer):
    gross_sales = serializers.DecimalField(max_digits=24, decimal_places=2)
    discounts = serializers.DecimalField(max_digits=24, decimal_places=2)
    average_ticket = serializers.DecimalField(max_digits=24, decimal_places=2)
    payment_mix = PaymentMixSerializer(many=True)
    category_mix = CategoryMixSerializer(many=True)
    family_mix = FamilyMixSerializer(many=True)


class DailySalesSerializer(SalesDriversSerializer):
    product_revenue_basis = serializers.CharField(required=False)
    date = serializers.DateField()
    total_revenue = serializers.DecimalField(max_digits=24, decimal_places=2)
    transaction_count = serializers.IntegerField()
    previous_revenue = serializers.DecimalField(max_digits=24, decimal_places=2)
    previous_transaction_count = serializers.IntegerField()
    growth_rate = serializers.FloatField(allow_null=True)
    items = DailySalesItemSerializer(many=True)


class DailyBreakdownItemSerializer(serializers.Serializer):
    date = serializers.DateField()
    transaction_count = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=24, decimal_places=2)


class TopProductItemSerializer(serializers.Serializer):
    product_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=24, decimal_places=2)
    revenue = serializers.DecimalField(max_digits=24, decimal_places=2)


class WeeklyBreakdownItemSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    week_end = serializers.DateField()
    transaction_count = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=24, decimal_places=2)


class WeeklySalesSerializer(SalesDriversSerializer):
    product_revenue_basis = serializers.CharField(required=False)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    revenue = serializers.DecimalField(max_digits=24, decimal_places=2)
    transaction_count = serializers.IntegerField()
    previous_revenue = serializers.DecimalField(max_digits=24, decimal_places=2)
    previous_transaction_count = serializers.IntegerField()
    growth_rate = serializers.FloatField(allow_null=True)
    daily_breakdown = DailyBreakdownItemSerializer(many=True)
    top_products = TopProductItemSerializer(many=True)


class MonthlySalesSerializer(SalesDriversSerializer):
    product_revenue_basis = serializers.CharField(required=False)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    revenue = serializers.DecimalField(max_digits=24, decimal_places=2)
    transaction_count = serializers.IntegerField()
    previous_revenue = serializers.DecimalField(max_digits=24, decimal_places=2)
    previous_transaction_count = serializers.IntegerField()
    previous_period_start = serializers.DateField()
    previous_period_end = serializers.DateField()
    growth_rate = serializers.FloatField(allow_null=True)
    weekly_breakdown = WeeklyBreakdownItemSerializer(many=True)
    top_products = TopProductItemSerializer(many=True)


class StockItemSerializer(serializers.Serializer):
    item_type = serializers.ChoiceField(choices=['product', 'ingredient'])
    id = serializers.IntegerField()
    name = serializers.CharField()
    unit = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=24, decimal_places=2)
    low_stock_threshold = serializers.DecimalField(max_digits=24, decimal_places=2)
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
    status_counts = serializers.DictField(child=serializers.IntegerField())


class ForecastPointSerializer(serializers.Serializer):
    date = serializers.DateField()
    predicted_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    lower_bound = serializers.DecimalField(max_digits=14, decimal_places=2)
    upper_bound = serializers.DecimalField(max_digits=14, decimal_places=2)
    end_date = serializers.DateField(required=False)
    trained_through = serializers.DateField(required=False)
    interval_nominal_percent = serializers.IntegerField(required=False)
    range_kind = serializers.CharField(required=False)
    point_kind = serializers.CharField(required=False)


class ForecastReportSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    data_end = serializers.DateField(required=False)
    horizon_days = serializers.IntegerField()
    method = serializers.CharField()
    is_placeholder = serializers.BooleanField()
    forecast = ForecastPointSerializer(many=True)
    status = serializers.CharField(required=False)
    period = serializers.CharField(required=False)
    scope = serializers.CharField(required=False)
    accuracy_target_percent = serializers.FloatField(required=False)
    accuracy_metric = serializers.CharField(required=False)
    quality_status = serializers.CharField(required=False)
    available_periods = serializers.ListField(child=serializers.CharField(),required=False)
    regular = serializers.DictField(required=False)
    bulk = serializers.DictField(required=False)
    combined = serializers.DictField(required=False)
    cutoff = serializers.DictField(required=False)
    metrics = serializers.DictField(required=False)
    baselines = serializers.DictField(required=False)
    historical_comparison = serializers.ListField(child=serializers.DictField(),required=False)
    fixed_origin_comparison = serializers.ListField(child=serializers.DictField(),required=False)
    fixed_origin_metrics = serializers.DictField(required=False,allow_null=True)
    component_metrics = serializers.DictField(required=False)
    component_comparison = serializers.DictField(required=False)
    evaluation_details = serializers.DictField(required=False)
    data_provenance = serializers.DictField(required=False)
    limitations = serializers.CharField(required=False)
    warnings = serializers.ListField(child=serializers.CharField(),required=False)


class CustomerTopItemSerializer(serializers.Serializer):
    customer_name = serializers.CharField()
    transaction_count = serializers.IntegerField()
    total_spent = serializers.DecimalField(max_digits=24, decimal_places=2)


class CustomerReportSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    total_customers = serializers.IntegerField()
    active_customer_count = serializers.IntegerField()
    customers_with_purchases = serializers.IntegerField()
    average_lifetime_value = serializers.DecimalField(max_digits=24, decimal_places=2)
    total_lifetime_value = serializers.DecimalField(max_digits=24, decimal_places=2)
    top_customers = CustomerTopItemSerializer(many=True)
    repeat_customer_count = serializers.IntegerField()
    unassigned_transaction_count = serializers.IntegerField()
    unassigned_revenue = serializers.DecimalField(max_digits=24, decimal_places=2)


REPORT_SERIALIZERS = {
    'daily_sales': DailySalesSerializer,
    'weekly_sales': WeeklySalesSerializer,
    'monthly_sales': MonthlySalesSerializer,
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
    period = serializers.ChoiceField(choices=['weekly','monthly','yearly'],required=False)
