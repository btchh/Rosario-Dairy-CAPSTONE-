"""Business reports with a landscape daily inventory ledger."""
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import CondPageBreak, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from systemsetting.runtime import get_brand_name, get_runtime_settings


NAVY = colors.HexColor('#17345F')
BLUE = colors.HexColor('#2875C7')
INK = colors.HexColor('#243247')
MUTED = colors.HexColor('#65758B')
LINE = colors.HexColor('#DCE5EE')
PALE = colors.HexColor('#F3F7FB')
MARGIN = 18 * mm


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
            self.line(MARGIN, 14 * mm, self._pagesize[0] - MARGIN, 14 * mm)
            self.setFillColor(MUTED)
            self.setFont('Helvetica', 8)
            self.drawString(MARGIN, 10 * mm, f'{self.brand}  •  Internal business report')
            self.drawRightString(self._pagesize[0] - MARGIN, 10 * mm, f'{self._pageNumber} / {total}')
            super().showPage()
        super().save()


class Report:
    def __init__(self, report_type, data):
        self.data = data
        self.type = report_type
        self.page_size = landscape(A4) if report_type in ("inventory", "customer") else A4
        self.content_width = self.page_size[0] - 2 * MARGIN
        self.settings = get_runtime_settings()
        self.brand = get_brand_name()
        self.currency = self.settings['currency']
        base = getSampleStyleSheet()
        self.styles = {
            'eyebrow': ParagraphStyle('Eyebrow', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=8, leading=11, textColor=BLUE, spaceAfter=4, keepWithNext=True),
            'title': ParagraphStyle('ReportTitle', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=19, leading=22, textColor=NAVY, spaceAfter=4, keepWithNext=True),
            'meta': ParagraphStyle('ReportMeta', parent=base['Normal'], fontSize=8.5,
                leading=12, textColor=MUTED),
            'section': ParagraphStyle('Section', parent=base['Normal'], fontName='Helvetica-Bold',
                fontSize=11, leading=14, textColor=NAVY,
                spaceBefore=4 * mm, spaceAfter=2 * mm),
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
        self.story.append(self.p(title, 'section'))

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
        table = Table(rows, colWidths=[self.content_width / columns] * columns)
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
        table = Table([head, *body], colWidths=[self.content_width * r for r in ratios],
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
            'monthly_sales': 'Month-to-date sales', 'inventory': 'Daily inventory breakdown',
            'sarima_forecast': 'Sales forecast', 'customer': 'Customer monthly summary',
        }
        if self.data.get('is_custom_range') and self.type in ('daily_sales', 'weekly_sales', 'monthly_sales'):
            titles[self.type] = 'Sales summary'
        if self.type == 'inventory' and self.data.get('is_custom_range'):
            titles[self.type] = 'Inventory movement report'
        if self.type in ('daily_sales', 'weekly_sales', 'monthly_sales', 'customer') or self.data.get('is_custom_range'):
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
            lines.append(f"The preceding comparison period ({d.get('previous_period_start')} to {d.get('previous_period_end')}) brought {d.get('previous_transaction_count', 0)} sales and {money(d.get('previous_revenue', 0), self.currency)} in net revenue.")
        if self.type == 'weekly_sales':
            days = len(d['daily_breakdown'])
            active = sum(value(day['transaction_count']) > 0 for day in d['daily_breakdown'])
            growth = d.get('growth_rate')
            change = f'{growth:+.1f}%' if growth is not None else 'an unmeasurable amount'
            lines.append(f'{active} of {days} days recorded a sale. Net revenue changed {change} against the preceding comparison period ({money(d["previous_revenue"], self.currency)} from {d.get("previous_transaction_count", 0)} sales).')
        if self.type == 'monthly_sales':
            lines.append(f'The comparison period ({d.get("previous_period_start")} to {d.get("previous_period_end")}) produced {money(d["previous_revenue"], self.currency)} from {d.get("previous_transaction_count", 0)} sales.')
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

    def inventory_ledger(self):
        ledger = self.data['ledger']
        columns = ledger['columns']
        # Wide ledgers continue horizontally on new sheets without dropping
        # customers. Stock totals and the complete product list repeat.
        groups = [columns[i:i + 6] for i in range(0, len(columns), 6)] or [[]]
        style = ParagraphStyle('Ledger', parent=self.styles['cell'], fontSize=7, leading=9)
        head_style = ParagraphStyle('LedgerHead', parent=style, fontName='Helvetica-Bold',
                                    textColor=colors.white)
        def cell(text, head=False):
            return Paragraph(escape(str(text)), head_style if head else style)
        def qty(amount):
            return f'{value(amount).normalize():f}'
        for sheet, group in enumerate(groups):
            if sheet:
                self.story.append(PageBreak())
                self.header()
            ledger_period = (f'{ledger["start_date"]} to {ledger["end_date"]}'
                             if self.data.get('is_custom_range') else str(ledger['date']))
            self.section(f'Stock ledger — {ledger_period}' + (f' · Sheet {sheet + 1}/{len(groups)}' if len(groups) > 1 else ''))
            top = ['No.', 'Description', 'Stocks', '', '', 'Outlet', *(['Customer / outlet allocations'] + [''] * (len(group) - 1) if group else []), 'Total ending inventory', 'Remarks']
            bottom = ['', '', 'Beginning inv. (calc.)', 'Production / received', 'Total', '', *[c['name'] for c in group], '', '']
            data = [[cell(text, True) for text in top], [cell(text, True) for text in bottom]]
            for index, row in enumerate(ledger['rows'], 1):
                data.append([cell(index), cell(f'{row["name"]} ({row["unit"]})'),
                    cell(qty(row['beginning'])), cell(qty(row['received'])), cell(qty(row['total'])),
                    cell(qty(row['outlet'])), *[cell(qty(row['allocations'].get(c['key'], 0))) for c in group],
                    cell(qty(row['ending'])), cell(row['remarks'] or '—')])
            fixed = [23, 110, 46, 54, 45, 44]
            ending, remarks = 59, 110
            flexible = self.content_width - sum(fixed) - ending - remarks
            widths = [*fixed, *([flexible / len(group)] * len(group) if group else []), ending, remarks]
            if not group:
                widths[1] += flexible
            table = Table(data, colWidths=widths, repeatRows=2, splitInRow=1, hAlign='LEFT')
            commands = [
                ('SPAN', (2, 0), (4, 0)),
                ('BACKGROUND', (0, 0), (-1, 1), INK),
                ('GRID', (0, 0), (-1, -1), .45, MUTED),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]
            for col in [0, 1, 5, len(top) - 2, len(top) - 1]:
                commands.append(('SPAN', (col, 0), (col, 1)))
            if len(group) > 1:
                commands.append(('SPAN', (6, 0), (5 + len(group), 0)))
            table.setStyle(TableStyle(commands))
            self.story.append(table)
            if not ledger['rows']:
                self.note('No active products to report.')
            self.note('Outlet means sales without a named customer. Customer columns show stock issued to each customer.')
            self.note('Beginning stock is calculated. Ending stock includes expired items still on hand.')
            if self.data.get('is_custom_range'):
                self.note('Movements use their recorded physical dates, including the entry date for backdated encoded invoices. Balances are reconstructed from retained movements using the current product catalog; missing legacy movements cannot be recovered.')
            if len(groups) > 1:
                self.note('Stock balances repeat on each sheet; do not add them together.')

    def inventory(self):
        self.inventory_ledger()
        self.story.append(PageBreak())
        self.section('Current inventory health and sellable stock')
        d = self.data
        self.note(f'Current stock as of {str(d["as_of"])[:10]}. Health figures are current; the stock ledger above covers the selected dates.')
        counts = d.get('status_counts', {})
        self.cards([
            ('Active products', str(d['total_products'])),
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
            for r in risks
        ], [.30, .17, .12, .18, .23], numeric=(1, 2), empty='No stock or expiry exceptions.')
        self.section('Full stock register')
        self.table(['Type / item', 'Available', 'Minimum', 'Next expiry', 'Status'], [
            [r['name'] if r['item_type'] == 'product' else f"{label(r['item_type'])}: {r['name']}", f"{number(r['quantity'])} {r['unit']}",
             number(r['low_stock_threshold']), r['next_expiration_date'] or '—', label(r['fefo_status'])]
            for r in d['items']
        ], [.32, .21, .13, .18, .16], numeric=(1, 2))
        self.note('Available quantity and next expiry use available batches. Expired/expiring batch counts may include more than one batch per item.')

    def customer(self):
        d = self.data
        self.cards([
            ('Customers in period', str(d['customers_with_purchases'])),
            ('Repeat customers', str(d['repeat_customer_count'])),
            ('Invoices in period', str(d['transaction_count'])),
            ('Total invoice value', money(d['total_revenue'], self.currency)),
            ('Paid invoice value', money(d['paid_amount'], self.currency)),
            ('Pending invoice value', money(d['pending_amount'], self.currency)),
        ])
        self.note(f"Sales from {d['start_date']} through {d['end_date']} inclusive, using {d['timezone']} sales dates. Voided invoices are excluded. Only customers with non-voided transactions in this period are listed, including inactive accounts with purchases.")
        multiple_months = len(d['monthly_summary']) > 1
        months_with_purchases = [month for month in d['monthly_summary'] if month['transaction_count']]
        def month_label(month):
            return date.fromisoformat(month + '-01').strftime('%B %Y')
        if multiple_months and months_with_purchases:
            self.section('Month-by-month overview')
            self.table(['Month', 'Included dates', 'Customers purchasing', 'Invoices', 'Invoice total'], [
                [month_label(r['month']), f"{r['start_date']} to {r['end_date']}", r['customer_count'],
                 r['transaction_count'], money(r['revenue'], self.currency)] for r in months_with_purchases
            ], [.16, .29, .19, .12, .24], numeric=(2, 3, 4))
        self.section('Customer purchase and payment summary')
        self.table(['Customer / account', 'Purchases', 'Invoice total', 'Paid', 'Pending', 'Unknown', 'Last purchase in period'], [
            [f"{r['customer_name']} · #{r['customer_id']}" + (' (inactive)' if not r['is_active'] else ''),
             r['transaction_count'], money(r['total_spent'], self.currency),
             money(r['paid_amount'], self.currency), money(r['pending_amount'], self.currency),
             money(r['unknown_amount'], self.currency), r['last_purchase'] or 'No purchases']
            for r in d['customers']
        ], [.28, .08, .13, .13, .13, .13, .12], numeric=(1, 2, 3, 4, 5), empty='No customer transactions recorded in the selected period.')
        for month in months_with_purchases if multiple_months else []:
            self.section(f"Customer summary — {month_label(month['month'])}")
            self.table(['Customer / account', 'Purchases', 'Invoice total', 'Paid', 'Pending', 'Unknown'], [
                [f"{r['customer_name']} · #{r['customer_id']}", r['transaction_count'],
                 money(r['total_spent'], self.currency), money(r['paid_amount'], self.currency),
                 money(r['pending_amount'], self.currency), money(r['unknown_amount'], self.currency)]
                for r in month['customers']
            ], [.30, .10, .15, .15, .15, .15], numeric=(1, 2, 3, 4, 5), empty='No customer purchases recorded in this month.')
        pending = sorted((r for r in d['customers'] if r['pending_count']),
                         key=lambda r: (-value(r['pending_amount']), r['customer_name'], r['customer_id']))
        if pending:
            self.section('Payment follow-up for selected period')
            self.table(['Customer / account', 'Pending invoices', 'Pending invoice value'], [
                [f"{r['customer_name']} · #{r['customer_id']}", r['pending_count'], money(r['pending_amount'], self.currency)]
                for r in pending
            ], [.55, .20, .25], numeric=(1, 2))
        self.note('Paid, pending and unknown are full invoice values grouped by recorded payment status. Payment statuses are current, not snapshots at the range end. Partial payments and due dates are not tracked, so pending value is not an aged or partially settled balance. Unknown payment status is shown separately and must be verified.')
        if d['unassigned_transaction_count']:
            self.section('Sales without a customer account')
            self.table(['Transactions', 'Invoice value'], [[
                d['unassigned_transaction_count'], money(d['unassigned_revenue'], self.currency),
            ]], [.50, .50], numeric=(0, 1))
            self.note('Sales without a linked customer account, including imported source labels, are shown separately and excluded from customer totals. Customer totals reflect the sales visible to the requesting account.')

    def forecast(self):
        d = self.data
        if d.get('range_note'):
            self.note(d['range_note'])
        regular = d.get('regular') or {}
        ready = d.get('status') == 'ready' and regular.get('predicted_revenue') is not None
        self.cards([
            ('Forecast status', 'Available' if ready else 'Unavailable'),
            ('Forecast period', label(d.get('period', 'monthly'))),
            ('Regular dairy forecast', money(regular['predicted_revenue'], self.currency) if ready else 'Not available'),
        ])
        if ready:
            point = next(iter(d.get('forecast') or []), {})
            if point.get('date') and point.get('end_date'):
                self.note(f"Forecast dates: {point['date']} to {point['end_date']}.")
            self.note('Estimated regular dairy sales only; milk feeding is not included.')
        else:
            self.note('Recent sales are needed to provide an updated forecast.'
                      if d.get('status') == 'stale_data'
                      else 'No forecast is available for the selected period.')
            if d.get('data_end'):
                self.note(f"Most recent recorded sale: {d['data_end']}.")

    def paginated_story(self):
        """Reserve a section heading plus the first meaningful content block.

        Tables may span many pages: reserve their repeated headers and first
        data row, rather than trying to keep the entire table on one page.
        """
        story = []
        # SimpleDocTemplate's frame has six points of padding on each side.
        width = self.content_width - 12
        for index, part in enumerate(self.story):
            if isinstance(part, Paragraph) and part.style is self.styles['section']:
                following = next((item for item in self.story[index + 1:]
                                  if not isinstance(item, Spacer)), None)
                if following is not None and not isinstance(following, PageBreak):
                    _, heading_height = part.wrap(width, self.page_size[1])
                    _, content_height = following.wrap(width, self.page_size[1])
                    if isinstance(following, Table):
                        # wrap() resolves the actual heights of wrapped cells.
                        # Both standard tables and the ledger's two-row header
                        # must have room for at least one data row beneath them.
                        repeated = following.repeatRows
                        header_rows = repeated if isinstance(repeated, int) else max(repeated, default=-1) + 1
                        content_height = sum(following._rowHeights[:header_rows + 1])
                    gap = 0
                    # Include any spacers immediately after this heading.
                    for item in self.story[index + 1:]:
                        if not isinstance(item, Spacer):
                            break
                        gap += item.height
                    required = (part.getSpaceBefore() + heading_height + part.getSpaceAfter()
                                + gap + following.getSpaceBefore() + content_height)
                    story.append(CondPageBreak(required))
            story.append(part)
        return story

    def render(self):
        self.header()
        {'daily_sales': self.sales, 'weekly_sales': self.sales, 'monthly_sales': self.sales,
         'inventory': self.inventory, 'sarima_forecast': self.forecast,
         'customer': self.customer}[self.type]()
        buffer = BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=self.page_size, leftMargin=MARGIN,
            rightMargin=MARGIN, topMargin=17 * mm, bottomMargin=19 * mm,
            title=f'{self.brand} — {self.type.replace("_", " ").title()}', author=self.brand)
        document.build(self.paginated_story(), canvasmaker=lambda *args, **kwargs: FooterCanvas(
            *args, brand=self.brand, **kwargs))
        buffer.seek(0)
        return buffer


def generate_pdf(report_type, data):
    return Report(report_type, data).render()
