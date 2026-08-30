def is_skip(config: list, c_str: str) -> bool:
    for content in config:
        if content["type"] == "full" and c_str == content["content"]:
            return True
        if content["type"] == "prefix" and c_str.startswith(content["content"]):
            return True
        if content["type"] == "suffix" and c_str.endswith(content["content"]):
            return True
        if content["type"] == "regex" and content["content"].fullmatch(c_str):
            return True
    return False


def format_chinese_datetime(dt) -> str:
    """Format a datetime-like object as "2026年01月01日 08:05:03".

    不直接使用带中文字面量的 strftime 格式串：在 Windows 上 Python 3.11 及
    更早版本会用 locale 编码转换格式串，非中文 locale（如 cp1252）无法编码
    中文而抛出 UnicodeEncodeError。这里用纯 Python 格式化，输出与
    strftime("%Y年%m月%d日 %H:%M:%S") 完全一致。
    """
    return (
        f"{dt.year}年{dt.month:02d}月{dt.day:02d}日 "
        f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
    )
