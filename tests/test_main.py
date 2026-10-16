import json
import time

import pendulum

from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import LLMResponse
from astrbot_plugin_smart_filter_under_test.core.manager.command_manager import (
    command_manager,
)


# ---------------------------------------------------------------- basic API


async def test_create_speak_msg(make_plugin, config):
    plugin = await make_plugin(config)
    assert plugin.create_speak_msg("内容") == "【内容】"


async def test_web_apis_registered(make_plugin, config):
    plugin = await make_plugin(config)
    assert len(plugin.context.web_apis) == 4
    paths = [api[0] for api in plugin.context.web_apis]
    assert "/astrbot_plugin_smart_filter/violations/list" in paths
    assert "/astrbot_plugin_smart_filter/violations/ban" in paths


# ------------------------------------------------------------- check_user


async def test_check_user_valid_platform(make_plugin, config):
    plugin = await make_plugin(config)
    assert plugin.check_user(["test_platform"]) is None


async def test_check_user_unknown_platform(make_plugin, config):
    plugin = await make_plugin(config)
    chain = plugin.check_user(["no_such_platform"])
    assert chain is not None
    assert "不存在" in str(chain)


async def test_check_user_bad_duration(make_plugin, config):
    plugin = await make_plugin(config)
    chain = plugin.check_user(["test_platform"], "not-a-duration")
    assert chain is not None
    assert "ISO8601" in str(chain)


async def test_check_user_valid_duration(make_plugin, config):
    plugin = await make_plugin(config)
    assert plugin.check_user(["test_platform"], "PT1H") is None


# ---------------------------------------------------------------- ban_user


async def test_ban_user_success(make_plugin, config):
    plugin = await make_plugin(config)
    state, detail = await plugin.ban_user(
        "u1", "test_platform", pendulum.duration(days=1)
    )
    assert state == "Success"
    assert "年" in detail  # formatted unban time
    assert "u1" in plugin.ban_list["banners"]["test_platform"]


async def test_ban_user_already_banned(make_plugin, config):
    plugin = await make_plugin(config)
    await plugin.ban_user("u1", "test_platform", pendulum.duration(days=1))
    state, detail = await plugin.ban_user(
        "u1", "test_platform", pendulum.duration(days=1)
    )
    assert state == "Fail"
    assert "正在封禁中" in detail


async def test_ban_user_after_expiry(make_plugin, config):
    plugin = await make_plugin(config)
    plugin.ban_list["banners"]["test_platform"]["u1"] = time.time() - 10
    state, _ = await plugin.ban_user("u1", "test_platform", pendulum.duration(hours=1))
    assert state == "Success"


# ------------------------------------------------------- unban_all / refresh


async def test_unban_all_expires_bans_and_clears_records(make_plugin, config):
    plugin = await make_plugin(config)
    now = pendulum.now().timestamp()
    plugin.ban_list["banners"]["test_platform"]["expired"] = now - 10
    plugin.ban_list["banners"]["test_platform"]["active"] = now + 3600
    plugin.ban_list["prohibits"]["test_platform"]["expired"] = [
        {"word": "旧词", "time": now - 10, "show": True, "raw": ""}
    ]
    flag = await plugin.unban_all()
    assert flag is True
    assert "expired" not in plugin.ban_list["banners"]["test_platform"]
    assert "expired" not in plugin.ban_list["prohibits"]["test_platform"]
    assert "active" in plugin.ban_list["banners"]["test_platform"]


async def test_unban_all_hides_old_violations(make_plugin, config):
    config["command_config"]["check_disshow_time"] = 3
    plugin = await make_plugin(config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {
            "word": "四天前",
            "time": now - 4 * 86400,
            "show": True,
            "raw": "旧原始返回体",
        },
        {"word": "今天", "time": now - 60, "show": True, "raw": "新原始返回体"},
    ]
    flag = await plugin.unban_all()
    assert flag is True
    items = plugin.ban_list["prohibits"]["test_platform"]["u1"]
    assert items[0]["show"] is False
    assert items[1]["show"] is True


