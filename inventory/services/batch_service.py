from . import fefo_service, stock_check_service, adjustment_service


class BatchService:
    deduct_product_batch = staticmethod(fefo_service.deduct_product_batch)
    check_product_stock = staticmethod(stock_check_service.check_product_stock)
    check_product_expiration = staticmethod(stock_check_service.check_product_expiration)
    create_stock_adjustment = staticmethod(adjustment_service.create_stock_adjustment)
    reconcile = staticmethod(adjustment_service.reconcile)
