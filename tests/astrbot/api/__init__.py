"""Stand-in for ``astrbot.api``."""


class AstrBotConfig(dict):
    """AstrBotConfig behaves like a dict in every place the plugin uses it."""


class _Logger:
    """Records log calls so tests can assert on them if needed."""

    def __init__(self):
        self.records = []

    def _log(self, level, message):
        self.records.append((level, str(message)))

    def info(self, message):
        self._log("info", message)

    def warning(self, message):
        self._log("warning", message)

    def error(self, message):
        self._log("error", message)

    def debug(self, message):
        self._log("debug", message)


logger = _Logger()
