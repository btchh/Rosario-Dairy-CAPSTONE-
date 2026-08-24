from . import checkout_service
from . import order_service
from . import report_service


class SalesService:
    checkout = staticmethod(checkout_service.checkout)

    place_order = staticmethod(order_service.place_order)
    void_fulfilled_order = staticmethod(order_service.void_fulfilled_order)

    get_revenue_report = staticmethod(report_service.get_revenue_report)
    get_best_sellers = staticmethod(report_service.get_best_sellers)
    get_sales_by_category = staticmethod(report_service.get_sales_by_category)

    PERIOD_TRUNC = report_service.PERIOD_TRUNC