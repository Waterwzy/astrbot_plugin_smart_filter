"""Stand-in for ``astrbot.api.provider``."""


class ProviderRequest:
    def __init__(self, contexts=None):
        self.contexts = contexts if contexts is not None else []