async def test_refresh_all_times_reshows_recent_violations(make_plugin, config):
    config["command_config"]["check_disshow_time"] = 3
    plugin = await make_plugin(config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "一天前", "time": now - 86400, "show": False},
        {"word": "五年前", "time": now - 5 * 365 * 86400, "show": False},
    ]
    flag = await plugin.refresh_all_times()
    assert flag is True
    items = plugin.ban_list["prohibits"]["test_platform"]["u1"]
    assert items[0]["show"] is True
    assert items[1]["show"] is False


# --------------------------------------------------------- update handlers


async def test_handle_white_list_update(make_plugin, config):
    plugin = await make_plugin(config)
    plugin.ban_list["white_list"]["test_platform"] = ["stale"]
    await plugin.handle_white_list_update(plugin.config)
    assert plugin.ban_list["white_list"]["test_platform"] == ["white_user"]
    assert plugin.ban_list["white_list"]["other_platform"] == []


async def test_handle_update_syncs_platforms(make_plugin, config):
    plugin = await make_plugin(config)
    plugin.ban_list["prohibits"]["legacy_platform"] = {"u1": []}
    plugin.ban_list["banners"]["legacy_platform"] = {}
    plugin.ban_list["white_list"]["legacy_platform"] = []
    await plugin.handle_update()
    assert "legacy_platform" not in plugin.ban_list["prohibits"]
    for key in ("test_platform", "other_platform"):
        assert key in plugin.ban_list["prohibits"]
        assert key in plugin.ban_list["banners"]
        assert key in plugin.ban_list["white_list"]
    assert plugin.ban_list["available_platforms"] == ["test_platform", "other_platform"]


# ------------------------------------------------------------- initialize


async def test_initialize_migrates_v2_3_0_data(make_plugin):
    data = {
        "available_platforms": ["test_platform"],
        "prohibits": {"test_platform": {"u1": ["旧词1", "旧词2"]}},
        "banners": {"test_platform": {}},
        "white_list": {"test_platform": []},
        "pending_notifications": [],
        "data_migrate_tag": [],
    }
    plugin = await make_plugin(data=data)
    migrated = plugin.ban_list["prohibits"]["test_platform"]["u1"]
    assert all(isinstance(item, dict) for item in migrated)
    assert migrated[0]["word"] == "旧词1"
    assert migrated[0]["show"] is True
    assert "time" in migrated[0]
    assert "v2.3.0" in plugin.ban_list["data_migrate_tag"]


async def test_initialize_migrates_v2_6_0_pending_notifications(make_plugin):
    data = {
        "available_platforms": ["test_platform"],
        "prohibits": {"test_platform": {}},
        "banners": {"test_platform": {}},
        "white_list": {"test_platform": []},
        "pending_notifications": [
            {
                "timestamp": 1700000000.0,
                "platform": "test_platform",
                "user_id": "u1",
                "message": "违规词",
                "counts": 2,
                "context_str": "上一轮上下文",
                "retry_count": 1,
            },
            {
                "timestamp": 1700000060.0,
                "platform": "test_platform",
                "user_id": "u2",
                "message": "缺省字段的旧条目",
            },
            {"id": "keep-me", "times": 0, "content": "已是新格式的条目"},
        ],
        "data_migrate_tag": ["v2.3.0"],
    }
    plugin = await make_plugin(data=data)
    assert "v2.6.0" in plugin.ban_list["data_migrate_tag"]
    pending = plugin.ban_list["pending_notifications"]
    assert len(pending) == 3
    first = pending[0]
    assert set(first.keys()) == {"id", "times", "content"}
    assert first["times"] == 1  # 继承旧条目的重试次数
    assert "违规词" in first["content"]
    assert "用户：u1" in first["content"]
    assert "总计次数：2" in first["content"]
    assert "📫上下文：上一轮上下文" in first["content"]
    second = pending[1]
    assert second["times"] == 0
    assert "缺省字段的旧条目" in second["content"]
    assert "总计次数：0" in second["content"]
    assert pending[2] == {"id": "keep-me", "times": 0, "content": "已是新格式的条目"}


async def test_initialize_resumes_pending_notifications(make_plugin, config):
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    data = {
        "available_platforms": ["test_platform"],
        "prohibits": {"test_platform": {}},
        "banners": {"test_platform": {}},
        "white_list": {"test_platform": []},
        "pending_notifications": [
            {"id": "old-1", "times": 1, "content": "停机前未发送的通知"}
        ],
        "data_migrate_tag": ["v2.3.0", "v2.6.0"],
    }
    plugin = await make_plugin(config, data=data)
    assert plugin._notify_manager is not None
    pending = await plugin._notify_manager.get_pending()
    assert len(pending) == 1
    assert pending[0]["id"] == "old-1"
    assert pending[0]["times"] == 1
    assert pending[0]["content"] == "停机前未发送的通知"
    # 恢复后数据文件中的快照被清空，防止下次初始化重复恢复
    assert plugin.ban_list["pending_notifications"] == []


async def test_initialize_compiles_skip_schema(make_plugin, config):
    plugin = await make_plugin(config)
    types = [item["type"] for item in plugin.skip_config]
    assert types == ["full", "prefix", "suffix", "regex"]
    regex_item = next(item for item in plugin.skip_config if item["type"] == "regex")
    assert regex_item["content"].fullmatch("skip123")


async def test_initialize_skips_invalid_regex(make_plugin, config):
    config["filter_config"]["skip_schema"].append(
        {"__template_key": "default", "match_type": "regex", "match_content": "("}
    )
    plugin = await make_plugin(config)
    assert len(plugin.skip_config) == 4  # the invalid regex is not added


# ------------------------------------------------------- check_request flow


async def test_check_request_passes_filter(make_plugin, config):
    plugin = await make_plugin(config)
    event = AstrMessageEvent(
        sender_id="u1", message="普通消息", platform="test_platform"
    )
    plugin.context.llm_script.append(LLMResponse(completion_text="allow"))
    await plugin.check_request(event, ProviderRequest())
    assert event.stopped is False
    assert plugin.ban_list["prohibits"]["test_platform"].get("u1") is None
    assert len(plugin.context.llm_calls) == 1


async def test_check_request_blocks_in_strict_mode(make_plugin, config):
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))
    await plugin.check_request(event, ProviderRequest())
    assert event.stopped is True
    records = plugin.ban_list["prohibits"]["test_platform"]["u1"]
    assert len(records) == 1
    assert records[0]["word"] == "骂人"
    assert records[0]["show"] is True
    assert str(event.sent[-1]) == "【您的消息不符合规范，请文明发言。】"


async def test_check_request_non_strict_mode(make_plugin, config):
    config["filter_config"]["filter_mode"] = False
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="消息", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="无关键词"))
    await plugin.check_request(event, ProviderRequest())
    assert event.stopped is False  # "block" absent -> pass in non-strict mode

    plugin2 = await make_plugin(config)
    event2 = AstrMessageEvent(sender_id="u1", message="消息", platform="test_platform")
    plugin2.context.llm_script.append(LLMResponse(completion_text="内容包含block"))
    await plugin2.check_request(event2, ProviderRequest())
    assert event2.stopped is True


async def test_check_request_banned_user(make_plugin, config):
    plugin = await make_plugin(config)
    plugin.ban_list["banners"]["test_platform"]["u1"] = time.time() + 3600
    event = AstrMessageEvent(sender_id="u1", message="消息", platform="test_platform")
    await plugin.check_request(event, ProviderRequest())
    assert event.stopped is True
    assert "被封禁中" in str(event.sent[-1])
    assert plugin.context.llm_calls == []


async def test_check_request_expired_ban_auto_unbans(make_plugin, config):
    plugin = await make_plugin(config)
    plugin.ban_list["banners"]["test_platform"]["u1"] = time.time() - 10
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "x", "time": time.time(), "show": True}
    ]
    event = AstrMessageEvent(sender_id="u1", message="消息", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="allow"))
    await plugin.check_request(event, ProviderRequest())
    assert "u1" not in plugin.ban_list["banners"]["test_platform"]
    assert "u1" not in plugin.ban_list["prohibits"]["test_platform"]
    assert len(plugin.context.llm_calls) == 1


async def test_check_request_whitelisted_user(make_plugin, config):
    plugin = await make_plugin(config)
    event = AstrMessageEvent(
        sender_id="white_user", message="任何消息", platform="test_platform"
    )
    await plugin.check_request(event, ProviderRequest())
    assert event.stopped is False
    assert plugin.context.llm_calls == []


