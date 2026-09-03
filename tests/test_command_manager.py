import pendulum

from astrbot.api.event import AstrMessageEvent
from astrbot_plugin_smart_filter_under_test.core.manager.command_manager import (
    command_manager,
)


async def _plugin_with_manager(make_plugin, config):
    plugin = await make_plugin(config)
    command_manager.initialize(plugin)
    return plugin


async def test_ban_success(make_plugin, config):
    plugin = await _plugin_with_manager(make_plugin, config)
    event = AstrMessageEvent(
        sender_id="admin", message="/sf ban u1 PT24H", platform="test_platform"
    )
    await command_manager.ban(event, "u1", "PT24H")
    assert "封禁成功" in str(event.sent[-1])
    assert "u1" in plugin.ban_list["banners"]["test_platform"]


async def test_ban_invalid_duration(make_plugin, config):
    await _plugin_with_manager(make_plugin, config)
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.ban(event, "u1", "not-a-duration")
    assert "ISO8601" in str(event.sent[-1])


async def test_ban_unknown_platform(make_plugin, config):
    await _plugin_with_manager(make_plugin, config)
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.ban(event, "u1", "PT1H", "no_such_platform")
    assert "不存在" in str(event.sent[-1])


async def test_ban_already_banned(make_plugin, config):
    await _plugin_with_manager(make_plugin, config)
    await command_manager.ban(AstrMessageEvent(platform="test_platform"), "u1", "PT24H")
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.ban(event, "u1", "PT24H")
    assert "正在封禁中" in str(event.sent[-1])


async def test_unban_not_banned(make_plugin, config):
    await _plugin_with_manager(make_plugin, config)
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.unban(event, "ghost")
    assert "不在封禁列表中" in str(event.sent[-1])


async def test_unban_success(make_plugin, config):
    plugin = await _plugin_with_manager(make_plugin, config)
    await command_manager.ban(AstrMessageEvent(platform="test_platform"), "u1", "PT24H")
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "x", "time": 1, "show": True}
    ]
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.unban(event, "u1")
    assert "解封操作成功" in str(event.sent[-1])
    assert "u1" not in plugin.ban_list["banners"]["test_platform"]
    assert "u1" not in plugin.ban_list["prohibits"]["test_platform"]


async def test_bancount(make_plugin, config):
    plugin = await _plugin_with_manager(make_plugin, config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["repeat_user"] = [
        {"word": "a", "time": now, "show": True},
        {"word": "b", "time": now, "show": True},
    ]
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.bancount(event, 2, "PT1H")
    assert "repeat_user" in plugin.ban_list["banners"]["test_platform"]
    assert "封禁成功" in str(event.sent[-1])


async def test_bancount_unknown_platform(make_plugin, config):
    await _plugin_with_manager(make_plugin, config)
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.bancount(event, 2, "PT1H", "no_such_platform")
    assert "不存在" in str(event.sent[-1])


async def test_check_lists_violations(make_plugin, config):
    plugin = await _plugin_with_manager(make_plugin, config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "违规词", "time": now, "show": True}
    ]
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.check(event)
    text = str(event.sent[-1])
    assert "u1" in text
    assert "违规词" in text


async def test_check_hides_old_violations(make_plugin, config):
    config["command_config"]["check_disshow_time"] = 3
    plugin = await _plugin_with_manager(make_plugin, config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "很旧", "time": now - 5 * 86400, "show": False},
        {"word": "新词", "time": now - 60, "show": True},
    ]
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.check(event)
    text = str(event.sent[-1])
    assert "新词" in text
    assert "很旧" not in text


async def test_check_hides_banned_users_when_configured(make_plugin, config):
    config["command_config"]["check_show_ban"] = False
    plugin = await _plugin_with_manager(make_plugin, config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["banned_user"] = [
        {"word": "x", "time": now, "show": True}
    ]
    plugin.ban_list["banners"]["test_platform"]["banned_user"] = now + 3600
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.check(event)
    assert "banned_user" not in str(event.sent[-1])


async def test_checku_found_and_not_found(make_plugin, config):
    plugin = await _plugin_with_manager(make_plugin, config)
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "词", "time": 1, "show": True}
    ]
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.checku(event, "u1")
    assert "词" in str(event.sent[-1])

    event2 = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.checku(event2, "ghost")
    assert "未找到" in str(event2.sent[-1])


async def test_checkban(make_plugin, config):
    plugin = await _plugin_with_manager(make_plugin, config)
    plugin.ban_list["banners"]["test_platform"]["u1"] = (
        pendulum.now().add(days=1).timestamp()
    )
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.checkban(event)
    assert "u1" in str(event.sent[-1])


async def test_clear_found_and_not_found(make_plugin, config):
    plugin = await _plugin_with_manager(make_plugin, config)
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "x", "time": 1, "show": True}
    ]
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.clear(event, "u1")
    assert "清除" in str(event.sent[-1])
    assert "u1" not in plugin.ban_list["prohibits"]["test_platform"]

    event2 = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.clear(event2, "ghost")
    assert "未找到" in str(event2.sent[-1])


async def test_notify_check_and_clear(make_plugin, config):
    plugin = await _plugin_with_manager(make_plugin, config)
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.notify(event, "check")
    assert "没有待通知" in str(event.sent[-1])

    plugin.ban_list["pending_notifications"] = [
        {
            "id": "abc",
            "times": 0,
            "content": "【违规消息通知】\n用户：u1\n消息：m",
        }
    ]
    event2 = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.notify(event2, "check")
    assert "u1" in str(event2.sent[-1])

    event3 = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.notify(event3, "clear")
    assert "已清空 1 条" in str(event3.sent[-1])
    assert plugin.ban_list["pending_notifications"] == []


async def test_notify_with_live_manager_queue(make_plugin, config):
    config["notify_config"]["enable_notify"] = True
    config["notify_config"]["notify_umo"] = "admin:umo"
    plugin = await _plugin_with_manager(make_plugin, config)
    # 模拟发送失败，使消息进入通知管理器的内存重试队列
    plugin.context.fail_send = True
    await plugin._notify_manager.add_notify("队列中的通知文本")

    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.notify(event, "check")
    assert "队列中的通知文本" in str(event.sent[-1])

    event2 = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.notify(event2, "clear")
    assert "已清空 1 条" in str(event2.sent[-1])
    assert await plugin._notify_manager.get_pending() == []


async def test_notify_invalid_action(make_plugin, config):
    await _plugin_with_manager(make_plugin, config)
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.notify(event, "bogus")
    assert "无效" in str(event.sent[-1])


async def test_checkw_all_and_single(make_plugin, config):
    await _plugin_with_manager(make_plugin, config)
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.checkw(event, None)
    assert "white_user" in str(event.sent[-1])

    event2 = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.checkw(event2, "white_user")
    assert "存在于" in str(event2.sent[-1])

    event3 = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.checkw(event3, "not_in_list")
    assert "不在白名单" in str(event3.sent[-1])


async def test_checkw_repeated_warning(make_plugin, config):
    config["platform_config"]["white_list"].append(
        {
            "__template_key": "white_list_temp",
            "platform": "test_platform",
            "user_id": "white_user",
        }
    )
    await _plugin_with_manager(make_plugin, config)
    event = AstrMessageEvent(sender_id="admin", message="x", platform="test_platform")
    await command_manager.checkw(event, "white_user")
    assert "重复" in str(event.sent[-1])
