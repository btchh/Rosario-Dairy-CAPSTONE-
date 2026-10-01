"""Portrait business reports with shared typography, alignment and evidence notes."""
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from systemsetting.runtime import get_brand_name, get_runtime_settings


NAVY = colors.HexColor('#17345F')
BLUE = colors.HexColor('#2875C7')
INK = colors.HexColor('#243247')
MUTED = colors.HexColor('#65758B')
LINE = colors.HexColor('#DCE5EE')
PALE = colors.HexColor('#F3F7FB')
PAGE_WIDTH, _ = A4
MARGIN = 18 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN


def value(number):
    try:
        return Decimal(str(number or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')


def number(amount, digits=2):
    return f'{value(amount):,.{digits}f}'


def money(amount, currency):
    return f'{currency} {number(amount)}'


def pct(part, whole):
    return f'{(value(part) / value(whole) * 100):.1f}%' if value(whole) else '0.0%'


def label(text):
    return str(text or '—').replace('_', ' ').title()


class FooterCanvas(canvas.Canvas):
    def __init__(self, *args, brand='Rosario Dairy', **kwargs):
        super().__init__(*args, **kwargs)
        self.brand = brand
        self.pages = []

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self.pages)
        for state in self.pages:
            self.__dict__.update(state)
            self.setStrokeColor(LINE)
            self.line(MARGIN, 14 * mm, PAGE_WIDTH - MARGIN, 14 * mm)
            self.setFillColor(MUTED)
            self.setFont('Helvetica', 8)
            self.drawString(MARGIN, 10 * mm, f'{self.brand}  •  Internal business report')
            self.drawRightString(PAGE_WIDTH - MARGIN, 10 * mm, f'{self._pageNumber} / {total}')
            super().showPage()
        super().save()


class Report:
    def __init__(self, report_type, data):
        self.data = data
        self.type = report_type
        self.settings = get_runtime_settings()
        self.brand = get_brand_name()
        self.currency = self.settings['currency']
        base = getSampleStyleSheet()
        self.styles = {
            'eyebrow': ParagraphStyle('Eyebrow', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=8, leading=11, textColor=BLUE, spaceAfter=4),
            'title': ParagraphStyle('ReportTitle', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=19, leading=22, textColor=NAVY, spaceAfter=4),
            'meta': ParagraphStyle('ReportMeta', parent=base['Normal'], fontSize=8.5,
                leading=12, textColor=MUTED),
            'section': ParagraphStyle('Section', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=11, leading=14, textColor=NAVY),
            'body': ParagraphStyle('Body', parent=base['Normal'], fontSize=8.5,
                leading=12.5, textColor=INK),
            'note': ParagraphStyle('Note', parent=base['Normal'], fontSize=8,
                leading=10, textColor=MUTED),
            'head': ParagraphStyle('Head', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=8, leading=10, textColor=colors.white),
            'head_right': ParagraphStyle('HeadRight', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=8, leading=10, textColor=colors.white, alignment=TA_RIGHT),
            'cell': ParagraphStyle('Cell', parent=base['Normal'], fontSize=8,
                leading=11, textColor=INK, alignment=TA_LEFT),
            'cell_right': ParagraphStyle('CellRight', parent=base['Normal'], fontSize=8,
                leading=11, textColor=INK, alignment=TA_RIGHT),
            'metric_label': ParagraphStyle('MetricLabel', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=7.5, leading=10, textColor=MUTED),
            'metric_value': ParagraphStyle('MetricValue', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=12, leading=15, textColor=NAVY),
        }
        self.story = []

    def p(self, text, style='body'):
        return Paragraph(escape(str(text)), self.styles[style])

    def section(self, title):
        self.story.extend([Spacer(1, 4 * mm), self.p(title, 'section'), Spacer(1, 2 * mm)])

    def note(self, text):
        self.story.extend([Spacer(1, 1 * mm), self.p(text, 'note')])

    def cards(self, metrics):
        cells = []
        for title, val in metrics:
            cells.append([self.p(title.upper(), 'metric_label'), Spacer(1, 1 * mm),
                          self.p(val, 'metric_value')])
        columns = min(3, len(cells))
        rows = [cells[i:i + columns] for i in range(0, len(cells), columns)]
        rows[-1] += [''] * (columns - len(rows[-1]))
        table = Table(rows, colWidths=[CONTENT_WIDTH / columns] * columns)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), PALE),
            ('LINEBELOW', (0, -1), (-1, -1), 0.7, LINE),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 9),
            ('RIGHTPADDING', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        self.story.append(table)

    def table(self, headers, rows, ratios, numeric=(), empty='No records for this period.'):
        if not rows:
            self.story.append(self.p(empty, 'note'))
            return
        head = [self.p(h, 'head_right' if i in numeric else 'head')
                for i, h in enumerate(headers)]
        body = [[self.p(cell, 'cell_right' if i in numeric else 'cell')
                 for i, cell in enumerate(row)] for row in rows]
        table = Table([head, *body], colWidths=[CONTENT_WIDTH * r for r in ratios],
                      repeatRows=1, hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), NAVY),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PALE]),
            ('LINEBELOW', (0, 1), (-1, -1), 0.35, LINE),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        self.story.append(table)

    def header(self):
        titles = {
            'daily_sales': 'Daily sales', 'weekly_sales': 'Seven-day sales',
            'monthly_sales': 'Month-to-date sales', 'inventory': 'Inventory health',
            'sarima_forecast': 'Sales forecast', 'customer': 'Customer activity',
        }
        if self.type == 'daily_sales':
            period = self.data['date']
        elif self.type in ('weekly_sales', 'monthly_sales'):
            period = f"{self.data['start_date']} to {self.data['end_date']}"
        elif self.type == 'sarima_forecast':
            period = f"{label(self.data.get('period', 'monthly'))} horizon"
        else:
            period = f"As of {str(self.data['as_of'])[:10]}"
        self.story.extend([
            self.p(self.brand.upper(), 'eyebrow'),
            self.p(titles[self.type], 'title'),
            self.p(f'{period}   •   Generated {timezone.localtime():%d %b %Y, %I:%M %p}', 'meta'),
            Spacer(1, 4 * mm),
        ])
        profile = self.settings
        contact = '  •  '.join(part for part in (
            profile.get('business_type', ''),
            profile.get('business_address', ''),
            f"Phone: {profile['business_contact']}" if profile.get('business_contact') else '',
            f"Email: {profile['business_email']}" if profile.get('business_email') else '',
            f"TIN: {profile['tin']}" if profile.get('tin') else '',
        ) if part)
        if contact:
            self.story.extend([self.p(contact, 'note'), Spacer(1, 2 * mm)])

    def insights(self, lines):
        self.section('What the numbers say')
        for line in lines:
            self.story.extend([self.p('•  ' + line), Spacer(1, 1.5 * mm)])

    def sales(self):
        d = self.data
        daily = self.type == 'daily_sales'
        net = value(d['total_revenue'] if daily else d['revenue'])
        count = d['transaction_count']
        gross = value(d.get('gross_sales', net))
        discounts = value(d.get('discounts', 0))
        self.cards([
            ('Net sales', money(net, self.currency)),
            ('Transactions', str(count)),
            ('Average sale', money(d.get('average_ticket', net / count if count else 0), self.currency)),
            ('Gross before discounts', money(gross, self.currency)),
            ('Discounts', money(discounts, self.currency)),
            ('Discount rate', pct(discounts, gross)),
        ])
        products = d['items'] if daily else d['top_products']
        top = products[0] if products else None
        top_revenue = value((top.get('total_revenue') if daily else top.get('revenue')) if top else 0)
        lines = [f'{count} completed sale{"s" if count != 1 else ""} generated {money(net, self.currency)} in net revenue.' ]
        if top:
            lines.append(f"{top['product_name']} led product sales at {money(top_revenue, self.currency)} gross ({pct(top_revenue, gross)} of gross transaction value).")
        if daily:
            lines.append(f"Yesterday brought {d.get('previous_transaction_count', 0)} sales and {money(d.get('previous_revenue', 0), self.currency)} in net revenue.")
        if self.type == 'weekly_sales':
            active = sum(value(day['transaction_count']) > 0 for day in d['daily_breakdown'])
            growth = d.get('growth_rate')
            change = f'{growth:+.1f}%' if growth is not None else 'an unmeasurable amount'
            lines.append(f'{active} of 7 days recorded a sale. Net revenue changed {change} against the prior seven days ({money(d["previous_revenue"], self.currency)} from {d.get("previous_transaction_count", 0)} sales).')
        if self.type == 'monthly_sales':
            lines.append(f'The matching prior-month period ({d.get("previous_period_start")} to {d.get("previous_period_end")}) produced {money(d["previous_revenue"], self.currency)} from {d.get("previous_transaction_count", 0)} sales.')
        if discounts:
            lines.append(f'Discounts reduced gross sales by {money(discounts, self.currency)} ({pct(discounts, gross)}).')
        self.insights(lines)
        if self.type == 'weekly_sales':
            self.section('Day-by-day performance')
            self.table(['Date', 'Sales', 'Net revenue'], [
                [r['date'], r['transaction_count'], money(r['revenue'], self.currency)]
                for r in d['daily_breakdown']], [.38, .18, .44], numeric=(1, 2))
        elif self.type == 'monthly_sales':
            self.section('Week-by-week performance')
            self.table(['Week', 'Sales', 'Net revenue'], [
                [f"{r['week_start']} to {r['week_end']}", r['transaction_count'], money(r['revenue'], self.currency)]
                for r in d['weekly_breakdown']], [.48, .16, .36], numeric=(1, 2))
        self.section('Product contribution')
        self.table(['Product', 'Units', 'Gross sales', 'Share'], [
            [r['product_name'], number(r['quantity']), money(r.get('total_revenue', r.get('revenue')), self.currency),
             pct(r.get('total_revenue', r.get('revenue')), gross)] for r in products
        ], [.40, .16, .29, .15], numeric=(1, 2, 3))
        self.section('Product families across pack sizes')
        self.table(['Product family', 'Units', 'Gross sales', 'Share'], [
            [r['product_name'], number(r['quantity']), money(r['gross_sales'], self.currency),
             pct(r['gross_sales'], gross)] for r in d.get('family_mix', [])
        ], [.40, .16, .29, .15], numeric=(1, 2, 3))
        self.section('Category contribution')
        self.table(['Category', 'Units', 'Gross sales', 'Share'], [
            [r['category'] or 'Uncategorized', number(r['quantity']), money(r['gross_sales'], self.currency), pct(r['gross_sales'], gross)]
            for r in d.get('category_mix', [])
        ], [.40, .16, .29, .15], numeric=(1, 2, 3))
        self.section('How customers paid')
        self.table(['Method', 'Sales', 'Net revenue', 'Share'], [
            [label(r['payment_method']), r['transaction_count'], money(r['revenue'], self.currency), pct(r['revenue'], net)]
            for r in d.get('payment_mix', [])
        ], [.30, .16, .37, .17], numeric=(1, 2, 3))
        self.note('Product and category amounts are gross line values before transaction discounts; period and payment totals are net. Shares may differ slightly when discounts apply.')

    def inventory(self):
        d = self.data
        counts = d.get('status_counts', {})
        self.cards([
            ('Active products', str(d['total_products'])),
            ('Ingredients', str(d['total_ingredients'])),
            ('Low stock', str(d['low_stock_count'])),
            ('Expiring in 7 days', str(d['expiring_soon_batch_count'])),
            ('Expired batches', str(d['expired_batch_count'])),
            ('No available stock', str(counts.get('no_stock', 0))),
        ])
        risks = [r for r in d['items'] if r['is_low_stock'] or r['fefo_status'] != 'healthy']
        risks.sort(key=lambda r: (not r['is_low_stock'],
            {'expired': 0, 'no_stock': 1, 'expiring_soon': 2, 'healthy': 3}.get(r['fefo_status'], 4),
            str(r['next_expiration_date'] or '9999-12-31'), r['name'].lower()))
        self.insights([
            f'{len(risks)} of {len(d["items"])} listed items need a stock or expiry review.',
            f'{counts.get("expiring_soon", 0)} item(s) have their next available batch expiring within seven days; {counts.get("no_stock", 0)} have no available stock.',
            'Replenish low-stock items and sell or inspect earlier-expiring batches first.',
        ])
        self.section('Priority stock review')
        self.table(['Item', 'Available', 'Minimum', 'Next expiry', 'Reason'], [
            [r['name'], f"{number(r['quantity'])} {r['unit']}", number(r['low_stock_threshold']),
             r['next_expiration_date'] or '—',
             ('Low stock; ' if r['is_low_stock'] else '') + label(r['fefo_status'])]
            for r in risks[:20]
        ], [.30, .17, .12, .18, .23], numeric=(1, 2), empty='No stock or expiry exceptions.')
        self.section('Full stock register')
        self.table(['Type / item', 'Available', 'Minimum', 'Next expiry', 'Status'], [
            [f"{label(r['item_type'])}: {r['name']}", f"{number(r['quantity'])} {r['unit']}",
             number(r['low_stock_threshold']), r['next_expiration_date'] or '—', label(r['fefo_status'])]
            for r in d['items']
        ], [.32, .21, .13, .18, .16], numeric=(1, 2))
        self.note('Available quantity and next expiry use available batches. Expired/expiring batch counts may include more than one batch per item.')

    def customer(self):
        d = self.data
        purchased = d['customers_with_purchases']
        top_spend = sum((value(r['total_spent']) for r in d['top_customers']), Decimal('0'))
        self.cards([
            ('Customer records', str(d['total_customers'])),
            ('Ever purchased', str(purchased)),
            ('Active in 90 days', str(d['active_customer_count'])),
            ('Repeat customers', str(d['repeat_customer_count'])),
            ('Customer-linked revenue', money(d['total_lifetime_value'], self.currency)),
            ('Average lifetime spend', money(d['average_lifetime_value'], self.currency)),
        ])
        self.insights([
            f'{pct(d["repeat_customer_count"], purchased)} of customers who bought have made at least two purchases.',
            f'The ten highest-spending customers account for {pct(top_spend, d["total_lifetime_value"])} of customer-linked revenue.',
            f'{d["unassigned_transaction_count"]} sales worth {money(d["unassigned_revenue"], self.currency)} have no customer attached and are excluded from lifetime-value figures.',
        ])
        self.section('Highest-spending customers')
        self.table(['Customer', 'Purchases', 'Lifetime spend', 'Share'], [
            [r['customer_name'], r['transaction_count'], money(r['total_spent'], self.currency),
             pct(r['total_spent'], d['total_lifetime_value'])] for r in d['top_customers']
        ], [.42, .15, .28, .15], numeric=(1, 2, 3))
        self.note('Lifetime figures include all non-voided customer-linked sales through the report date. “Active” means at least one purchase in the past 90 days.')

    def forecast(self):
        d = self.data
        status = d.get('status', 'unavailable')
        regular, bulk, combined = (d.get(key) or {} for key in ('regular', 'bulk', 'combined'))
        ready = status == 'ready' and combined.get('predicted_revenue') is not None
        self.cards([
            ('Forecast status', label(status)),
            ('Horizon', f"{d.get('horizon_days', 0)} days"),
            ('Planning estimate', money(combined['predicted_revenue'], self.currency) if ready else 'Withheld'),
        ])
        lines = [
            'A planning figure is shown only when the evaluated model meets its quality gate.' if ready
            else 'No validated combined planning figure is available for this period; do not treat a withheld amount as zero sales.',
        ]
        score = (d.get('metrics') or {}).get('combined') or {}
        if score.get('wape_percent') is not None:
            lines.append(f"Historical combined WAPE is {number(score['wape_percent'])}% across {score.get('rows', 0)} evaluated periods; the acceptance target is {d.get('accuracy_target_percent', 30)}% or lower.")
        self.insights(lines)
        self.section('Planning components')
        rows = [
            ['Regular sales', money(regular['predicted_revenue'], self.currency) if regular.get('predicted_revenue') is not None else 'Withheld',
             money(regular['lower_bound'], self.currency) if regular.get('lower_bound') is not None else '—',
             money(regular['upper_bound'], self.currency) if regular.get('upper_bound') is not None else '—'],
            ['Bulk-sale risk', money(bulk['expected_revenue'], self.currency) if bulk.get('expected_revenue') is not None else 'Insufficient history',
             money(bulk['min_revenue'], self.currency) if bulk.get('min_revenue') is not None else '—',
             money(bulk['max_revenue'], self.currency) if bulk.get('max_revenue') is not None else '—'],
            ['Combined', money(combined['predicted_revenue'], self.currency) if ready else 'Withheld',
             money(combined['lower_bound'], self.currency) if ready and combined.get('lower_bound') is not None else '—',
             money(combined['upper_bound'], self.currency) if ready and combined.get('upper_bound') is not None else '—'],
        ]
        self.table(['Component', 'Expected', 'Low case', 'High case'], rows,
                   [.28, .24, .24, .24], numeric=(1, 2, 3))
        metrics = d.get('metrics') or {}
        metric_rows = [
            [label(name), values.get('rows', 0),
             f"{number(values['wape_percent'])}%" if values.get('wape_percent') is not None else 'N/A',
             f"{number(values['coverage_percent'])}%" if values.get('coverage_percent') is not None else 'N/A']
            for name, values in metrics.items() if name in ('regular', 'bulk', 'combined')
        ]
        if metric_rows:
            self.section('Historical evaluation')
            self.table(['Component', 'Periods', 'WAPE', 'Range coverage'], metric_rows,
                       [.32, .16, .22, .30], numeric=(1, 2, 3))
        if d.get('forecast'):
            self.section('Forecast period')
            self.table(['From', 'To', 'Expected', 'Low–high'], [
                [p['date'], p.get('end_date', p['date']), money(p['predicted_revenue'], self.currency),
                 f"{money(p['lower_bound'], self.currency)} – {money(p['upper_bound'], self.currency)}"]
                for p in d['forecast']
            ], [.18, .18, .24, .40], numeric=(2, 3))
        for warning in d.get('warnings', [])[:6]:
            self.note(f'Note: {warning}')
        self.note(d.get('limitations') or 'Forecasts are planning estimates, not guaranteed sales.')

    def render(self):
        self.header()
        {'daily_sales': self.sales, 'weekly_sales': self.sales, 'monthly_sales': self.sales,
         'inventory': self.inventory, 'sarima_forecast': self.forecast,
         'customer': self.customer}[self.type]()
        buffer = BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=MARGIN,
            rightMargin=MARGIN, topMargin=17 * mm, bottomMargin=19 * mm,
            title=f'{self.brand} — {self.type.replace("_", " ").title()}', author=self.brand)
        document.build(self.story, canvasmaker=lambda *args, **kwargs: FooterCanvas(
            *args, brand=self.brand, **kwargs))
        buffer.seek(0)
        return buffer


def generate_pdf(report_type, data):
    return Report(report_type, data).render()
