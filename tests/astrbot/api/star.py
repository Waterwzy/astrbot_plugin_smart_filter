"""Stand-in for ``astrbot.api.star``."""

from pathlib import Path


class LLMResponse:
    """Fake LLM generation result."""

    def __init__(self, completion_text="", raw_completion=None):
        self.completion_text = completion_text
        self.raw_completion = raw_completion


class _Persona:
    def __init__(self, system_prompt=""):
        self.system_prompt = system_prompt


class PersonaManager:
    def __init__(self, prompts=None):
        self.prompts = prompts or {}
        self.calls = []

    async def get_persona(self, name):
        self.calls.append(name)
        return _Persona(self.prompts.get(name, f"persona:{name}"))


class Context:
    """Scriptable fake of AstrBot's ``Context``."""

    DEFAULT_SESSION_CONFIG = {
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

    def __init__(self, session_config=None, persona_prompts=None):
        self.web_apis = []
        self.sent_messages = []
        self.llm_calls = []
        self.llm_script = []
        self.fail_send = False
        self.session_config = (
            session_config
            if session_config is not None
            else dict(self.DEFAULT_SESSION_CONFIG)
        )
        self.persona_manager = PersonaManager(persona_prompts)

    def register_web_api(self, path, handler, methods, desc):
        self.web_apis.append((path, handler, methods, desc))

    def get_config(self, unified_msg_origin):
        return self.session_config

    async def send_message(self, umo, chain):
        if self.fail_send:
            raise RuntimeError("stub: send_message failed")
        self.sent_messages.append((umo, chain))

    async def llm_generate(self, chat_provider_id=None, contexts=None):
        self.llm_calls.append({"provider": chat_provider_id, "contexts": contexts})
        if self.llm_script:
            item = self.llm_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return LLMResponse("allow")


class Star:
    def __init__(self, context):
        self.context = context


class StarTools:
    """Fake ``StarTools``; ``data_dir`` is pointed at a tmp_path per test."""

    data_dir = None

    @classmethod
    def get_data_dir(cls):
        if cls.data_dir is None:
            return Path(".")
        return Path(cls.data_dir)
