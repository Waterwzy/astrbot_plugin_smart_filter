from dataclasses import dataclass


@dataclass
class ContextParser:
    """SmartFilter上下文解析类
    2.4.0加入，用于上下文的截断和规范化
    """

    context: list[dict]
    """OpenAI格式上下文消息"""

    def _clear_tool_calls(self):
        clear_index = []
        for i, obj in enumerate(self.context):
            if obj["role"] == "assistant" and (
                obj.get("refusal") or obj.get("tool_calls")
            ):
                clear_index.append(i)
            if obj["role"] == "tool" or obj["role"] == "system":
                clear_index.append(i)
        self.context = [
            obj for i, obj in enumerate(self.context) if i not in clear_index
        ]

    def _get_roles_context(self, role_lenth: int):
        end_place = len(self.context) - 1
        role_count = 0
        while True:
            if end_place < 0:
                break
            if (
                role_count == role_lenth
                and self.context[end_place]["role"] == "assistant"
            ):
                break
            if self.context[end_place]["role"] == "assistant":
                role_count += 1
            end_place -= 1
        self.context = self.context[end_place + 1 : len(self.context)]

    def parse_context(self, role_lenth: int) -> str:
        """将最近的几轮对话整理成可读的字符串形式
        Args:
            role_lenth(int):需要保留的轮数
        Returns:
            parsed_str(str):格式化后的字符串
        """
        self._clear_tool_calls()
        self._get_roles_context(role_lenth)
        parsed_str = ""
        for i, context_obj in enumerate(self.context):
            for j, son_obj in enumerate(context_obj["content"]):
                text = ""
                if son_obj["type"] == "image_url":
                    text = "[图片]"
                elif son_obj["type"] == "input_audio":
                    text = "[语音]"
                elif son_obj["type"] == "file":
                    text = "[文件]"
                elif son_obj.get("text"):
                    text = son_obj["text"]
                    if text.find("<system_reminder>") != -1:
                        text = text[
                            : text.find("<system_reminder>")
                        ]  # 去除astrbot的系统提示
                if (
                    j == 0
                    and self.context[i]["role"] == "user"
                    and (i == 0 or self.context[i - 1]["role"] == "assistant")
                ):
                    parsed_str += "\n用户输入："
                if j == 0 and self.context[i]["role"] == "assistant":
                    parsed_str += "\n回复："
                parsed_str += text
        return parsed_str.strip()
