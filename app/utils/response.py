from typing import Any

from fastapi.responses import JSONResponse


def success_response(data: Any, message: str = "OK", status_code: int = 200) -> dict:
    """Build the success envelope. Routes return this; FastAPI validates `data` via response_model."""
    return {
        "statusCode": status_code,
        "success": True,
        "message": message,
        "data": data,
        "error": None,
    }


def error_response(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    """Build the error envelope as a ready-to-send response."""
    return JSONResponse(
        status_code=status_code,
        content={
            "statusCode": status_code,
            "success": False,
            "message": message,
            "data": None,
            "error": {"code": code, "details": details},
        },
    )
