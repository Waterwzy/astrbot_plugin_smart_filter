"""Stand-in for ``astrbot.api.web``."""


class _Request:
    def __init__(self):
        self._json_data = None

    async def json(self, default=None):
        if self._json_data is not None:
            return self._json_data
        return default if default is not None else {}


request = _Request()


def json_response(data):
    return {"status": "ok", "data": data}


def error_response(message, status_code=400):
    return {"status": "error", "message": message, "code": status_code}
