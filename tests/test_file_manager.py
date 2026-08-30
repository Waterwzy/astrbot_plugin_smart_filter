import json

import pytest

from astrbot_plugin_smart_filter_under_test.core.manager.file_manager import (
    SmartFilterFileManager,
)

DEFAULT_KEYS = [
    "available_platforms",
    "prohibits",
    "banners",
    "white_list",
    "pending_notifications",
    "data_migrate_tag",
]


@pytest.fixture
async def fm(tmp_path):
    manager = SmartFilterFileManager()
    await manager.initialize(tmp_path)
    return manager


async def test_initialize_creates_default_file(tmp_path, fm):
    file_path = tmp_path / "banlist.json"
    assert file_path.exists()
    data = json.loads(file_path.read_text(encoding="utf-8"))
    for key in DEFAULT_KEYS:
        assert key in data


async def test_read_file_returns_normalized_data(fm):
    data = await fm.read_file()
    assert data["available_platforms"] == []
    assert data["prohibits"] == {}
    assert data["banners"] == {}
    assert data["white_list"] == {}
    assert data["pending_notifications"] == []
    assert data["data_migrate_tag"] == []


async def test_check_list_repairs_missing_and_wrong_types(fm):
    repaired = fm._check_list({"available_platforms": "not-a-list"})
    assert repaired["available_platforms"] == []
    assert repaired["prohibits"] == {}
    assert repaired["white_list"] == {}

    repaired = fm._check_list({})
    for key in DEFAULT_KEYS:
        assert key in repaired


async def test_write_file_and_force_write(fm, tmp_path):
    file_path = tmp_path / "banlist.json"
    await fm.write_file({"available_platforms": ["p1"]}, force=True)
    saved = json.loads(file_path.read_text(encoding="utf-8"))
    assert saved["available_platforms"] == ["p1"]

    # Non-force writes within the 5-second window are throttled
    await fm.write_file({"available_platforms": ["p2"]})
    saved = json.loads(file_path.read_text(encoding="utf-8"))
    assert saved["available_platforms"] == ["p1"]

    await fm.write_file({"available_platforms": ["p3"]}, force=True)
    saved = json.loads(file_path.read_text(encoding="utf-8"))
    assert saved["available_platforms"] == ["p3"]


async def test_read_corrupted_file_backs_up_and_returns_default(fm, tmp_path):
    (tmp_path / "banlist.json").write_text("{invalid json", encoding="utf-8")
    data = await fm.read_file()
    for key in DEFAULT_KEYS:
        assert key in data
    backups = list(tmp_path.glob("banlist.json.backup.*"))
    assert len(backups) == 1
