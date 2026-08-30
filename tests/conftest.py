"""Shared fixtures and bootstrap for the smart_filter test suite.

Two things happen here before any test module is imported:

1. ``tests/astrbot`` (a minimal stand-in for the real AstrBot API) is put
   first on ``sys.path``, so plugin imports resolve to the stub even when a
   real AstrBot installation exists in the environment.
2. The plugin is made importable as ``astrbot_plugin_smart_filter_under_test``
   (a synthetic package whose ``__path__`` points at the repo root), so the
   relative imports in ``main.py`` (``from .core import ...``) work without
   installing the plugin.
"""

import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STUB_DIR = _REPO_ROOT / "tests" / "astrbot"

# 1) astrbot stub first on sys.path
sys.path.insert(0, str(_STUB_DIR))

# 2) synthetic package for the plugin under test
_PKG_NAME = "astrbot_plugin_smart_filter_under_test"
_pkg = types.ModuleType(_PKG_NAME)
_pkg.__path__ = [str(_REPO_ROOT)]
sys.modules[_PKG_NAME] = _pkg
importlib.import_module(f"{_PKG_NAME}.main")

from astrbot.api import AstrBotConfig  # noqa: E402
from astrbot.api.star import Context, StarTools  # noqa: E402
from astrbot_plugin_smart_filter_under_test.main import SmartFilter  # noqa: E402
from astrbot_plugin_smart_filter_under_test.core.manager.file_manager import (  # noqa: E402
    file_manager,
)
from astrbot.api.web import request  # noqa: E402


def make_config():
    """A complete default configuration matching ``_conf_schema.json``."""
    return {
        "filter_config": {
            "filter_prompt": "审核提示词",
            "filter_provider": "filter-provider",
            "filter_allow": "allow",
            "filter_block": "block",
            "filter_roles": 3,
            "filter_group": True,
            "filter_mode": True,
            "filter_failclose": False,
            "debug_mode": False,
            "skip_empty_mode": False,
            "skip_schema": [
                {
                    "__template_key": "default",
                    "match_type": "full",
                    "match_content": "skip-full",
                },
                {
                    "__template_key": "default",
                    "match_type": "prefix",
                    "match_content": "skip-prefix",
                },
                {
                    "__template_key": "default",
                    "match_type": "suffix",
                    "match_content": "skip-suffix",
                },
                {
                    "__template_key": "default",
                    "match_type": "regex",
                    "match_content": r"^skip\d+$",
                },
            ],
        },
        "speak_config": {
            "enable_speak": False,
            "speak_prompt": "speak prompt",
            "speak_provider": "speak-provider",
            "speak_start": "【",
            "speak_end": "】",
            "speak_fallback": "您的消息不符合规范，请文明发言。",
        },
        "platform_config": {
            "available_platforms": ["test_platform", "other_platform"],
            "white_list": [
                {
                    "__template_key": "white_list_temp",
                    "platform": "test_platform",
                    "user_id": "white_user",
                }
            ],
        },
        "notify_config": {
            "enable_notify": False,
            "notify_umo": "",
            "notify_retry_intrvael": 60,
            "notify_max_retries": 3,
        },
        "command_config": {
            "check_show_ban": True,
            "check_disshow_time": 3,
        },
    }


@pytest.fixture
def config():
    """A fresh copy of the default configuration per test."""
    return make_config()


@pytest.fixture
def make_plugin(tmp_path):
    """Factory that builds an initialized SmartFilter in the test's loop.

    The plugin is created and ``await``-ed inside the caller's event loop,
    so its ``asyncio.Lock`` objects bind to that loop only.
    """

    async def _make(config=None, data=None, context=None):
        StarTools.data_dir = tmp_path
        if data is not None:
            (tmp_path / "banlist.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        ctx = context if context is not None else Context()
        plugin = SmartFilter(
            ctx, AstrBotConfig(config if config is not None else make_config())
        )
        await plugin.initialize()
        return plugin

    return _make


@pytest.fixture(autouse=True)
def _reset_shared_state():
    """Reset module-level singleton state before each test.

    ``file_manager._fm_lock`` is created at import time and would otherwise
    be bound to the first test's event loop; every test gets a fresh lock.
    """
    file_manager._fm_lock = asyncio.Lock()
    request._json_data = None
    yield
