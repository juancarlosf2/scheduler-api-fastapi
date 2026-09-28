"""Compatibility aggregate for host workspace feature routers."""
from fastapi import APIRouter
from app.features.profiles.workspace_api import router as profiles_router
from app.features.bookings.workspace_api import router as bookings_router
from app.features.contacts.workspace_api import router as contacts_router
from app.features.onboarding.workspace_api import router as onboarding_router
from app.features.workflows.workspace_api import router as workflows_router

router = APIRouter()
for feature_router in (profiles_router, bookings_router, contacts_router, onboarding_router, workflows_router):
    router.include_router(feature_router)
