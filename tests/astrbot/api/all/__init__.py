"""Stand-in for ``astrbot.api.all`` (re-exports)."""

from .. import AstrBotConfig
from ..event import AstrMessageEvent, MessageChain  # noqa: F401

__all__ = ["AstrBotConfig", "AstrMessageEvent", "MessageChain"]
