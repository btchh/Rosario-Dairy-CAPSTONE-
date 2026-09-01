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
            'date': today,
        }
        for row in item_rows
    ]
    return {
        'date': today,
        'total_revenue': revenue,
        'transaction_count': count,
        'items': items,
    }


def weekly_sales():
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=6)
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)
    revenue, count = _sales_summary(start_date, end_date)
    previous_revenue, _ = _sales_summary(previous_start, previous_end)
    return {
        'start_date': start_date, 'end_date': end_date, 'revenue': revenue,
        'transaction_count': count, 'previous_revenue': previous_revenue,
        'growth_rate': _growth_rate(revenue, previous_revenue),
    }


def monthly_sales():
    today = timezone.localdate()
    start_date = today.replace(day=1)
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    revenue, count = _sales_summary(start_date, today)
    previous_revenue, _ = _sales_summary(previous_start, previous_end)
    return {
        'start_date': start_date, 'end_date': today, 'revenue': revenue,
        'transaction_count': count, 'previous_revenue': previous_revenue,
        'growth_rate': _growth_rate(revenue, previous_revenue),
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
    return {
        'as_of': today, 'total_customers': Customer.objects.count(),
        'active_customer_count': values['active'],
        'customers_with_purchases': purchased,
        'average_lifetime_value': (total_ltv / purchased if purchased else Decimal('0.00')),
        'total_lifetime_value': total_ltv,
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


def _flatten_rows(report_type, data):
    if report_type == 'daily_sales':
        headers = ['Date', 'Product Name', 'Quantity Sold', 'Total Revenue']
        rows = [[
            item['date'], item['product_name'], item['quantity'], item['total_revenue'],
        ] for item in data['items']]
    elif report_type == 'inventory':
        headers = ['Type', 'Item', 'Stock', 'Unit', 'Next expiry', 'FEFO status']
        rows = [[
            item['item_type'].title(), item['name'], str(item['quantity']), item['unit'],
            str(item['next_expiration_date'] or '-'), item['fefo_status'].replace('_', ' ').title(),
        ] for item in data['items']]
    elif report_type == 'sarima_forecast':
        headers = ['Date', 'Forecast', 'Lower bound', 'Upper bound']
        rows = [[p['date'], p['predicted_revenue'], p['lower_bound'], p['upper_bound']] for p in data['forecast']]
    else:
        headers = ['Metric', 'Value']
        rows = [[key.replace('_', ' ').title(), value] for key, value in data.items()]
    return headers, rows


def _column_widths(report_type, printable_width):
    ratios = {
        'inventory': (0.11, 0.29, 0.12, 0.10, 0.19, 0.19),
        'sarima_forecast': (0.25, 0.25, 0.25, 0.25),
        'daily_sales': (0.18, 0.42, 0.18, 0.22),
        'weekly_sales': (0.36, 0.64),
        'monthly_sales': (0.36, 0.64),
        'customer': (0.36, 0.64),
    }[report_type]
    return [printable_width * ratio for ratio in ratios]


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
    summary_value_style = ParagraphStyle(
        'SummaryValue', parent=styles['Normal'], textColor=PDF_PRIMARY_DARK,
        fontName='Helvetica-Bold', fontSize=15, leading=18,
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
    headers, rows = _flatten_rows(report_type, data)
    if report_type == 'daily_sales':
        summary = Table([[
            Paragraph(
                f'<font size="8">REPORT DATE</font><br/><b>{data["date"]}</b>',
                summary_value_style,
            ),
            Paragraph(
                f'<font size="8">TOTAL REVENUE</font><br/><b>{data["total_revenue"]}</b>',
                summary_value_style,
            ),
            Paragraph(
                f'<font size="8">TRANSACTIONS</font><br/><b>{data["transaction_count"]}</b>',
                summary_value_style,
            ),
        ]], colWidths=[printable_width / 3] * 3)
        summary.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#EFF6FF')),
            ('BOX', (0, 0), (-1, -1), 0.7, colors.HexColor('#BFDBFE')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#BFDBFE')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        story.extend([
            summary,
            Spacer(1, 6 * mm),
            Paragraph('Products Sold Breakdown', section_title_style),
            Spacer(1, 3 * mm),
        ])
        if not rows:
            rows = [['-', 'No products sold today', '-', '-']]
    table_data = [
        [_as_table_paragraph(header, header_cell_style) for header in headers]
    ] + [
        [
            _as_table_paragraph(
                cell,
                body_cell_right_style
                if report_type == 'daily_sales' and index in (2, 3)
                else body_cell_style,
            )
            for index, cell in enumerate(row)
        ]
        for row in rows
    ]
    table = Table(
        table_data,
        colWidths=_column_widths(report_type, printable_width),
        repeatRows=1,
        hAlign='LEFT',
    )
    table.setStyle(TableStyle([
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
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
    ]))
    story.append(table)
    document.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer
