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
