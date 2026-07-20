from dataclasses import dataclass

from astrbot.api import logger

from . import helper


@dataclass
class ContextParser:
    """SmartFilter上下文解析类
    2.4.0加入，用于上下文的截断和规范化
    """

    context: list[dict]
    """OpenAI格式上下文消息"""
    skip_config: list

    def _normalize_str(self, content) -> str:
        if isinstance(content, str):
            return self._remove_astrbot_system_reminder(content)
        r_str = ""
        for obj in content:
            if obj["type"] == "image_url":
                r_str += "[图片]"
            elif obj["type"] == "input_audio":
                r_str += "[语音]"
            elif obj["type"] == "file":
                r_str += "[文件]"
            elif obj.get("text"):
                r_str += obj["text"]
                r_str = self._remove_astrbot_system_reminder(r_str)
        return r_str

    def _clear_other_calls(self):
        clear_index = []
        for i, obj in enumerate(self.context):
            if obj["role"] != "user":
                clear_index.append(i)
            elif helper.is_skip(
                self.skip_config,
                self._remove_astrbot_system_reminder(
                    self._normalize_str(obj["content"])
                ),
            ):
                clear_index.append(i)
                logger.debug(
                    f"消息{self._remove_astrbot_system_reminder(self._normalize_str(obj['content']))}符合跳过配置，不计入多轮审核。"
                )
        self.context = [
            obj for i, obj in enumerate(self.context) if i not in clear_index
        ]

    def _remove_astrbot_system_reminder(
        self, ori_str: str
    ) -> str:  # 去除astrbot的系统提示<system_reminder>
        if ori_str.find("<system_reminder>") != -1:
            return ori_str[: ori_str.find("<system_reminder>")]
        else:
            return ori_str

    def parse_context(self, role_lenth: int) -> str:
        """将最近的几轮对话整理成可读的字符串形式
        Args:
            role_lenth(int):需要保留的轮数
        Returns:
            parsed_str(str):格式化后的字符串
        """
        self._clear_other_calls()
        self.context = self.context[max(0, len(self.context) - role_lenth) :]
        parsed_str = ""
        for i, context_obj in enumerate(self.context):
            text = self._normalize_str(context_obj["content"])
            parsed_str += f"\n[Round{i + 1}]{text}"
        return parsed_str.strip()