async def test_check_request_skip_config(make_plugin, config):
    plugin = await make_plugin(config)
    event = AstrMessageEvent(
        sender_id="u1", message="skip-full", platform="test_platform"
    )
    await plugin.check_request(event, ProviderRequest())
    assert plugin.context.llm_calls == []


async def test_check_request_unknown_platform(make_plugin, config):
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="消息", platform="unknown")
    await plugin.check_request(event, ProviderRequest())
    assert plugin.context.llm_calls == []


async def test_check_request_group_disabled(make_plugin, config):
    config["filter_config"]["filter_group"] = False
    plugin = await make_plugin(config)
    event = AstrMessageEvent(
        sender_id="u1",
        message="消息",
        platform="test_platform",
        group_id="g1",
        group={"id": "g1"},
    )
    await plugin.check_request(event, ProviderRequest())
    assert plugin.context.llm_calls == []

    config["filter_config"]["filter_group"] = True
    plugin2 = await make_plugin(config)
    event2 = AstrMessageEvent(
        sender_id="u1",
        message="消息",
        platform="test_platform",
        group_id="g1",
        group={"id": "g1"},
    )
    plugin2.context.llm_script.append(LLMResponse(completion_text="allow"))
    await plugin2.check_request(event2, ProviderRequest())
    assert len(plugin2.context.llm_calls) == 1


async def test_check_request_empty_message(make_plugin, config):
    plugin = await make_plugin(config)  # skip_empty_mode = False -> skip empty
    event = AstrMessageEvent(sender_id="u1", message="   ", platform="test_platform")
    await plugin.check_request(event, ProviderRequest())
    assert plugin.context.llm_calls == []

    config["filter_config"]["skip_empty_mode"] = True
    plugin2 = await make_plugin(config)
    event2 = AstrMessageEvent(sender_id="u1", message="   ", platform="test_platform")
    plugin2.context.llm_script.append(LLMResponse(completion_text="block"))
    await plugin2.check_request(event2, ProviderRequest())
    assert len(plugin2.context.llm_calls) == 1
    assert event2.stopped is True


async def test_check_request_failclose(make_plugin, config):
    config["filter_config"]["filter_failclose"] = True
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="消息", platform="test_platform")
    plugin.context.llm_script.append(RuntimeError("provider error"))
    await plugin.check_request(event, ProviderRequest())
    assert event.stopped is True
    assert event.sent == []

    config["filter_config"]["filter_failclose"] = False
    plugin2 = await make_plugin(config)
    event2 = AstrMessageEvent(sender_id="u1", message="消息", platform="test_platform")
    plugin2.context.llm_script.append(RuntimeError("provider error"))
    await plugin2.check_request(event2, ProviderRequest())
    assert event2.stopped is False
    assert event2.sent == []


async def test_check_request_sends_context_to_filter(make_plugin, config):
    plugin = await make_plugin(config)
    event = AstrMessageEvent(
        sender_id="u1", message="本轮输入", platform="test_platform"
    )
    contexts = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "之前的内容"},
    ]
    plugin.context.llm_script.append(LLMResponse(completion_text="allow"))
    await plugin.check_request(event, ProviderRequest(contexts=contexts))
    call = plugin.context.llm_calls[0]
    assert call["provider"] == config["filter_config"]["filter_provider"]
    user_msg = call["contexts"][1]["content"]
    assert "之前的内容" in user_msg
    assert "本轮输入" in user_msg


async def test_check_request_speak_mode(make_plugin, config):
    config["speak_config"]["enable_speak"] = True
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))
    plugin.context.llm_script.append(LLMResponse(completion_text="请文明发言"))
    await plugin.check_request(event, ProviderRequest())
    assert len(plugin.context.llm_calls) == 2
    assert str(event.sent[-1]) == "【请文明发言】"


async def test_check_request_speak_fallback_on_error(make_plugin, config):
    config["speak_config"]["enable_speak"] = True
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))
    plugin.context.llm_script.append(RuntimeError("speak provider error"))
    await plugin.check_request(event, ProviderRequest())
    assert str(event.sent[-1]) == "【您的消息不符合规范，请文明发言。】"


# ------------------------------------------------------------------ notify


