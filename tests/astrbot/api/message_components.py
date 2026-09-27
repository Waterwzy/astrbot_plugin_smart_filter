"""Stand-in for ``astrbot.api.message_components``.

Component classes the plugin's ``MessageChain`` usage relies on, mirroring the
real layout where ``At``/``Plain`` live in ``astrbot.api.message_components``
and the chain itself lives in ``astrbot.api.event``.
"""


class At:
    """Stand-in for the ``At`` message component.

    Mirrors the real component: ``name`` falls back to ``qq``, and ``text`` is
    the literal a platform renders when the component is sent.
    """

    def __init__(self, qq="", name=""):
        self.qq = str(qq)
        self.name = str(name) if name else str(qq)

    @property
    def text(self):
        return f"@{self.name} "

    def __str__(self):
        return self.text

    def __repr__(self):
        return f"At(qq={self.qq!r}, name={self.name!r})"


class Plain:
    """Stand-in for the ``Plain`` message component."""

    def __init__(self, text=""):
        self.text = str(text)

    def __str__(self):
        return self.text

    def __repr__(self):
        return f"Plain({self.text!r})"


__all__ = ["At", "Plain"]
