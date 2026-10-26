from typing import Any


class AppError(Exception):
    """An expected, user-facing error. Rendered by the handlers as the standard error envelope."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class NotFoundError(AppError):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(404, code, message, details)


class ConflictError(AppError):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(409, code, message, details)


class UnprocessableError(AppError):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(422, code, message, details)


class PlaybookConfigError(Exception):
    """Raised at startup when a playbook JSON file is invalid. Never swallowed."""
