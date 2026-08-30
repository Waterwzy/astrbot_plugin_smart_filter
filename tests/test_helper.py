import re

from astrbot_plugin_smart_filter_under_test.core.helper import is_skip


def test_full_match():
    config = [{"type": "full", "content": "关键词"}]
    assert is_skip(config, "关键词")
    assert not is_skip(config, "关键词前缀")


def test_prefix_match():
    config = [{"type": "prefix", "content": "/cmd"}]
    assert is_skip(config, "/cmd help")
    assert not is_skip(config, "no /cmd here")


def test_suffix_match():
    config = [{"type": "suffix", "content": "结束语"}]
    assert is_skip(config, "这是一段结束语")
    assert not is_skip(config, "结束语开头不匹配")


def test_regex_match():
    config = [{"type": "regex", "content": re.compile(r"^skip-\d+$")}]
    assert is_skip(config, "skip-123")
    assert not is_skip(config, "skip-abc")
    assert not is_skip(config, "xskip-123")


def test_no_match_returns_false():
    config = [
        {"type": "full", "content": "a"},
        {"type": "prefix", "content": "b"},
        {"type": "suffix", "content": "c"},
        {"type": "regex", "content": re.compile(r"^d$")},
    ]
    assert not is_skip(config, "zzz")


def test_empty_config_returns_false():
    assert not is_skip([], "任意消息")
