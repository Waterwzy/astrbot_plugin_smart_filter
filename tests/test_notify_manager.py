"""Regression tests for the notify @ id feature (``notify_at_ids``).

The ``notify_at_ids`` config item was added in v2.6.1 and prepends one ``At``
component per configured id to every notification that leaves the plugin.
These tests pin down:

- the untouched pass-through when the list is empty (``_conf_schema.json``
  declares no default, and AstrBot fills the key in with an empty list);
- the exact component layout when it is configured;
- that the immediate send path, the debug path and the retry path all use it.

Configs used here mirror what AstrBot hands the plugin, i.e. every key declared
in ``_conf_schema.json`` is present, so no defensive fallback is exercised.
"""

import pytest
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import At
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, LLMResponse
from astrbot_plugin_smart_filter_under_test.core.manager.notify_manager import (
    NotifyManager,
)
from astrbot_plugin_smart_filter_under_test.main import SmartFilter

ZERO_WIDTH_LINE = "\u200b\n\u200b"


def _plugin(config, context=None):
    """Build a SmartFilter synchronously (no ``initialize()``, no ban list IO)."""
    return SmartFilter(
        context if context is not None else Context(), AstrBotConfig(config)
    )


@pytest.fixture(autouse=True)
def _reset_notify_singleton():
    """``NotifyManager`` is a process-wide singleton; isolate it per test."""
    NotifyManager._instance = None
    yield
    NotifyManager._instance = None


@pytest.fixture
async def notify_manager(config):
    """A ``NotifyManager`` bound to a fresh plugin, torn down afterwards."""
    manager = NotifyManager(_plugin(config))
    yield manager
    manager._stop_retry_task()


async def test_add_at_ids_empty_returns_same_chain(notify_manager):
    chain = MessageChain().message("通知正文")

    assert notify_manager._add_at_in_msg(chain) is chain
    assert str(chain) == "通知正文"


async def test_add_at_ids_prepends_mentions_in_configured_order(config):
    config["notify_config"]["notify_at_ids"] = ["111", "222"]
    manager = NotifyManager(_plugin(config))

    chain = manager._add_at_in_msg(MessageChain().message("通知正文"))

    assert chain.at_mentions() == ["111", "222"]
    assert [type(c).__name__ for c in chain.chain] == [
        "At",
        "At",
        "Plain",
        "Plain",
    ]
    # 两次 @ 之后插入零宽换行，避免正文与 @ 挤在同一行
    assert chain.chain[2].text == ZERO_WIDTH_LINE
    assert chain.chain[3].text == "通知正文"

    manager._stop_retry_task()


async def test_add_at_ids_does_not_mutate_original_chain(config):
    config["notify_config"]["notify_at_ids"] = ["111"]
    manager = NotifyManager(_plugin(config))
    original = MessageChain().message("通知正文")

    chain = manager._add_at_in_msg(original)

    assert chain is not original
    assert original.at_mentions() == []
    assert len(original.chain) == 1

    manager._stop_retry_task()


async def test_send_once_with_at_ids_sends_mention_chain(config):
    config["notify_config"]["notify_at_ids"] = ["111", "222"]
    context = Context()
    manager = NotifyManager(_plugin(config, context))

    assert await manager.send_once("通知正文", False) is True

    umo, chain = context.sent_messages[0]
    assert umo == config["notify_config"]["notify_umo"]
    assert chain.at_mentions() == ["111", "222"]
    assert "通知正文" in str(chain)

    manager._stop_retry_task()


async def test_send_once_without_at_ids_sends_plain_chain(notify_manager):
    context = notify_manager._plugin.context

    assert await notify_manager.send_once("通知正文", False) is True

    _, chain = context.sent_messages[0]
    assert chain.at_mentions() == []
    assert [type(c).__name__ for c in chain.chain] == ["Plain"]
    assert str(chain) == "通知正文"


async def test_send_once_at_ids_empty_list_sends_plain_chain(config):
    config["notify_config"]["notify_at_ids"] = []
    context = Context()
    manager = NotifyManager(_plugin(config, context))

    assert await manager.send_once("通知正文", False) is True

    _, chain = context.sent_messages[0]
    assert chain.at_mentions() == []
    assert str(chain) == "通知正文"

    manager._stop_retry_task()


