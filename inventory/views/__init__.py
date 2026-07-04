from .product_views import ProductViewSet
from .productbatch_views import ProductBatchViewSet
from .productstock_views import LowStockProductView, ExpiringProductView
from .ingredient_views import IngredientViewSet
from .ingredientbatch_views import IngredientBatchViewSet 
from .ingredientstock_views import LowStockIngredientView, ExpiringIngredientView
from .category_views import CategoryViewSet
from .supplier_view import SupplierViewSet

# Naming convention thingy so you won't get confused later
# XXXXXViewSet = (viewset.ModelViewSet)
# XXXXXView = (APIView)