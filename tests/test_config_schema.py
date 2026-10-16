"""Keep ``_conf_schema.json`` and the code/config used by the tests in sync.

v2.7.0 removed ``debug_mode`` from the schema, which silently invalidated every
test that still configured it: AstrBot fills in exactly the keys declared in
``_conf_schema.json``, so a config item that is no longer declared can never
reach the plugin, and a config item the plugin reads but the schema does not
declare is always missing at runtime.

Two guards live here:

1. every config key the plugin source reads must be declared in the schema;
2. the config fixture in ``conftest.py`` must mirror the schema exactly, since
   it stands in for what AstrBot hands the plugin.
"""

import json
import re
from pathlib import Path

import pytest
from conftest import make_config

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "_conf_schema.json"

# config["section"] / config.get("section", {}) —— 分组级访问
_SECTION_ACCESS = re.compile(
    r"\.config(?:\[\s*[\"'](\w+)[\"']\s*\]|\.get\(\s*[\"'](\w+)[\"'])"
)
# config["section"]["item"] —— 配置项级访问
_ITEM_ACCESS = re.compile(
    r"\.config\[\s*[\"'](\w+)[\"']\s*\]\s*\[\s*[\"'](\w+)[\"']\s*\]"
)

# Composite containers: the schema declares templates, while the plugin (and the
# test fixture) receives the rendered list of items.
_TEMPLATE_TYPES = ("template_list",)


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _iter_sections(schema):
    for section, spec in schema.items():
        if isinstance(spec, dict) and spec.get("type") == "object":
            yield section, spec["items"]


def _template_names(spec):
    """配置分组中声明为模板列表、因而由插件自行渲染的配置项名。"""
    return {name for name, item in spec.items() if item.get("type") in _TEMPLATE_TYPES}


def _source_files():
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if "tests" in path.relative_to(REPO_ROOT).parts:
            continue
        yield path


def test_source_only_reads_declared_sections(schema):
    """源码读取的配置分组必须在 schema 中声明（例如 notify_config）。"""
    section_names = {section for section, _ in _iter_sections(schema)}
    assert section_names, "schema 未解析出任何配置分组"

    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for match in _SECTION_ACCESS.finditer(text):
            section = match.group(1) or match.group(2)
            if section not in section_names:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {section}")

    assert not offenders, (
        f"源码读取了 schema 未声明的配置分组：{sorted(set(offenders))}"
    )


def test_source_config_items_exist_in_schema(schema):
    """分组内的配置项（如 debug_expire_day）必须真实存在于对应分组。"""
    sections = dict(_iter_sections(schema))
    missing = []

    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for match in _ITEM_ACCESS.finditer(text):
            section, item = match.group(1), match.group(2)
            if section not in sections:
                continue
            if item not in sections[section]:
                missing.append(f"{path.relative_to(REPO_ROOT)}: {section}.{item}")

    assert not missing, (
        f"源码读取了 schema 中不存在的配置项：{sorted(set(missing))}；"
        "被删除的配置项（例如 v2.7.0 的 debug_mode）必须同步清理"
    )


def test_removed_debug_mode_is_not_referenced(schema):
    """v2.7.0 已删除 debug_mode，源码与 schema 都不应再出现它。"""
    assert "debug_mode" not in schema["filter_config"]["items"]

    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _source_files()
        if "debug_mode" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"debug_mode 已被移除，但仍出现在：{offenders}"


def test_config_fixture_matches_schema(schema):
    """conftest 的配置夹具必须与 schema 完全一致（AstrBot 就是这么填充的）。

    模板列表（skip_schema / white_list）在 schema 里声明的是 templates，插件收到的
    是渲染后的条目列表，夹具中的条目形态由各自的测试覆盖，这里只对比普通配置项。
    """
    fixture = make_config()
    assert set(fixture) == set(schema), "配置分组与 schema 不一致"

    for section, spec in _iter_sections(schema):
        declared = {
            name
            for name, item in spec.items()
            if item.get("type") not in _TEMPLATE_TYPES
        }
        assert declared, f"配置分组 {section} 未解析出任何配置项"
        assert set(fixture[section]) >= declared, (
            f"配置分组 {section} 缺少 schema 声明的配置项；"
            f"缺失：{sorted(declared - set(fixture[section]))}，"
            "新增配置项时请同步更新 conftest.make_config"
        )
        # 夹具里多出来的普通配置项说明它已被 schema 删除（例如 v2.7.0 的 debug_mode）
        extra = {
            name
            for name in set(fixture[section]) - declared
            if name not in _template_names(spec)
        }
        assert not extra, (
            f"配置分组 {section} 存在 schema 未声明的配置项：{sorted(extra)}；"
            "配置项被删除时请同步删除 conftest.make_config 中的旧键"
        )
