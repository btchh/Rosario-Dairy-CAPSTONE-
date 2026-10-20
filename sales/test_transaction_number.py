from django.test import SimpleTestCase

from sales.models import Transaction
from sales.serializers.order_serializer import OrderListTransactionSerializer
from sales.serializers.transaction_serializer import TransactionSerializer


class TransactionNumberTests(SimpleTestCase):
    def test_full_and_compact_serializers_preserve_recorded_format(self):
        for serializer_class in (TransactionSerializer, OrderListTransactionSerializer):
            field = serializer_class().fields['transaction_number']
            for number in ('SI0608', 'SI0001', 'SI-12', '00123'):
                with self.subTest(serializer=serializer_class.__name__, number=number):
                    self.assertEqual(field.to_representation(Transaction(source_invoice_number=number)), number)
            self.assertIsNone(field.to_representation(Transaction(source_invoice_number='')))

    def test_transaction_number_cannot_be_overwritten_by_clients(self):
        for serializer_class in (TransactionSerializer, OrderListTransactionSerializer):
            self.assertTrue(serializer_class().fields['transaction_number'].read_only)
