from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    details: Any | None = None


class ApiResponse(BaseModel, Generic[T]):
    """Standard envelope returned by every endpoint.

    Success: success=true,  data=<payload>, error=null
    Failure: success=false, data=null,      error={code, details}
    """

    statusCode: int
    success: bool
    message: str
    data: T | None = None
    error: ErrorBody | None = None