async def test_check_request_notify_success(make_plugin, config):
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    plugin = await make_plugin(config)
    assert plugin._notify_manager is not None
    assert plugin._notify_manager.admin_umo == "admin:umo"
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))
    await plugin.check_request(event, ProviderRequest())
    assert len(plugin.context.sent_messages) == 1
    umo, chain = plugin.context.sent_messages[0]
    assert umo == "admin:umo"
    assert "骂人" in str(chain)
    # notify_at_ids 为空时不追加任何@组件，消息链保持原样
    assert chain.at_mentions() == []
    assert await plugin._notify_manager.get_pending() == []


async def test_check_request_notify_at_ids_mentions_admins(make_plugin, config):
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    config["notify_config"]["notify_at_ids"] = ["111", "222"]
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))
    await plugin.check_request(event, ProviderRequest())
    umo, chain = plugin.context.sent_messages[0]
    assert umo == "admin:umo"
    # @ 组件按配置顺序排在正文之前
    assert [type(component).__name__ for component in chain.chain[:2]] == ["At", "At"]
    assert chain.at_mentions() == ["111", "222"]
    assert "骂人" in str(chain)


async def test_check_request_notify_failure_queues(make_plugin, config):
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    plugin = await make_plugin(config)
    plugin.context.fail_send = True
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))
    await plugin.check_request(event, ProviderRequest())
    pending = await plugin._notify_manager.get_pending()
    assert len(pending) == 1
    assert pending[0]["times"] == 0
    assert "u1" in pending[0]["content"]
    assert "骂人" in pending[0]["content"]
    # 运行时队列仅保存在内存中，数据文件的待通知快照保持为空
    assert plugin.ban_list["pending_notifications"] == []


async def test_check_request_notify_carries_message_id_and_debug_hint(
    make_plugin, config
):
    """违规通知需要携带违规消息 id，并提示管理员用 /sf debug 查询原始返回体。

    v2.7.0 起 debug_mode 被移除：原始返回体不再单独推送，而是随违规记录落盘，
    由 /sf debug {id} 按需查询，因此通知里必须带上这个 id。
    """
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(
        LLMResponse(completion_text="block", raw_completion="raw-reasoning")
    )
    await plugin.check_request(event, ProviderRequest())

    pro_id = plugin.ban_list["prohibits"]["test_platform"]["u1"][0]["id"]
    _, chain = plugin.context.sent_messages[0]
    text = str(chain)
    assert f"🔑 消息ID：{pro_id}" in text
    assert f"/sf debug {pro_id}" in text
    # 原始返回体只落盘，不再随通知外发
    assert "raw-reasoning" not in text


async def test_check_request_without_notify_neither_sends_nor_crashes(
    make_plugin, config
):
    """关闭违规通知时违规记录照常落盘，但不向管理员会话发送任何消息。

    旧实现会在 debug_mode 打开时单独推送一条原始返回体；该模式已移除，
    因此即使 notify_umo 已配置、enable_notify 仍为关闭，也应当保持静默。
    """
    config["notify_config"]["notify_umo"] = "admin:umo"
    plugin = await make_plugin(config)
    assert plugin._notify_manager is None
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(
        LLMResponse(completion_text="block", raw_completion="raw-reasoning")
    )
    await plugin.check_request(event, ProviderRequest())

    assert plugin.context.sent_messages == []
    records = plugin.ban_list["prohibits"]["test_platform"]["u1"]
    assert records[0]["raw"] == "raw-reasoning"
    assert str(event.sent[-1]) == "【您的消息不符合规范，请文明发言。】"


# ------------------------------------------------------------------ debug 溯源


async def test_check_request_stores_id_and_raw_completion(make_plugin, config):
    """每条违规记录都要带上唯一 id 与审核模型原始返回体，供 /sf debug 溯源。"""
    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(
        LLMResponse(completion_text="block", raw_completion="思维链原文")
    )
    await plugin.check_request(event, ProviderRequest())

    record = plugin.ban_list["prohibits"]["test_platform"]["u1"][0]
    assert record["raw"] == "思维链原文"
    assert record["id"].startswith("abpsfpt-")
    assert len(record["id"]) == len("abpsfpt-") + 20  # token_hex(nbytes=10)

    # 同一个用户连续违规，两条记录拿到的 id 不能重复
    event2 = AstrMessageEvent(
        sender_id="u1", message="又骂人", platform="test_platform"
    )
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))
    await plugin.check_request(event2, ProviderRequest())
    records = plugin.ban_list["prohibits"]["test_platform"]["u1"]
    assert len(records) == 2
    assert records[0]["id"] != records[1]["id"]


