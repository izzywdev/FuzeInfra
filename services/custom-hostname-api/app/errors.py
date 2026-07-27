"""Typed API errors that serialize to the frozen `Error` schema.

Every failure a consumer can act on gets a stable machine-readable `error`
code — consumers branch on the code, never on the message.
"""

from __future__ import annotations

from fastapi import HTTPException


class ApiError(HTTPException):
    """An HTTPException whose detail is already the frozen `Error` body."""

    def __init__(self, status_code: int, code: str, message: str, detail: str | None = None):
        super().__init__(
            status_code=status_code,
            detail={"error": code, "message": message, "detail": detail},
        )


def bad_request(message: str, detail: str | None = None) -> ApiError:
    return ApiError(400, "bad_request", message, detail)


def unauthorized(message: str = "Missing or invalid bearer token.") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str, detail: str | None = None) -> ApiError:
    return ApiError(403, "forbidden", message, detail)


def not_found(message: str = "No such custom hostname.") -> ApiError:
    return ApiError(404, "not_found", message)


def validation_error(message: str, detail: str | None = None) -> ApiError:
    return ApiError(422, "validation_error", message, detail)


def upstream_error(message: str, detail: str | None = None) -> ApiError:
    return ApiError(502, "upstream_error", message, detail)


def quota_exceeded(message: str, detail: str | None = None) -> ApiError:
    return ApiError(429, "quota_exceeded", message, detail)
