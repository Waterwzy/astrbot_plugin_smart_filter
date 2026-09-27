"""Stand-in for ``astrbot.api.event``."""

import types

from ..message_components import At, Plain
from . import filter
from .filter import (  # noqa: F401
    PermissionType,
    command_group,
    on_llm_request,
    permission_type,
)

__all__ = [
    "AstrMessageEvent",
    "At",
    "MessageChain",
    "Plain",
    "filter",
    "PermissionType",
    "command_group",
    "on_llm_request",
    "permission_type",
]


class MessageChain:
    """Chainable message container.

    The real AstrBot ``MessageChain`` subclasses ``list``: components live in
    ``self.chain`` and every builder method returns ``self``. The stub keeps
    that surface (``.chain``, ``.message()``, ``.at()``) because production
    code mixes ``MessageChain().message(...)`` with ``chain.extend(...)``.
    """

    def __init__(self):
        self.chain = []

    def message(self, msg):
        self.chain.append(Plain(msg))
        return self

    def at(self, name, qq):
        self.chain.append(At(qq=qq, name=name))
        return self

    def plain_text(self):
        """Concatenated text of every component (``At`` renders as ``@name``)."""
        return "".join(str(component) for component in self.chain)

    def at_mentions(self):
        """The ``qq`` ids of every ``At`` component, in order."""
        return [c.qq for c in self.chain if isinstance(c, At)]

    def __str__(self):
        return self.plain_text()

    def __repr__(self):
        return f"MessageChain({self.chain!r})"


class AstrMessageEvent:
    """Configurable fake message event used across the test suite."""

    def __init__(
        self,
        sender_id="",
        message="",
        platform="test_platform",
        group_id=None,
        group=None,
        umo=None,
    ):
        self.platform_meta = types.SimpleNamespace(name=platform)
        self.unified_msg_origin = umo or f"test:{platform}:{sender_id}"
        self._sender_id = sender_id
        self._message = message
        self._group_id = group_id
        self._group = group
        self.stopped = False
        self.sent = []

    def get_sender_id(self):
        return self._sender_id

    def get_message_str(self):
        return self._message

    def get_group_id(self):
        return self._group_id

    def get_group(self):
        return self._group

    def get_platform_name(self):
        return self.platform_meta.name

    def stop_event(self):
        self.stopped = True

    async def send(self, chain):
        self.sent.append(chain)
