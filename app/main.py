"""Application composition and health check."""

from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from app.core.dependencies import session_factory
from app.core.errors import DomainError
from app.core.http import get_service
from app.features.hosts.api import router as hosts_router
from app.features.calendars.api import router as calendar_booking_router
from app.features.availability.api import router as availability_router
from app.features.event_types.api import router as event_types_router
from app.features.bookings.public_api import router as public_booking_router
from app.calendar_routes import router as calendar_router
from app.notification_routes import router as notification_router
from app.workos_auth import router as workos_auth_router
from app.workspace_routes import router as workspace_router

app = FastAPI(
    title="Scheduling API Starter",
    description="Self-hostable scheduling API with host-owned event types and public booking.",
    version="0.1.0",
)


@app.exception_handler(DomainError)
async def domain_error_handler(_request, error: DomainError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=error.status_code, content={"detail": str(error)})


@app.get("/health", tags=["health"])
def health():
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


for router in (
    hosts_router,
    calendar_booking_router,
    availability_router,
    event_types_router,
    public_booking_router,
    calendar_router,
    workspace_router,
    notification_router,
    workos_auth_router,
):
    app.include_router(router)

__all__ = ["app", "get_service"]
