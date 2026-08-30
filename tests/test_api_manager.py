import pendulum

from astrbot.api.web import request
from astrbot_plugin_smart_filter_under_test.core.manager.api_manager import (
    api_manager,
)


async def _plugin_with_api(make_plugin, config):
    plugin = await make_plugin(config)
    api_manager.initialize(plugin)
    return plugin


async def test_get_violations_sorted_by_last_time(make_plugin, config):
    plugin = await _plugin_with_api(make_plugin, config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "最近", "time": now, "show": True}
    ]
    plugin.ban_list["prohibits"]["other_platform"]["u2"] = [
        {"word": "较早", "time": now - 5000, "show": True}
    ]
    resp = await api_manager.get_violations()
    assert resp["status"] == "ok"
    data = resp["data"]
    assert [v["user_id"] for v in data] == ["u1", "u2"]
    assert data[0]["is_banned"] is False
    assert data[0]["message"] == "最近"


async def test_get_violations_is_banned_flag(make_plugin, config):
    plugin = await _plugin_with_api(make_plugin, config)
    now = pendulum.now().timestamp()
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "x", "time": now, "show": True}
    ]
    plugin.ban_list["banners"]["test_platform"]["u1"] = now + 3600
    resp = await api_manager.get_violations()
    assert resp["data"][0]["is_banned"] is True


async def test_ban_users_success(make_plugin, config):
    plugin = await _plugin_with_api(make_plugin, config)
    resp = await api_manager.ban_users(
        [{"platform": "test_platform", "user_id": "u1"}], {"days": 1}
    )
    result = resp["data"]["results"][0]
    assert result["status"] == "success"
    assert "u1" in plugin.ban_list["banners"]["test_platform"]


async def test_ban_users_empty(make_plugin, config):
    await _plugin_with_api(make_plugin, config)
    resp = await api_manager.ban_users([], {})
    assert resp["status"] == "error"
    assert resp["code"] == 400


async def test_ban_users_zero_duration(make_plugin, config):
    await _plugin_with_api(make_plugin, config)
    resp = await api_manager.ban_users(
        [{"platform": "test_platform", "user_id": "u1"}], {}
    )
    assert resp["status"] == "error"
    assert resp["code"] == 400


async def test_ban_users_invalid_platform(make_plugin, config):
    await _plugin_with_api(make_plugin, config)
    resp = await api_manager.ban_users(
        [{"platform": "nope", "user_id": "u1"}], {"days": 1}
    )
    result = resp["data"]["results"][0]
    assert result["status"] == "error"
    assert "Invalid platform" in result["message"]


async def test_ban_users_missing_fields(make_plugin, config):
    await _plugin_with_api(make_plugin, config)
    resp = await api_manager.ban_users([{"platform": "test_platform"}], {"days": 1})
    result = resp["data"]["results"][0]
    assert result["status"] == "error"
    assert "Missing" in result["message"]


async def test_ban_users_already_banned(make_plugin, config):
    plugin = await _plugin_with_api(make_plugin, config)
    plugin.ban_list["banners"]["test_platform"]["u1"] = (
        pendulum.now().add(days=1).timestamp()
    )
    resp = await api_manager.ban_users(
        [{"platform": "test_platform", "user_id": "u1"}], {"days": 1}
    )
    result = resp["data"]["results"][0]
    assert result["status"] == "error"
    assert "正在封禁中" in result["message"]


async def test_clear_violations(make_plugin, config):
    plugin = await _plugin_with_api(make_plugin, config)
    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "x", "time": 1, "show": True}
    ]
    resp = await api_manager.clear_violations(
        [{"platform": "test_platform", "user_id": "u1"}]
    )
    assert resp["data"]["results"][0]["status"] == "success"
    assert "u1" not in plugin.ban_list["prohibits"]["test_platform"]


async def test_clear_violations_none_found(make_plugin, config):
    await _plugin_with_api(make_plugin, config)
    resp = await api_manager.clear_violations(
        [{"platform": "test_platform", "user_id": "u1"}]
    )
    assert resp["data"]["results"][0]["status"] == "error"


async def test_unban_users(make_plugin, config):
    plugin = await _plugin_with_api(make_plugin, config)
    plugin.ban_list["banners"]["test_platform"]["u1"] = (
        pendulum.now().add(days=1).timestamp()
    )
    resp = await api_manager.unban_users(
        [{"platform": "test_platform", "user_id": "u1"}]
    )
    assert resp["data"]["results"][0]["status"] == "success"
    assert "u1" not in plugin.ban_list["banners"]["test_platform"]


async def test_unban_users_not_banned(make_plugin, config):
    await _plugin_with_api(make_plugin, config)
    resp = await api_manager.unban_users(
        [{"platform": "test_platform", "user_id": "u1"}]
    )
    assert resp["data"]["results"][0]["status"] == "error"


async def test_web_api_handlers_delegate(make_plugin, config):
    plugin = await _plugin_with_api(make_plugin, config)
    request._json_data = {
        "users": [{"platform": "test_platform", "user_id": "u1"}],
        "duration": {"days": 1},
    }
    resp = await plugin.api_ban_users()
    assert resp["data"]["results"][0]["status"] == "success"

    plugin.ban_list["prohibits"]["test_platform"]["u1"] = [
        {"word": "x", "time": 1, "show": True}
    ]
    request._json_data = {"users": [{"platform": "test_platform", "user_id": "u1"}]}
    resp = await plugin.api_clear_violations()
    assert resp["data"]["results"][0]["status"] == "success"

    resp = await plugin.api_unban_users()
    assert resp["data"]["results"][0]["status"] == "success"

    resp = await plugin.api_get_violations()
    assert resp["status"] == "ok"
