class DomainError(Exception):
    """可安全呈現給用戶的領域錯誤。"""


class NotFoundError(DomainError):
    """找不到指定資源。"""


class PermissionDeniedError(DomainError):
    """目前用戶沒有操作權限。"""


class InvalidStateError(DomainError):
    """對話或資料狀態不允許目前操作。"""


class ConflictError(DomainError):
    """資料已由另一個操作更新。"""
