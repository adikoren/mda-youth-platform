from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Domain error with a stable machine code and a Hebrew user message."""

    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


def not_found(code: str = "not_found", message: str = "לא נמצא") -> AppError:
    # Used both for missing objects and for objects the actor may not know exist.
    return AppError(404, code, message)


def forbidden(code: str = "forbidden", message: str = "אין הרשאה לפעולה זו") -> AppError:
    return AppError(403, code, message)


def conflict(code: str, message: str, extra: dict | None = None) -> AppError:
    return AppError(409, code, message, extra)


def bad_request(code: str, message: str) -> AppError:
    return AppError(400, code, message)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=jsonable_encoder({"code": exc.code, "message": exc.message, **exc.extra}),
    )
