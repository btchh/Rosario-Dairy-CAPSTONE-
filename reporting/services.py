from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.core.cache import cache
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Min, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from inventory.models import Ingredient, IngredientBatch, Product, ProductBatch
from sales.models import Customer, Transaction, TransactionItem


REPORT_TYPES = (
    'daily_sales', 'weekly_sales', 'monthly_sales', 'inventory',
    'sarima_forecast', 'customer',
)
CACHE_PREFIX = 'reporting:preview:v2:'
CACHE_TIMEOUT = 300
PDF_PRIMARY = colors.HexColor('#1E3A8A')
PDF_PRIMARY_DARK = colors.HexColor('#172554')
PDF_ROW_ALT = colors.HexColor('#F3F4F6')
PDF_BORDER = colors.HexColor('#CBD5E1')
PDF_TEXT = colors.HexColor('#111827')
STATUS_COLORS = {
    'expired': '#DC2626',
    'expiring_soon': '#D97706',
    'no_stock': '#9CA3AF',
    'healthy': '#111827',
}
summary_value_style = ParagraphStyle(
    'SummaryValue', textColor=PDF_PRIMARY_DARK,
    fontName='Helvetica-Bold', fontSize=15, leading=18,
)


class NumberedCanvas(canvas.Canvas):
    """Defers page drawing so the footer can display Page X of Y."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(page_count)
            super().showPage()
        super().save()

    def _draw_footer(self, page_count):
        page_width, _ = landscape(A4)
        self.saveState()
        self.setStrokeColor(PDF_BORDER)
        self.setLineWidth(0.5)
        self.line(0.6 * inch, 0.48 * inch, page_width - 0.6 * inch, 0.48 * inch)
        self.setFillColor(colors.HexColor('#64748B'))
        self.setFont('Helvetica', 8)
        self.drawString(0.6 * inch, 0.3 * inch, 'Rosario Dairy System - Confidential')
        self.drawRightString(
            page_width - 0.6 * inch, 0.3 * inch,
            f'Page {self._pageNumber} of {page_count}',
        )
        self.restoreState()


def _money(value):
    return value or Decimal('0.00')


def _growth_rate(current, previous):
    current, previous = _money(current), _money(previous)
    if previous == 0:
        return None if current == 0 else 100.0
    return round(float(((current - previous) / previous) * 100), 2)


def _sales_summary(start_date, end_date):
    values = Transaction.objects.filter(
        is_voided=False,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).aggregate(revenue=Sum('total_amount'), transaction_count=Count('id'))
    return _money(values['revenue']), values['transaction_count']


def daily_sales():
    today = timezone.localdate()
    revenue, count = _sales_summary(today, today)
    line_total = ExpressionWrapper(
        F('quantity') * F('unit_price'),
        output_field=DecimalField(max_digits=20, decimal_places=2),
    )
    item_rows = (
        TransactionItem.objects.filter(
            transaction__is_voided=False,
            transaction__created_at__date=today,
        )
        .values(product_name=F('product_batch__product__name'))
        .annotate(
            sold_quantity=Sum('quantity'),
            total_revenue=Sum(line_total),
        )
        .order_by('-total_revenue', 'product_name')
    )
    items = [
        {
            'product_name': row['product_name'],
            'quantity': _money(row['sold_quantity']),
            'total_revenue': _money(row['total_revenue']),
        }
        for row in item_rows
    ]
    return {
        'date': today,
        'total_revenue': revenue,
        'transaction_count': count,
        'items': items,
    }


def _line_revenue():
    return ExpressionWrapper(
        F('quantity') * F('unit_price'),
        output_field=DecimalField(max_digits=20, decimal_places=2),
    )


def _period_items(start_date, end_date):
    return TransactionItem.objects.filter(
        transaction__is_voided=False,
        transaction__created_at__date__gte=start_date,
        transaction__created_at__date__lte=end_date,
    )


def _daily_sales_breakdown(start_date, end_date):
    period_items = TransactionItem.objects.filter(
        transaction__is_voided=False,
        transaction__created_at__date__gte=start_date,
        transaction__created_at__date__lte=end_date,
    )
    daily_rows = (
        period_items
        .annotate(day=TruncDate('transaction__created_at'))
        .values('day')
        .annotate(
            transaction_count=Count('transaction_id', distinct=True),
            revenue=Sum(_line_revenue()),
        )
        .order_by('day')
    )
    daily_by_date = {row['day']: row for row in daily_rows}
    daily_breakdown = []
    date = start_date
    while date <= end_date:
        row = daily_by_date.get(date)
        daily_breakdown.append({
            'date': date,
            'transaction_count': row['transaction_count'] if row else 0,
            'revenue': _money(row['revenue']) if row else Decimal('0.00'),
        })
        date += timedelta(days=1)
    return daily_breakdown


def _top_products(start_date, end_date):
    product_rows = (
        _period_items(start_date, end_date)
        .values(product_name=F('product_batch__product__name'))
        .annotate(revenue=Sum(_line_revenue()), quantity=Sum('quantity'))
        .order_by('-revenue')[:10]
    )
    return [{
        'product_name': row['product_name'],
        'quantity': _money(row['quantity']),
        'revenue': _money(row['revenue']),
    } for row in product_rows]


def weekly_sales():
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=6)
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)
    revenue, count = _sales_summary(start_date, end_date)
    previous_revenue, _ = _sales_summary(previous_start, previous_end)
    daily_breakdown = _daily_sales_breakdown(start_date, end_date)
    top_products = _top_products(start_date, end_date)
    return {
        'start_date': start_date, 'end_date': end_date, 'revenue': revenue,
        'transaction_count': count, 'previous_revenue': previous_revenue,
        'growth_rate': _growth_rate(revenue, previous_revenue),
        'daily_breakdown': daily_breakdown, 'top_products': top_products,
    }


def monthly_sales():
    today = timezone.localdate()
    start_date = today.replace(day=1)
    end_date = today
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    revenue, count = _sales_summary(start_date, end_date)
    previous_revenue, _ = _sales_summary(previous_start, previous_end)
    week_starts = []
    cursor = start_date
    while cursor <= end_date:
        week_start = cursor - timedelta(days=cursor.weekday())
        if not week_starts or week_starts[-1] != week_start:
            week_starts.append(week_start)
        cursor += timedelta(days=1)
    weekly_breakdown = []
    for week_start in week_starts:
        bucket_start = max(week_start, start_date)
        bucket_end = min(week_start + timedelta(days=6), end_date)
        bucket_revenue, bucket_count = _sales_summary(bucket_start, bucket_end)
        weekly_breakdown.append({
            'week_start': bucket_start,
            'week_end': bucket_end,
            'transaction_count': bucket_count,
            'revenue': bucket_revenue,
        })
    top_products = _top_products(start_date, end_date)
    return {
        'start_date': start_date, 'end_date': end_date, 'revenue': revenue,
        'transaction_count': count, 'previous_revenue': previous_revenue,
        'growth_rate': _growth_rate(revenue, previous_revenue),
        'weekly_breakdown': weekly_breakdown, 'top_products': top_products,
    }


def _inventory_rows(model, relation_name, item_type, visible_to_staff=False):
    today = timezone.localdate()
    soon = today + timedelta(days=7)
    rows = model.objects.filter(is_active=True)
    if visible_to_staff and model is Product:
        rows = rows.filter(category__is_visible_to_staff=True)
    rows = rows.annotate(
        stock=Sum(
            f'{relation_name}__remaining_quantity',
            filter=Q(**{f'{relation_name}__status': 'available'}),
        ),
        next_expiration=Min(
            f'{relation_name}__expiration_date',
            filter=Q(**{f'{relation_name}__status': 'available'}),
        ),
    )
    result = []
    for item in rows:
        quantity = _money(item.stock)
        next_expiration = item.next_expiration
        if quantity <= 0 or next_expiration is None:
            fefo_status = 'no_stock'
        elif next_expiration < today:
            fefo_status = 'expired'
        elif next_expiration <= soon:
            fefo_status = 'expiring_soon'
        else:
            fefo_status = 'healthy'
        result.append({
            'item_type': item_type, 'id': item.pk, 'name': item.name,
            'unit': item.unit, 'quantity': quantity,
            'low_stock_threshold': Decimal(str(item.low_stock_threshold)),
            'is_low_stock': quantity <= item.low_stock_threshold,
            'next_expiration_date': next_expiration, 'fefo_status': fefo_status,
        })
    return result


def inventory_status(visible_to_staff=False):
    today = timezone.localdate()
    soon = today + timedelta(days=7)
    items = _inventory_rows(Product, 'batches', 'product', visible_to_staff)
    items += _inventory_rows(Ingredient, 'batches', 'ingredient')
    available_batches = Q(status='available', remaining_quantity__gt=0)
    product_batches = ProductBatch.objects.all()
    products = Product.objects.filter(is_active=True)
    if visible_to_staff:
        product_batches = product_batches.filter(product__category__is_visible_to_staff=True)
        products = products.filter(category__is_visible_to_staff=True)
    expired = (
        product_batches.filter(available_batches, expiration_date__lt=today).count()
        + IngredientBatch.objects.filter(available_batches, expiration_date__lt=today).count()
    )
    expiring = (
        product_batches.filter(available_batches, expiration_date__range=(today, soon)).count()
        + IngredientBatch.objects.filter(available_batches, expiration_date__range=(today, soon)).count()
    )
    return {
        'as_of': timezone.now(),
        'total_products': products.count(),
        'total_ingredients': Ingredient.objects.filter(is_active=True).count(),
        'low_stock_count': sum(item['is_low_stock'] for item in items),
        'expired_batch_count': expired,
        'expiring_soon_batch_count': expiring,
        'items': sorted(items, key=lambda item: (item['fefo_status'], item['name'].lower())),
    }


def sarima_forecast():
    """Thirty-day weekday-seasonal baseline; replace with a fitted SARIMA provider."""
    today = timezone.localdate()
    history_start = today - timedelta(days=84)
    sales = Transaction.objects.filter(
        is_voided=False, created_at__date__gte=history_start,
        created_at__date__lt=today,
    ).annotate(day=TruncDate('created_at')).values('day').annotate(
        revenue=Sum('total_amount')
    )
    daily = {row['day']: _money(row['revenue']) for row in sales}
    weekday_values = defaultdict(list)
    cursor = history_start
    while cursor < today:
        weekday_values[cursor.weekday()].append(daily.get(cursor, Decimal('0.00')))
        cursor += timedelta(days=1)
    all_values = list(daily.values()) or [Decimal('0.00')]
    fallback = sum(all_values, Decimal('0.00')) / len(all_values)
    points = []
    for offset in range(1, 31):
        date = today + timedelta(days=offset)
        values = weekday_values.get(date.weekday()) or [fallback]
        predicted = sum(values, Decimal('0.00')) / len(values)
        margin = predicted * Decimal('0.20')
        points.append({
            'date': date, 'predicted_revenue': predicted.quantize(Decimal('0.01')),
            'lower_bound': max(Decimal('0.00'), predicted - margin).quantize(Decimal('0.01')),
            'upper_bound': (predicted + margin).quantize(Decimal('0.01')),
        })
    return {
        'generated_at': timezone.now(), 'horizon_days': 30,
        'method': 'weekday_seasonal_baseline', 'is_placeholder': True,
        'forecast': points,
    }


def customer_report():
    today = timezone.localdate()
    active_since = today - timedelta(days=89)
    transactions = Transaction.objects.filter(is_voided=False, customer__isnull=False)
    values = transactions.aggregate(
        total_ltv=Sum('total_amount'),
        customers_with_purchases=Count('customer_id', distinct=True),
        active=Count(
            'customer_id', distinct=True,
            filter=Q(created_at__date__gte=active_since),
        ),
    )
    purchased = values['customers_with_purchases']
    total_ltv = _money(values['total_ltv'])
    top_customers = list(
        transactions
        .values(customer_name=F('customer__name'))
        .annotate(
            transaction_count=Count('id'),
            total_spent=Sum('total_amount'),
        )
        .order_by('-total_spent', 'customer_name')[:10]
    )
    return {
        'as_of': today, 'total_customers': Customer.objects.count(),
        'active_customer_count': values['active'],
        'customers_with_purchases': purchased,
        'average_lifetime_value': (total_ltv / purchased if purchased else Decimal('0.00')),
        'total_lifetime_value': total_ltv,
        'top_customers': top_customers,
    }


REPORT_BUILDERS = {
    'daily_sales': daily_sales,
    'weekly_sales': weekly_sales,
    'monthly_sales': monthly_sales,
    'inventory': inventory_status,
    'sarima_forecast': sarima_forecast,
    'customer': customer_report,
}


def get_report(report_type, force_refresh=False, visible_to_staff=False):
    if report_type not in REPORT_BUILDERS:
        raise ValueError(f'Unsupported report type: {report_type}')
    scope = 'staff' if visible_to_staff else 'admin'
    key = f'{CACHE_PREFIX}{scope}:{report_type}'
    if not force_refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached
    if report_type == 'inventory':
        data = inventory_status(visible_to_staff=visible_to_staff)
    else:
        data = REPORT_BUILDERS[report_type]()
    cache.set(key, data, CACHE_TIMEOUT)
    return data


def refresh_reports(visible_to_staff=False):
    generated_at = timezone.now()
    refreshed = {
        name: get_report(name, force_refresh=True, visible_to_staff=visible_to_staff)
        for name in REPORT_TYPES
    }
    return generated_at, refreshed


def _report_metadata(report_type, data):
    title = report_type.replace('_', ' ').title()
    if report_type == 'daily_sales':
        period = str(data.get('date', '-'))
    elif report_type in ('weekly_sales', 'monthly_sales'):
        period = f"{data.get('start_date', '-')} to {data.get('end_date', '-')}"
    elif report_type == 'sarima_forecast':
        period = f"Next {data.get('horizon_days', 30)} days"
    else:
        as_of = data.get('as_of', timezone.localdate())
        if hasattr(as_of, 'date'):
            as_of = as_of.date()
        elif isinstance(as_of, str):
            as_of = as_of.split('T', 1)[0].split(' ', 1)[0]
        period = f'As of {as_of}'
    return title, period


def _as_table_paragraph(value, style):
    return Paragraph(str(value), style)


def _summary_card_cell(label, value, label_size=8):
    """Return a consistently formatted summary-card cell."""
    return Paragraph(
        f'<font size="{label_size}">{label}</font><br/><b>{value}</b>',
        summary_value_style,
    )


def _format_growth_rate(value):
    if value is None:
        return 'N/A'
    prefix = '+' if value > 0 else ''
    return f'{prefix}{value}%'


def generate_pdf(report_type, data):
    buffer = BytesIO()
    page_size = landscape(A4)
    margin = 0.6 * inch
    printable_width = page_size[0] - (2 * margin)
    document = SimpleDocTemplate(
        buffer, pagesize=page_size,
        rightMargin=margin, leftMargin=margin,
        topMargin=margin, bottomMargin=0.65 * inch,
        title=f'Rosario Dairy - {report_type.replace("_", " ").title()}',
        author='Rosario Dairy System',
        subject='Operational report export',
    )
    styles = getSampleStyleSheet()
    brand_style = ParagraphStyle(
        'Brand', parent=styles['Heading1'], alignment=TA_LEFT,
        textColor=colors.white, fontName='Helvetica-Bold', fontSize=18,
        leading=21, spaceAfter=0,
    )
    banner_meta_style = ParagraphStyle(
        'BannerMeta', parent=styles['Normal'], alignment=TA_RIGHT,
        textColor=colors.white, fontName='Helvetica', fontSize=8.5, leading=12,
    )
    mark_style = ParagraphStyle(
        'BrandMark', parent=styles['Heading1'], alignment=TA_CENTER,
        textColor=PDF_PRIMARY, fontName='Helvetica-Bold', fontSize=19, leading=21,
    )
    report_title_style = ParagraphStyle(
        'ReportTitle', parent=styles['Heading2'], alignment=TA_LEFT,
        textColor=PDF_PRIMARY_DARK, fontName='Helvetica-Bold', fontSize=15,
        leading=18, spaceAfter=0,
    )
    report_meta_style = ParagraphStyle(
        'ReportMeta', parent=styles['Normal'], alignment=TA_RIGHT,
        textColor=colors.HexColor('#475569'), fontSize=9, leading=12,
    )
    header_cell_style = ParagraphStyle(
        'HeaderCell', parent=styles['Normal'], textColor=colors.white,
        fontName='Helvetica-Bold', fontSize=9, leading=11,
    )
    body_cell_style = ParagraphStyle(
        'BodyCell', parent=styles['Normal'], textColor=PDF_TEXT,
        fontName='Helvetica', fontSize=9, leading=11,
    )
    body_cell_right_style = ParagraphStyle(
        'BodyCellRight', parent=body_cell_style, alignment=TA_RIGHT,
    )
    header_cell_right_style = ParagraphStyle(
        'HeaderCellRight', parent=header_cell_style, alignment=TA_RIGHT,
    )
    section_title_style = ParagraphStyle(
        'SectionTitle', parent=styles['Heading3'], textColor=PDF_PRIMARY_DARK,
        fontName='Helvetica-Bold', fontSize=11, leading=14, spaceAfter=0,
    )
    generated = timezone.localtime()
    report_title, report_period = _report_metadata(report_type, data)
    banner = Table([
        [
            _as_table_paragraph('RD', mark_style),
            _as_table_paragraph('Rosario Dairy System', brand_style),
            _as_table_paragraph(
                f'<b>REPORT EXPORT</b><br/>Generated {generated:%B %d, %Y at %I:%M %p}',
                banner_meta_style,
            ),
        ]
    ], colWidths=[0.65 * inch, printable_width * 0.57, printable_width * 0.43 - 0.65 * inch])
    banner.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), PDF_PRIMARY),
        ('BACKGROUND', (0, 0), (0, 0), colors.white),
        ('BOX', (0, 0), (-1, -1), 0.8, PDF_PRIMARY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 11),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 11),
        ('LEFTPADDING', (0, 0), (0, 0), 5),
        ('RIGHTPADDING', (0, 0), (0, 0), 5),
    ]))
    metadata = Table([
        [
            _as_table_paragraph(report_title, report_title_style),
            _as_table_paragraph(
                f'<b>Reporting period:</b> {report_period}<br/>'
                f'<b>Report type:</b> {report_type}',
                report_meta_style,
            ),
        ]
    ], colWidths=[printable_width * 0.55, printable_width * 0.45])
    metadata.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#EFF6FF')),
        ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#BFDBFE')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
    ]))
    story = [
        banner,
        Spacer(1, 5 * mm),
        metadata,
        Spacer(1, 6 * mm),
    ]

    def summary_table(cells):
        table = Table([cells], colWidths=[printable_width / len(cells)] * len(cells))
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#EFF6FF')),
            ('BOX', (0, 0), (-1, -1), 0.7, colors.HexColor('#BFDBFE')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#BFDBFE')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        return table

    def breakdown_table(
        headers, rows, width_ratios, body_right_columns=(),
        header_right_columns=(), spans=(),
    ):
        table_data = [[
            _as_table_paragraph(
                header,
                header_cell_right_style if index in header_right_columns else header_cell_style,
            )
            for index, header in enumerate(headers)
        ]]
        table_data.extend([
            [
                cell if isinstance(cell, Paragraph) else _as_table_paragraph(
                    cell,
                    body_cell_right_style if index in body_right_columns else body_cell_style,
                )
                for index, cell in enumerate(row)
            ]
            for row in rows
        ])
        style_commands = [
            ('BACKGROUND', (0, 0), (-1, 0), PDF_PRIMARY),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('GRID', (0, 0), (-1, -1), 0.45, PDF_BORDER),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PDF_ROW_ALT]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]
        style_commands.extend(('SPAN', start, end) for start, end in spans)
        table = Table(
            table_data,
            colWidths=[printable_width * ratio for ratio in width_ratios],
            repeatRows=1,
            hAlign='LEFT',
        )
        table.setStyle(TableStyle(style_commands))
        return table

    def append_section(title, table, first=False):
        story.extend([
            Spacer(1, 6 * mm if first else 8 * mm),
            Paragraph(title, section_title_style),
            Spacer(1, 3 * mm),
            table,
        ])

    if report_type == 'daily_sales':
        story.append(summary_table([
            _summary_card_cell('REPORT DATE', data['date']),
            _summary_card_cell('TOTAL REVENUE', data['total_revenue']),
            _summary_card_cell('TRANSACTIONS', data['transaction_count']),
        ]))
        rows = [[
            item['product_name'], item['quantity'], item['total_revenue'],
        ] for item in data['items']]
        spans = []
        if not rows:
            rows = [['No products sold today', '', '']]
            spans = [((0, 1), (-1, 1))]
        table = breakdown_table(
            ['Product Name', 'Quantity Sold', 'Revenue'], rows,
            (0.55, 0.22, 0.23), body_right_columns=(1, 2),
            header_right_columns=(1, 2), spans=spans,
        )
        append_section('Products Sold Breakdown', table, first=True)

    elif report_type == 'weekly_sales':
        story.append(summary_table([
            _summary_card_cell(
                'REPORT PERIOD', f"{data['start_date']} – {data['end_date']}"
            ),
            _summary_card_cell('TOTAL REVENUE', data['revenue']),
            _summary_card_cell(
                'GROWTH VS PREV WEEK', _format_growth_rate(data['growth_rate'])
            ),
            _summary_card_cell('TRANSACTIONS', data['transaction_count']),
        ]))
        daily_rows = [[
            item['date'], item['transaction_count'], item['revenue'],
        ] for item in data['daily_breakdown']]
        append_section(
            'Daily Breakdown',
            breakdown_table(
                ['Date', 'Transactions', 'Revenue'], daily_rows,
                (0.30, 0.30, 0.40), body_right_columns=(2,),
                header_right_columns=(2,),
            ),
            first=True,
        )
        product_rows = [[
            item['product_name'], item['quantity'], item['revenue'],
        ] for item in data['top_products']]
        product_spans = []
        if not product_rows:
            product_rows = [['No sales in this period', '', '']]
            product_spans = [((0, 1), (-1, 1))]
        append_section(
            'Top Products',
            breakdown_table(
                ['Product', 'Qty Sold', 'Revenue'], product_rows,
                (0.50, 0.25, 0.25), body_right_columns=(1, 2),
                header_right_columns=(1, 2), spans=product_spans,
            ),
        )

    elif report_type == 'monthly_sales':
        story.append(summary_table([
            _summary_card_cell(
                'REPORT PERIOD', f"{data['start_date']} – {data['end_date']}"
            ),
            _summary_card_cell('TOTAL REVENUE', data['revenue']),
            _summary_card_cell(
                'GROWTH VS PREV MONTH', _format_growth_rate(data['growth_rate'])
            ),
            _summary_card_cell('TRANSACTIONS', data['transaction_count']),
        ]))
        weekly_rows = [[
            f"{item['week_start']} – {item['week_end']}",
            item['transaction_count'], item['revenue'],
        ] for item in data['weekly_breakdown']]
        append_section(
            'Weekly Breakdown',
            breakdown_table(
                ['Week', 'Transactions', 'Revenue'], weekly_rows,
                (0.40, 0.25, 0.35), body_right_columns=(2,),
                header_right_columns=(2,),
            ),
            first=True,
        )
        product_rows = [[
            item['product_name'], item['quantity'], item['revenue'],
        ] for item in data['top_products']]
        product_spans = []
        if not product_rows:
            product_rows = [['No sales in this period', '', '']]
            product_spans = [((0, 1), (-1, 1))]
        append_section(
            'Top Products',
            breakdown_table(
                ['Product', 'Qty Sold', 'Revenue'], product_rows,
                (0.50, 0.25, 0.25), body_right_columns=(1, 2),
                header_right_columns=(1, 2), spans=product_spans,
            ),
        )

    elif report_type == 'inventory':
        story.append(summary_table([
            _summary_card_cell('TOTAL PRODUCTS', data['total_products']),
            _summary_card_cell('TOTAL INGREDIENTS', data['total_ingredients']),
            _summary_card_cell('LOW STOCK ITEMS', data['low_stock_count']),
            _summary_card_cell('EXPIRING SOON', data['expiring_soon_batch_count']),
        ]))
        inventory_rows = []
        for item in data['items']:
            status = item['fefo_status']
            status_label = status.replace('_', ' ').title()
            status_cell = Paragraph(
                f'<font color="{STATUS_COLORS[status]}">{status_label}</font>',
                body_cell_style,
            )
            inventory_rows.append([
                item['item_type'].title(), item['name'], str(item['quantity']), item['unit'],
                str(item['next_expiration_date'] or '-'), status_cell,
            ])
        append_section(
            'Stock Status',
            breakdown_table(
                ['Type', 'Item', 'Stock', 'Unit', 'Next Expiry', 'FEFO Status'],
                inventory_rows, (0.11, 0.29, 0.12, 0.10, 0.19, 0.19),
                body_right_columns=(2,), header_right_columns=(2,),
            ),
            first=True,
        )

    elif report_type == 'sarima_forecast':
        story.append(summary_table([
            _summary_card_cell('HORIZON', '30 days'),
            _summary_card_cell('METHOD', 'Weekday Seasonal Baseline'),
            _summary_card_cell('STATUS', 'Placeholder Model'),
        ]))
        disclaimer_style = ParagraphStyle(
            'ForecastDisclaimer', parent=body_cell_style,
            fontName='Helvetica-Oblique', textColor=colors.HexColor('#64748B'),
        )
        story.extend([
            Spacer(1, 3 * mm),
            Paragraph(
                'This forecast uses a weekday-seasonal baseline. Replace with a '
                'fitted SARIMA model for production use.',
                disclaimer_style,
            ),
        ])
        forecast_rows = [[
            point['date'], point['predicted_revenue'],
            point['lower_bound'], point['upper_bound'],
        ] for point in data['forecast']]
        append_section(
            '30-Day Forecast',
            breakdown_table(
                ['Date', 'Predicted Revenue', 'Lower Bound', 'Upper Bound'],
                forecast_rows, (0.22, 0.26, 0.26, 0.26),
                body_right_columns=(1, 2, 3), header_right_columns=(1, 2, 3),
            ),
            first=True,
        )

    elif report_type == 'customer':
        story.append(summary_table([
            _summary_card_cell('TOTAL CUSTOMERS', data['total_customers']),
            _summary_card_cell('ACTIVE (90 DAYS)', data['active_customer_count']),
            _summary_card_cell('AVG LIFETIME VALUE', data['average_lifetime_value']),
        ]))
        customer_rows = [[
            item['customer_name'], item['transaction_count'], item['total_spent'],
        ] for item in data['top_customers']]
        customer_spans = []
        if not customer_rows:
            customer_rows = [['No customer transactions yet', '', '']]
            customer_spans = [((0, 1), (-1, 1))]
        append_section(
            'Top Customers by Spend',
            breakdown_table(
                ['Customer', 'Transactions', 'Total Spent'], customer_rows,
                (0.50, 0.20, 0.30), body_right_columns=(1, 2),
                header_right_columns=(1, 2), spans=customer_spans,
            ),
            first=True,
        )

    document.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer
