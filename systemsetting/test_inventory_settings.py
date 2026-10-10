from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import (
    Category, FEFOConf, Ingredient, IngredientBatch, Product, ProductBatch,
)
from inventory.services.batch_service import BatchService
from .tests import make_user


class InventorySettingsTests(TestCase):
    url = '/settings/inventory/'

    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('inventorysettingsadmin', role='admin')
        self.client.force_authenticate(self.admin)

    def test_defaults_and_settings_overview(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['low_stock_threshold'], 20)
        self.assertEqual(response.data['near_expiry_threshold'], 7)
        self.assertEqual(self.client.get('/settings/').data['inventory'], response.data)

    def test_updates_share_existing_inventory_configuration(self):
        for method in ('put', 'patch'):
            response = getattr(self.client, method)(self.url, {
                'low_stock_threshold': 25,
                'near_expiry_threshold': 10,
                'critical_expiry_threshold': 2,
            }, format='json')
            self.assertEqual(response.status_code, 200)
        config = FEFOConf.get_config()
        self.assertEqual(config.low_stock_threshold, 25)
        self.assertEqual(config.near_expiry_threshold, 10)
        self.assertEqual(FEFOConf.objects.count(), 1)
        self.assertEqual(
            self.client.get('/inventory/fefo-config/1/').data,
            self.client.get(self.url).data,
        )

    def test_permissions(self):
        for user, expected in ((None, 401), (make_user('inventorystaff'), 403)):
            self.client.force_authenticate(user)
            for method in ('get', 'put', 'patch'):
                with self.subTest(user=user, method=method):
                    response = getattr(self.client, method)(
                        self.url, {'low_stock_threshold': 99}, format='json',
                    )
                    self.assertEqual(response.status_code, expected)
        self.assertFalse(FEFOConf.objects.exists())

    def test_invalid_thresholds_do_not_change_configuration(self):
        for payload in (
            {'low_stock_threshold': -1},
            {'near_expiry_threshold': -1},
            {'near_expiry_threshold': 2},
            {'low_stock_threshold': 1.5},
            {'near_expiry_threshold': None},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(self.url, payload, format='json')
                self.assertEqual(response.status_code, 400)
        config = FEFOConf.get_config()
        self.assertEqual(config.low_stock_threshold, 20)
        self.assertEqual(config.near_expiry_threshold, 7)

    def test_updated_thresholds_control_product_and_ingredient_alerts(self):
        product = Product.objects.create(
            category=Category.objects.create(name='Alerts'), name='Milk',
            unit='liter', unit_price=10, shelf_life=30,
        )
        ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=10, shelf_life=30,
        )
        for model, relation in (
            (ProductBatch, {'product': product}),
            (IngredientBatch, {'ingredient': ingredient}),
        ):
            model.objects.create(
                **relation, batch_number='ALERT-BATCH', unit_price=10,
                initial_quantity=Decimal('18'), remaining_quantity=Decimal('18'),
                expiration_date=timezone.localdate() + timedelta(days=8),
            )
        for stock_check in (BatchService.check_product_stock, BatchService.check_ingredient_stock):
            self.assertEqual(len(stock_check()), 1)
        for expiry_check in (BatchService.check_product_expiration, BatchService.check_ingredient_expiration):
            self.assertFalse(expiry_check().exists())

        response = self.client.patch(self.url, {
            'low_stock_threshold': 17, 'near_expiry_threshold': 8,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        for stock_check in (BatchService.check_product_stock, BatchService.check_ingredient_stock):
            self.assertEqual(stock_check(), [])
        for expiry_check in (BatchService.check_product_expiration, BatchService.check_ingredient_expiration):
            self.assertEqual(expiry_check().count(), 1)

        for url, item in (('/inventory/products/', product), ('/inventory/ingredients/', ingredient)):
            self.assertEqual(self.client.get(f'{url}{item.pk}/').data['low_stock_threshold'], 17)
            for value in (0, 19, None):
                with self.subTest(url=url, value=value):
                    response = self.client.patch(
                        f'{url}{item.pk}/', {'low_stock_threshold': value}, format='json',
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('low_stock_threshold', response.data)

        # Zero is supported globally and applies to every item.
        response = self.client.patch(self.url, {'low_stock_threshold': 0}, format='json')
        self.assertEqual(response.status_code, 200)
        for stock_check in (BatchService.check_product_stock, BatchService.check_ingredient_stock):
            self.assertEqual(stock_check(), [])

    def test_item_creation_rejects_threshold_overrides(self):
        category = Category.objects.create(name='Creation')
        for url, payload in (
            ('/inventory/products/', {
                'category_id': category.pk, 'name': 'Milk', 'unit': 'liter',
                'unit_price': 10, 'shelf_life': 7,
            }),
            ('/inventory/ingredients/', {
                'name': 'Raw Milk', 'unit': 'liter', 'unit_price': 10, 'shelf_life': 7,
            }),
        ):
            for value in (0, 10, None):
                response = self.client.post(url, {**payload, 'low_stock_threshold': value}, format='json')
                self.assertEqual(response.status_code, 400)
            response = self.client.post(url, payload, format='json')
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.data['low_stock_threshold'], 20)
