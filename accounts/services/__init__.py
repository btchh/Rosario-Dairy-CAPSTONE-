from . import registration_service, auth_service, profile_service, admin_service

class _UserServiceFacade:
    register_user = staticmethod(registration_service.register_user)
    change_password = staticmethod(auth_service.change_password)
    forgot_password = staticmethod(auth_service.forgot_password)
    logout = staticmethod(auth_service.logout)
    get_user = staticmethod(profile_service.get_user)
    get_user_detail = staticmethod(profile_service.get_user_detail)
    update_user = staticmethod(admin_service.update_user)
    deactivate_user = staticmethod(admin_service.deactivate_user)

user_service = _UserServiceFacade()