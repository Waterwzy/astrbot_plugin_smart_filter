"""Stand-in for ``astrbot.api.event``."""

import types

from . import filter
from .filter import (  # noqa: F401
    PermissionType,
    command_group,
    on_llm_request,
    permission_type,
)

__all__ = [
    "AstrMessageEvent",
    "MessageChain",
    "filter",
    "PermissionType",
    "command_group",
    "on_llm_request",
    "permission_type",
]


class MessageChain:
    """Chainable message container; ``MessageChain().message(text)``."""

    def __init__(self):
        self.messages = []

    def message(self, msg):
        self.messages.append(str(msg))
        return self

    def __str__(self):
        return "".join(self.messages)


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