async def test_check_request_stringifies_non_str_raw_completion(make_plugin, config):
    """审核 SDK 的 raw_completion 未必是字符串，落盘前需统一转成字符串。"""

    class _RawCompletion:
        def __init__(self, payload):
            self.payload = payload

        def __str__(self):
            return str(self.payload)

    plugin = await make_plugin(config)
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(
        LLMResponse(
            completion_text="block", raw_completion=_RawCompletion({"reason": "违规"})
        )
    )
    await plugin.check_request(event, ProviderRequest())

    record = plugin.ban_list["prohibits"]["test_platform"]["u1"][0]
    assert record["raw"] == "{'reason': '违规'}"
    assert isinstance(record["raw"], str)


# ------------------------------------------------------- v2.7.0 数据迁移


async def test_initialize_migrates_v2_7_0_records(make_plugin):
    """v2.7.0 迁移层：老违规记录补齐 id 与 raw，已有的字段必须原样保留。"""
    data = {
        "available_platforms": ["test_platform"],
        "prohibits": {
            "test_platform": {
                "u1": [
                    {"word": "老记录", "time": 1700000000.0, "show": True},
                    {
                        "id": "abpsfpt-keepme",
                        "word": "已迁移",
                        "time": 1700000001.0,
                        "show": True,
                        "raw": "保留的原始返回体",
                    },
                ]
            }
        },
        "banners": {"test_platform": {}},
        "white_list": {"test_platform": []},
        "pending_notifications": [],
        "data_migrate_tag": ["v2.3.0", "v2.6.0"],
    }
    plugin = await make_plugin(data=data)

    records = plugin.ban_list["prohibits"]["test_platform"]["u1"]
    assert records[0]["id"].startswith("abpsfpt-")
    assert records[0]["raw"] == ""
    assert records[1]["id"] == "abpsfpt-keepme"
    assert records[1]["raw"] == "保留的原始返回体"
    assert "v2.7.0" in plugin.ban_list["data_migrate_tag"]


async def test_initialize_skips_v2_7_0_migration_when_tagged(make_plugin):
    """迁移标签已存在时不再改动数据，避免每次启动都重写违规记录。"""
    data = {
        "available_platforms": ["test_platform"],
        "prohibits": {
            "test_platform": {
                "u1": [
                    {
                        "id": "abpsfpt-fixed",
                        "word": "w",
                        "time": 1700000000.0,
                        "show": True,
                        "raw": "原样",
                    }
                ]
            }
        },
        "banners": {"test_platform": {}},
        "white_list": {"test_platform": []},
        "pending_notifications": [],
        "data_migrate_tag": ["v2.3.0", "v2.6.0", "v2.7.0"],
    }
    plugin = await make_plugin(data=data)

    record = plugin.ban_list["prohibits"]["test_platform"]["u1"][0]
    assert record == {
        "id": "abpsfpt-fixed",
        "word": "w",
        "time": 1700000000.0,
        "show": True,
        "raw": "原样",
    }


async def test_migrated_record_is_traceable_through_debug_command(make_plugin, config):
    """端到端：老数据升级后，其补齐的 id 能被 /sf debug 查到（raw 为空是预期结果）。"""
    data = {
        "available_platforms": ["test_platform"],
        "prohibits": {
            "test_platform": {
                "u1": [{"word": "老记录", "time": 1700000000.0, "show": True}]
            }
        },
        "banners": {"test_platform": {}},
        "white_list": {"test_platform": []},
        "pending_notifications": [],
        "data_migrate_tag": ["v2.3.0", "v2.6.0"],
    }
    plugin = await make_plugin(config, data=data)
    command_manager.initialize(plugin)

    migrated_id = plugin.ban_list["prohibits"]["test_platform"]["u1"][0]["id"]
    event = AstrMessageEvent(
        sender_id="admin", message=f"/sf debug {migrated_id}", platform="test_platform"
    )
    await command_manager.debug(event, migrated_id)

    # 老记录本来就没有原始返回体，迁移只是补空字符串
    assert "已经被清理" in str(event.sent[-1])


