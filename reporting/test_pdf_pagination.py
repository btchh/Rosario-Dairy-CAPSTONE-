from io import BytesIO

from django.core.cache import cache
from django.test import TestCase
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

from reporting.pdf import MARGIN, Report


class ObservedDocument(SimpleDocTemplate):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.drawn = []

    def afterFlowable(self, part):
        if isinstance(part, Paragraph):
            self.drawn.append(('paragraph', self.page, part.text))
        elif isinstance(part, Table):
            def text(cell):
                if isinstance(cell, Paragraph):
                    return cell.text
                if isinstance(cell, (tuple, list)):
                    return ' '.join(text(item) for item in cell)
                return str(cell)
            rows = [[text(cell) for cell in row] for row in part._cellvalues]
            self.drawn.append(('table', self.page, rows))


class ReportPaginationTests(TestCase):
    def setUp(self):
        cache.clear()

    def report_with_space_left(self, remaining, report_type='customer'):
        report = Report(report_type, {})
        frame_height = report.page_size[1] - 17 * mm - 19 * mm - 12
        report.story.append(Spacer(1, frame_height - remaining))
        return report

    def build(self, report):
        doc = ObservedDocument(BytesIO(), pagesize=report.page_size,
                               leftMargin=MARGIN, rightMargin=MARGIN,
                               topMargin=17 * mm, bottomMargin=19 * mm)
        doc.build(report.paginated_story())
        return doc.drawn

    def test_heading_moves_with_table_headers_and_first_row(self):
        for report_type in ('daily_sales', 'customer'):
            with self.subTest(report_type=report_type):
                report = self.report_with_space_left(45, report_type)
                report.section('Product contribution')
                report.table(['Product', 'Quantity'], [['Milk', '2']], [.75, .25])
                drawn = self.build(report)
                heading = next(event for event in drawn if event[0] == 'paragraph')
                table = next(event for event in drawn if event[0] == 'table')
                self.assertEqual(heading[1], 2)
                self.assertEqual(table[1], heading[1])
                self.assertEqual(table[2][1], ['Milk', '2'])

    def test_long_tables_start_in_available_space_and_continue_with_headers(self):
        report = self.report_with_space_left(125)
        report.section('Customer summary')
        rows = [[f'ROW-{index:03}', str(index)] for index in range(60)]
        report.table(['Customer', 'Purchases'], rows, [.75, .25])
        drawn = self.build(report)
        heading = next(event for event in drawn if event[0] == 'paragraph')
        tables = [event for event in drawn if event[0] == 'table']
        self.assertEqual(heading[1], 1)
        self.assertEqual(tables[0][1], heading[1])
        self.assertGreater(len(tables), 1)
        for event in tables:
            self.assertEqual(event[2][0], ['Customer', 'Purchases'])
            self.assertGreater(len(event[2]), 1)
        actual = [row for event in tables for row in event[2][1:]]
        self.assertEqual(actual, rows)

    def test_two_row_ledger_header_stays_with_heading_and_first_product(self):
        report = self.report_with_space_left(60, 'inventory')
        report.section('Daily stock ledger')
        rows = [[report.p(text) for text in row] for row in [
            ['Stocks', 'Allocations'], ['Beginning', 'Outlet'], ['Milk', '2'],
        ]]
        table = Table(rows, colWidths=[report.content_width / 2] * 2, repeatRows=2)
        report.story.append(table)
        drawn = self.build(report)
        heading = next(event for event in drawn if event[0] == 'paragraph')
        table_event = next(event for event in drawn if event[0] == 'table')
        self.assertEqual(heading[1], 2)
        self.assertEqual(table_event[1], 2)
        self.assertEqual(table_event[2], [['Stocks', 'Allocations'], ['Beginning', 'Outlet'], ['Milk', '2']])

    def test_heading_stays_with_wrapped_first_row(self):
        report = self.report_with_space_left(95)
        report.section('Product contribution')
        description = 'A long product description with additional details ' * 10
        report.table(['Description', 'Quantity'], [[description, '2']], [.40, .60])
        drawn = self.build(report)
        heading = next(event for event in drawn if event[0] == 'paragraph')
        table = next(event for event in drawn if event[0] == 'table')
        self.assertEqual(heading[1], 2)
        self.assertEqual(table[1], heading[1])

    def test_empty_section_heading_stays_with_explanation(self):
        report = self.report_with_space_left(25)
        report.section('Customer summary')
        report.table(['Customer'], [], [1], empty='No customer transactions recorded in this period.')
        paragraphs = [event for event in self.build(report) if event[0] == 'paragraph']
        self.assertEqual([event[1] for event in paragraphs], [2, 2])