async def test_send_once_failure_with_at_ids_still_queues_retry(config):
    config["notify_config"]["notify_at_ids"] = ["111"]
    context = Context()
    context.fail_send = True
    manager = NotifyManager(_plugin(config, context))

    assert await manager.send_once("通知正文", False) is False

    pending = await manager.get_pending()
    assert [item["content"] for item in pending] == ["通知正文"]

    manager._stop_retry_task()


async def test_add_notify_routes_through_at_ids(config):
    """``add_notify`` is the public entry point used by both send paths."""
    config["notify_config"]["notify_at_ids"] = ["111"]
    context = Context()
    manager = NotifyManager(_plugin(config, context))

    await manager.add_notify("通知正文")

    _, chain = context.sent_messages[0]
    assert chain.at_mentions() == ["111"]
    assert "通知正文" in str(chain)

    manager._stop_retry_task()


async def test_retry_send_once_still_carries_at_ids(config):
    """The retry loop re-sends through the same @-aware path."""
    config["notify_config"]["notify_at_ids"] = ["111"]
    context = Context()
    manager = NotifyManager(_plugin(config, context))
    context.fail_send = True
    await manager.add_notify("通知正文")
    assert len(await manager.get_pending()) == 1

    context.fail_send = False
    # retry_task 对队首条目执行的就是这次调用
    assert await manager.send_once("通知正文", True) is True

    _, chain = context.sent_messages[0]
    assert chain.at_mentions() == ["111"]
    assert "通知正文" in str(chain)

    manager._stop_retry_task()


# ------------------------------------------------------------------ end to end


async def test_check_request_notify_carries_at_ids(make_plugin, config):
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    config["notify_config"]["notify_at_ids"] = ["111", "222"]
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))

    await plugin.check_request(event, ProviderRequest())

    assert len(plugin.context.sent_messages) == 1
    umo, chain = plugin.context.sent_messages[0]
    assert umo == "admin:umo"
    assert chain.at_mentions() == ["111", "222"]
    assert "骂人" in str(chain)
    await plugin.terminate()


async def test_check_request_debug_mode_carries_at_ids(make_plugin, config):
    config["filter_config"]["debug_mode"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    config["notify_config"]["notify_at_ids"] = ["111"]
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(
        LLMResponse(completion_text="block", raw_completion="raw-debug-output")
    )

    await plugin.check_request(event, ProviderRequest())

    _, chain = plugin.context.sent_messages[0]
    assert chain.at_mentions() == ["111"]
    assert "[DEBUG]raw content:raw-debug-output" in str(chain)
    await plugin.terminate()


async def test_check_request_notify_without_at_ids_sends_plain(make_plugin, config):
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))

    await plugin.check_request(event, ProviderRequest())

    _, chain = plugin.context.sent_messages[0]
    assert not any(isinstance(component, At) for component in chain.chain)
    await plugin.terminate()


async def test_notify_manager_rebinds_to_new_plugin_config(make_plugin, config):
    """Reloading the plugin must rebind the manager to the new config.

    ``NotifyManager`` is a process-wide singleton, so reloading re-runs
    ``__init__`` on the *same* object; what must change is the bound plugin
    and therefore every config value it reads (umo and at-id list alike).
    """
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    config["notify_config"]["notify_at_ids"] = ["111"]
    plugin = await make_plugin(config)
    manager = plugin._notify_manager
    assert manager.admin_umo == "admin:umo"
    assert manager._add_at_in_msg(MessageChain().message("x")).at_mentions() == ["111"]

    config["notify_config"]["notify_umo"] = "other:umo"
    config["notify_config"]["notify_at_ids"] = ["999"]
    plugin2 = await make_plugin(config)

    assert plugin2._notify_manager is manager
    assert manager._plugin is plugin2
    assert manager.admin_umo == "other:umo"
    chain = manager._add_at_in_msg(MessageChain().message("x"))
    assert chain.at_mentions() == ["999"]
    await plugin2.terminate()