# ------------------------------------------------------- raw 保留期限


async def test_unban_all_clears_raw_after_expire_day(make_plugin, config):
    """超过 debug_expire_day 的原始返回体被清空，未到期的原样保留。"""
    config["filter_config"]["debug_expire_day"] = 3
    plugin = await make_plugin(config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "五天前", "time": now - 5 * 86400, "show": True, "raw": "过期原文"},
        {"word": "今天", "time": now - 60, "show": True, "raw": "新鲜原文"},
    ]
    flag = await plugin.unban_all()

    assert flag is True
    records = plugin.ban_list["prohibits"]["test_platform"]["u1"]
    assert records[0]["raw"] == ""
    assert records[1]["raw"] == "新鲜原文"


async def test_unban_all_keeps_raw_when_expire_day_zero(make_plugin, config):
    """debug_expire_day 最小可设为 0，此时任何已存在的原始返回体都应立即清理。"""
    config["filter_config"]["debug_expire_day"] = 0
    plugin = await make_plugin(config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "刚刚", "time": now, "show": True, "raw": "不该保留的原文"}
    ]
    flag = await plugin.unban_all()

    assert flag is True
    assert plugin.ban_list["prohibits"]["test_platform"]["u1"][0]["raw"] == ""


async def test_unban_all_reports_no_change_for_recent_record(make_plugin, config):
    """未到期且无需隐藏的违规记录不应触发落盘。"""
    config["filter_config"]["debug_expire_day"] = 3
    plugin = await make_plugin(config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "今天", "time": now - 60, "show": True, "raw": "新鲜原文"}
    ]
    assert await plugin.unban_all() is False


async def test_unban_all_clears_hidden_record_raw_too(make_plugin, config):
    """被 check 命令隐藏与 raw 过期是两套独立规则，同一条记录可能同时命中。"""
    config["command_config"]["check_disshow_time"] = 3
    config["filter_config"]["debug_expire_day"] = 1
    plugin = await make_plugin(config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "两天前", "time": now - 2 * 86400, "show": True, "raw": "过期原文"}
    ]
    # 模拟 /sf check 已折叠该记录：show 由 check 命令维护，与 raw 是否保留无关
    plugin.ban_list["prohibits"]["test_platform"]["u1"][0]["show"] = False
    assert await plugin.unban_all() is True

    record = plugin.ban_list["prohibits"]["test_platform"]["u1"][0]
    assert record["show"] is False  # 仍在 check_disshow_time 展示期内，不会被重新显示
    assert record["raw"] == ""  # 但已超过原始返回体的保留期限
    assert record["word"] == "两天前"  # 违规内容本身不会被清理


# ---------------------------------------------------------------- terminate


async def test_terminate_writes_file(make_plugin, config, tmp_path):
    plugin = await make_plugin(config)
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "x", "time": time.time(), "show": True}
    ]
    await plugin.terminate()
    saved = json.loads((tmp_path / "banlist.json").read_text(encoding="utf-8"))
    assert "u1" in saved["prohibits"]["test_platform"]


async def test_terminate_cancels_notify_manager_task(make_plugin, config):
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    plugin = await make_plugin(config)
    assert plugin._notify_manager is not None
    await plugin.terminate()
    assert plugin._notify_manager._retry_task.done()


async def test_terminate_snapshots_pending_notifications(make_plugin, config, tmp_path):
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    plugin = await make_plugin(config)
    plugin.context.fail_send = True
    event = AstrMessageEvent(sender_id="u1", message="骂人", platform="test_platform")
    plugin.context.llm_script.append(LLMResponse(completion_text="block"))
    await plugin.check_request(event, ProviderRequest())
    assert len(await plugin._notify_manager.get_pending()) == 1
    await plugin.terminate()
    saved = json.loads((tmp_path / "banlist.json").read_text(encoding="utf-8"))
    pending = saved["pending_notifications"]
    assert len(pending) == 1
    assert "骂人" in pending[0]["content"]
    assert "v2.6.0" in saved["data_migrate_tag"]
