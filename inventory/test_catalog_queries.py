from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from django.test import TestCase
from django.utils import timezone
from inventory.models import Category, Product, ProductBatch, FEFOConf
from inventory.views.product_views import ProductViewSet
from inventory.views.productbatch_views import ProductBatchViewSet
from inventory.serializers import ProductSerializer, ProdBatchSerializer


class CatalogQueryTests(TestCase):
    def test_catalog_and_nested_batches_use_bounded_queries_and_exclude_expired_stock(self):
        category=Category.objects.create(name='Milk')
        for i in range(12):
            product=Product.objects.create(name=f'Milk {i}',category=category,unit='liter',unit_price=50,shelf_life=7)
            for offset,qty in [(2,10),(-1,5)]:
                ProductBatch.objects.create(product=product,batch_number=f'{i}-{offset}',
                    initial_quantity=qty,remaining_quantity=qty,status='available',
                    expiration_date=timezone.localdate()+timedelta(days=offset))
        for view_type,serializer,nested in [(ProductViewSet,ProductSerializer,False),(ProductBatchViewSet,ProdBatchSerializer,True)]:
            view=view_type();view.action='list'
            view.request=SimpleNamespace(user=SimpleNamespace(role='admin'),query_params={})
            with self.assertNumQueries(2):
                data=serializer(view.get_queryset(),many=True).data
            self.assertEqual(len(data),24 if nested else 12)
            self.assertTrue(all(Decimal(str((r['product'] if nested else r)['total_stock']))==10 for r in data))

        from inventory.services.stock_check_service import check_product_expiration
        FEFOConf.get_config()
        with self.assertNumQueries(3):
            data=ProdBatchSerializer(check_product_expiration(),many=True).data
        self.assertEqual(len(data),12)
        self.assertTrue(all(Decimal(str(r['product']['total_stock']))==10 for r in data))
