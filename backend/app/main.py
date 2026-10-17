from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.access.dependencies import csrf_protect
from app.accounts.router import router as auth_router
from app.admin.router import admin as admin_router
from app.admin.router import router as people_router
from app.core.errors import AppError, app_error_handler
from app.mentoring.router import router as journal_router
from app.notifications.push_router import router as push_router
from app.accounts.temporary_router import router as temporary_router
from app.registration.router import router as registration_router
from app.scheduling.router import manage as manage_router
from app.scheduling.router import router as schedule_router


def create_app() -> FastAPI:
    app = FastAPI(title="MDA youth platform", version="0.1.0", dependencies=[Depends(csrf_protect)])
    app.add_exception_handler(AppError, app_error_handler)

    async def validation_handler(_, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": "נתונים לא תקינים",
                                                      "detail": exc.errors()[:5]})

    app.add_exception_handler(RequestValidationError, validation_handler)
    for r in (auth_router, registration_router, schedule_router, manage_router, people_router, admin_router, journal_router,
              push_router, temporary_router):
        app.include_router(r)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    return app


app = create_app()
