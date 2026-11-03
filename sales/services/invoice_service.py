"""Yearly SI allocation; invoice identity is separate from submission identity.

All application entry paths take the same year-row lock before stock locks.
Paper references may overlap automatic numbers, but cannot repeat other paper
references in their sales year. Existing legacy rows are treated as paper
references without rewriting or constraining their historical duplicates.
"""
import re

from django.db import connection
from django.db.models.functions import Trim, Upper

from sales.models import InvoiceSequence, Transaction


class InvoiceNumberConflict(ValueError):
    pass


def _initial_value(year):
    # History contains a mid-year restart and large transcription outliers.
    # Continue its latest dated plain SI rather than taking a global maximum.
    numbers = (Transaction.objects.filter(created_at__year=year)
               .exclude(source_invoice_number__isnull=True)
               .exclude(source_invoice_number='')
               .order_by('-created_at', '-pk')
               .values_list('source_invoice_number', flat=True))
    for number in numbers.iterator():
        match = re.fullmatch(r'SI([0-9]+)', number.strip(), re.IGNORECASE)
        if match and int(match[1]) < 2**63 - 1:
            return int(match[1])
    return 0


def assign_invoice(year, paper_number=None):
    """Return (recorded SI, provenance), inside the enclosing sale transaction.

    A failed sale rolls back the counter too. get_or_create handles two first
    sales racing to create a year; select_for_update locks the winning row.
    """
    if not connection.in_atomic_block:
        raise RuntimeError('Invoice allocation requires an atomic sale transaction.')
    sequence, _ = InvoiceSequence.objects.select_for_update().get_or_create(
        year=year, defaults={'last_value': lambda: _initial_value(year)},
    )
    recorded = Transaction.objects.filter(created_at__year=year).annotate(
        normalized_si=Upper(Trim('source_invoice_number')),
    )
    if paper_number and paper_number.strip():
        if recorded.exclude(invoice_number_source='automatic').filter(
                normalized_si=paper_number.strip().upper()).exists():
            raise InvoiceNumberConflict(
                f'This paper SI number is already recorded for {year}. '
                'Check Sales History before entering it again.'
            )
        return paper_number, 'manual'

    # Include paper, imported, voided and automatic numbers when skipping.
    # The persistent counter never goes backwards after cancellation/deletion.
    while sequence.last_value < 2**63 - 1:
        sequence.last_value += 1
        number = f'SI{sequence.last_value:04d}'
        if not recorded.filter(normalized_si=number).exists():
            sequence.save(update_fields=['last_value'])
            return number, 'automatic'
    raise ValueError(f'The SI sequence for {year} is exhausted.')
