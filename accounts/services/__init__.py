from . import registration_service
from . import auth_service
from . import profile_service
from . import admin_service
class _UserServiceFacade:
    register_user = staticmethod(registration_service.register_user)
    change_password = staticmethod(auth_service.change_password)
    forgot_password = staticmethod(auth_service.forgot_password)
    logout = staticmethod(auth_service.logout)
    get_user = staticmethod(profile_service.get_user)
    update_own_profile = staticmethod(profile_service.update_own_profile)
    get_user_detail = staticmethod(profile_service.get_user_detail)
    update_user = staticmethod(admin_service.update_user)
    deactivate_user = staticmethod(admin_service.deactivate_user)


user_service = _UserServiceFacade()