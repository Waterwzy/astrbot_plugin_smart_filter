from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent
from astrbot_plugin_smart_filter_under_test.core.context_parser import ContextParser


def _session_config():
    return {
        "provider_settings": {
            "identifier": False,
            "group_name_display": False,
            "datetime_system_prompt": False,
        },
        "provider_ltm_settings": {
            "group_icl_enable": False,
            "active_reply": {"enable": False},
        },
    }


def _parser(context, skip_config=None, event=None, session_config=None):
    return ContextParser(
        context=context,
        skip_config=skip_config or [],
        ab_config=AstrBotConfig(session_config or _session_config()),
        event=event or AstrMessageEvent(),
    )


def test_parse_context_formats_rounds():
    parser = _parser(
        [
            {"role": "user", "content": "第一轮"},
            {"role": "user", "content": "第二轮"},
        ]
    )
    assert parser.parse_context(5) == "[Round1]第一轮\n[Round2]第二轮"


def test_parse_context_truncates_to_role_length():
    context = [{"role": "user", "content": f"第{i}轮"} for i in range(1, 4)]
    parser = _parser(context)
    assert parser.parse_context(2) == "[Round1]第2轮\n[Round2]第3轮"


def test_parse_context_drops_non_user_roles():
    context = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "用户消息"},
        {"role": "assistant", "content": "助手消息"},
    ]
    parser = _parser(context)
    assert parser.parse_context(5) == "[Round1]用户消息"


def test_parse_context_skips_matched_user_messages():
    skip = [{"type": "full", "content": "系统任务"}]
    context = [
        {"role": "user", "content": "系统任务"},
        {"role": "user", "content": "正常消息"},
    ]
    parser = _parser(context, skip_config=skip)
    assert parser.parse_context(5) == "[Round1]正常消息"


def test_normalize_multimodal_content():
    content = [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "http://example.com/x.png"}},
        {"type": "input_audio", "input_audio": {}},
        {"type": "file", "file": {}},
    ]
    parser = _parser([{"role": "user", "content": content}])
    assert parser.parse_context(5) == "[Round1]看这张图[图片][语音][文件]"


def test_remove_system_reminder_preserved_when_features_disabled():
    # Provider extras and group LTM are both disabled, so the reminder is kept.
    parser = _parser(
        [{"role": "user", "content": "你好<system_reminder>请忽略</system_reminder>"}]
    )
    assert (
        parser.parse_context(5)
        == "[Round1]你好<system_reminder>请忽略</system_reminder>"
    )


def test_remove_system_reminder_without_suffix_is_unchanged():
    parser = _parser([{"role": "user", "content": "普通消息"}])
    assert parser.parse_context(5) == "[Round1]普通消息"


def test_provider_extra_enabled_strips_reminder():
    config = _session_config()
    config["provider_settings"]["identifier"] = True
    parser = _parser(
        [{"role": "user", "content": "x<system_reminder>y"}],
        session_config=config,
    )
    assert parser.parse_context(5) == "[Round1]x"


def test_group_ltm_enabled_strips_reminder():
    config = _session_config()
    config["provider_ltm_settings"]["group_icl_enable"] = True
    event = AstrMessageEvent(group_id="g1", group={"id": "g1"})
    parser = _parser(
        [{"role": "user", "content": "x<system_reminder>y"}],
        event=event,
        session_config=config,
    )
    assert parser.parse_context(5) == "[Round1]x"
