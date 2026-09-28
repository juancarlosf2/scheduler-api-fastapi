"""Domain errors independent of HTTP routing."""


class DomainError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
