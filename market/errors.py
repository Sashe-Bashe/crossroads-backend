class Problem(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        self.code, self.message, self.status, self.details = code, message, status, details


def require(condition, code, message, status=400):
    if not condition:
        raise Problem(code, message, status)
