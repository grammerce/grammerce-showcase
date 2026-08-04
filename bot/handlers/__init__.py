from .admin import router as admin_router
from .contact import router as contact_router
from .feedback import router as feedback_router
from .menu import router as menu_router
from .promo import router as promo_router
from .registration import router as registration_router

__all__ = ["menu_router", "registration_router", "promo_router", "contact_router", "admin_router", "feedback_router"]
