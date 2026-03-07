import asyncio
import datetime
import json
import time
import traceback

import pendulum

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools


class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.ban_list = None
        self._sf_lock = asyncio.Lock()

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        async with self._sf_lock:
            self.ban_list = self.get_ban_list()
            await self.handle_update()

    async def handle_update(self):
        for key in list(self.ban_list["prohibits"]):
            if key not in self.config["available_platforms"]:
                self.ban_list["prohibits"].pop(key, None)
        for key in list(self.ban_list["banners"]):
            if key not in self.config["available_platforms"]:
                self.ban_list["banners"].pop(key, None)
        for key in self.config["available_platforms"]:
            if key not in self.ban_list["prohibits"]:
                self.ban_list["prohibits"][key] = {}
            if key not in self.ban_list["banners"]:
                self.ban_list["banners"][key] = {}
        self.ban_list["available_platforms"] = self.config["available_platforms"]
        self.write_ban(self.ban_list)

    async def ban_user(
        self, user_id: str, platform: str, times: pendulum.Duration
    ) -> tuple:
        if user_id in self.ban_list["banners"][platform]:
            if self.ban_list["banners"][platform][user_id] <= time.time():
                self.ban_list["banners"][platform].pop(user_id)
            else:
                time_str = datetime.datetime.fromtimestamp(
                    self.ban_list["banners"][platform][user_id]
                ).strftime("%Y年%m月%d日 %H:%M:%S")
                return (
                    "Fail",
                    f"用户正在封禁中，预计解封时间{time_str}，请尝试解封后再试",
                )
        future = pendulum.now() + times
        self.ban_list["banners"][platform][user_id] = future.timestamp()
        return "Success", future.strftime("%Y年%m月%d日 %H:%M:%S")

    def check_user(
        self,
        user_id: str,
        config: AstrBotConfig,
        plat_name: list,
        times: str | None = None,
    ) -> MessageChain | None:
        if user_id not in config["admins_id"]:
            chain = MessageChain().message(
                "此用户仅管理员有权使用，你不是管理员，无权使用"
            )
            logger.error(f"user id:{user_id}")
            return chain
        for plat in plat_name:
            if plat not in self.ban_list["available_platforms"]:
                chain = MessageChain().message(f"消息平台{plat}不存在，请核实后重试")
                return chain
        if times is not None:
            try:
                ban_time = pendulum.parse(times)
                if not isinstance(ban_time, pendulum.Duration):
                    chain = MessageChain().message(
                        "这不是一个符合ISO8601规范的持续时间，请核实后再试。"
                    )
                    return chain
            except pendulum.parsing.exceptions.ParserError:
                chain = MessageChain().message(
                    "这不是一个符合ISO8601规范的持续时间，请核实后再试。"
                )
                return chain
        return None

    @filter.command("sf_ban")
    async def sf_ban(
        self,
        event: AstrMessageEvent,
        user_id: str,
        times: str,
        plat_name: str | None = None,
    ):
        """按照id封禁某位用户一段时间"""
        async with self._sf_lock:
            if plat_name is None:
                plat_name = event.platform_meta.name
            config = self.context.get_config(event.unified_msg_origin)
            chain = self.check_user(event.get_sender_id(), config, [plat_name], times)

            if chain is None:
                ban_time = pendulum.parse(times)
                state, detail = await self.ban_user(user_id, plat_name, ban_time)
                if state == "Success":
                    chain = MessageChain().message(
                        f"用户{user_id}封禁成功，预计解封时间{detail}"
                    )
                else:
                    chain = MessageChain().message(f"{detail}")

                self.write_ban(self.ban_list)
        await event.send(chain)

    @filter.command("sf_unban")
    async def sf_unban(
        self, event: AstrMessageEvent, user_id: str, plat_name: str | None = None
    ):
        """按照id手动解封某位用户"""
        async with self._sf_lock:
            config = self.context.get_config(event.unified_msg_origin)

            if plat_name is None:
                plat_name = event.platform_meta.name

            chain = self.check_user(event.get_sender_id(), config, [plat_name])
            if chain is None:
                if user_id not in self.ban_list["banners"][plat_name]:
                    chain = MessageChain().message("该用户不在封禁列表中，请核实后重试")
                else:
                    self.ban_list["banners"][plat_name].pop(user_id)
                    if user_id in self.ban_list["prohibits"][plat_name]:
                        self.ban_list["prohibits"][plat_name].pop(user_id)
                    self.write_ban(self.ban_list)

                    chain = MessageChain().message("解封操作成功！")
        await event.send(chain)

    @filter.command("sf_bancount")
    async def sf_bancount(
        self,
        event: AstrMessageEvent,
        count: int,
        times: str,
        plat_name: str | None = None,
    ):
        """按照封禁次数自动封禁一批用户一段时间"""
        async with self._sf_lock:
            config = self.context.get_config(event.unified_msg_origin)

            if plat_name is not None:
                plat_name = [plat_name]
            else:
                plat_name = self.ban_list["available_platforms"]

            chain = self.check_user(event.get_sender_id(), config, plat_name, times)

            if chain is None:
                if await self.unban_all():
                    self.write_ban(self.ban_list)
                ban_time = pendulum.parse(times)
                res_str = "封禁结果返回：\n"
                for plat in plat_name:
                    res_str += f"平台{plat}:\n"
                    for key, user in self.ban_list["prohibits"][plat].items():
                        if len(user) >= count:
                            res, detail = await self.ban_user(key, plat, ban_time)
                            res_str += f"用户{key}:"
                            if res == "Success":
                                res_str += f"封禁成功，预计解封时间{detail}\n"
                            else:
                                res_str += f"{detail}\n"
                self.write_ban(self.ban_list)
                chain = MessageChain().message(res_str)
        await event.send(chain)

    @filter.command("sf_check")
    async def sf_check(self, event: AstrMessageEvent, plat_name: str | None = None):
        """检查用户发送的违规消息内容"""
        async with self._sf_lock:
            config = self.context.get_config(event.unified_msg_origin)

            if plat_name is None:
                plat_name = self.config["available_platforms"]

            chain = self.check_user(event.get_sender_id(), config, plat_name)

            if chain is None:
                if await self.unban_all():
                    self.write_ban(self.ban_list)
                prohibit_str = "目前的所有违规历史消息：\n"
                for key in plat_name:
                    prohibit_str += f"消息平台{key}:\n"
                    for user, msg_list in self.ban_list["prohibits"][key].items():
                        prohibit_str += f"用户id：{user} 违规消息数:{len(msg_list)}条\n"
                        for words in msg_list:
                            prohibit_str += f"{words}\n"
                        prohibit_str += "\n"
                chain = MessageChain().message(prohibit_str)
        await event.send(chain)

    async def unban_all(self):
        flag = False
        for key_p, item_plat in list(self.ban_list["banners"].items()):
            for key, item in list(item_plat.items()):
                if item <= time.time():
                    self.ban_list["banners"][key_p].pop(key)
                    self.ban_list["prohibits"][key_p].pop(key, None)
                    flag = True
        return flag

    @filter.command("sf_checkban")
    async def sf_checkban(self, event: AstrMessageEvent, plat_name: str | None = None):
        """查看目前正在封禁的用户"""
        async with self._sf_lock:
            config = self.context.get_config(event.unified_msg_origin)

            if plat_name is not None:
                plat_name = [plat_name]
            else:
                plat_name = self.ban_list["available_platforms"]

            chain = self.check_user(event.get_sender_id(), config, plat_name)

            if chain is None:
                if await self.unban_all():
                    self.write_ban(self.ban_list)

                ban_str = "目前封禁中的用户：\n"
                for key in plat_name:
                    ban_str += f"消息平台：{key}\n"
                    for user, times in self.ban_list["banners"][key].items():
                        except_time = datetime.datetime.fromtimestamp(times)
                        ban_str += f"用户{user},预计解封时间为{except_time.strftime('%Y年%m月%d日 %H:%M:%S')}\n"
                chain = MessageChain().message(ban_str)
        await event.send(chain)

    @filter.command("sf_clear")
    async def sf_clear(
        self, event: AstrMessageEvent, user_id: str, plat_name: str | None = None
    ):
        """手动清理特定用户的违规记录（默认清理找到的第一个）"""
        async with self._sf_lock:
            config = self.context.get_config(event.unified_msg_origin)

            if plat_name is None:
                plat_name = self.ban_list["available_platforms"]
            else:
                plat_name = [plat_name]

            chain = self.check_user(event.get_sender_id(), config, plat_name)
            flag = 0

            if chain is None:
                for plat in plat_name:
                    if user_id in self.ban_list["prohibits"][plat]:
                        send_str = f"用户{user_id}的违规消息{self.ban_list['prohibits'][plat][user_id]}将会被清除"
                        self.ban_list["prohibits"][plat].pop(user_id)
                        chain = MessageChain().message(send_str)
                        self.write_ban(self.ban_list)
                        flag = 1
                        break
                if not flag:
                    send_str = f"未找到用户{user_id}的违规消息，请使用/sf_check来查看当前记录的所有平台的违规消息"
                    chain = MessageChain().message(send_str)
        await event.send(chain)

    def get_ban_list(self):
        data_dir = StarTools.get_data_dir()
        if not data_dir.exists():
            data_dir.mkdir(parents=True)
        file_path = data_dir / "banlist.json"

        if not file_path.exists():
            # 创建默认结构
            default_banlist = {
                "available_platforms": [],
                "prohibits": {},
                "banners": {},
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(default_banlist, f, ensure_ascii=False, indent=4)
            return default_banlist

        try:
            with open(file_path, encoding="utf-8") as f:
                banlist = json.load(f)
        except Exception as e:
            logger.error(e)
            raise e
        return banlist

    def write_ban(self, ban_list):
        data_dir = StarTools.get_data_dir()
        if not data_dir.exists():
            data_dir.mkdir(parents=True)
        file_path = data_dir / "banlist.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(ban_list, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(e)
            raise e
        return

    async def terminate(self):
        """插件销毁时记得关闭会话，释放资源"""

    # 注册指令的装饰器。
    @filter.on_llm_request()
    async def check_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """这是一个检查用户输入的函数"""  # 这是 handler 的描述，将会被解析方便用户了解插件内容。建议填写。
        sender_id = event.get_sender_id()
        msg_str = event.get_message_str()
        sender_plat = event.platform_meta.name
        if sender_plat not in self.ban_list["available_platforms"]:
            return
        is_banned = 0
        async with self._sf_lock:
            if (
                sender_plat in self.ban_list["available_platforms"]
                and sender_id in self.ban_list["banners"][sender_plat]
            ):
                if time.time() >= self.ban_list["banners"][sender_plat][sender_id]:
                    self.ban_list["banners"][sender_plat].pop(sender_id)
                    self.ban_list["prohibits"][sender_plat].pop(sender_id, None)
                    self.write_ban(self.ban_list)
                else:
                    times = self.ban_list["banners"][sender_plat][sender_id]
                    except_time = datetime.datetime.fromtimestamp(times)
                    chain = MessageChain().message(
                        f"你在被封禁中，具体情况请联系管理员。预计解封时间:{except_time.strftime('%Y年%m月%d日 %H:%M:%S')}"
                    )
                    event.stop_event()
                    is_banned = 1

        if is_banned:
            await event.send(chain)
            return

        system_prompt = (
            await self.context.persona_manager.get_persona(self.config["filter_prompt"])
        ).system_prompt
        msg = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"user input:{msg_str}"},
        ]
        # logger.warning(f"获取personl类：{system_prompt}")
        try:
            filter_res = await self.context.llm_generate(
                chat_provider_id=self.config["filter_config"], contexts=msg
            )
            if self.config["filter_mode"]:
                if self.config["filter_allow"] in filter_res.completion_text:
                    return
            else:
                if self.config["filter_block"] not in filter_res.completion_text:
                    return
            filter_reasoning_res = filter_res.reasoning_content
        except Exception:
            error_msg = traceback.format_exc()
            logger.error(error_msg)
            return

        # 这里就是stage1没通过的消息，换人格了
        # chain = MessageChain().message(f"审核模型拒绝！")
        async with self._sf_lock:
            if (
                sender_plat in self.ban_list["available_platforms"]
                and self.ban_list["prohibits"][sender_plat].get(sender_id, None) is None
            ):
                self.ban_list["prohibits"][sender_plat][sender_id] = []
            self.ban_list["prohibits"][sender_plat][sender_id].append(msg_str)

            self.write_ban(self.ban_list)

        speak_prompt_str = (
            await self.context.persona_manager.get_persona(self.config["speak_prompt"])
        ).system_prompt
        msg = [
            {"role": "system", "content": speak_prompt_str},
            {"role": "user", "content": msg_str},
        ]
        try:
            speak_res = await self.context.llm_generate(
                chat_provider_id=self.config["speak_config"], contexts=msg
            )
        except Exception:
            error_msg = traceback.format_exc()
            logger.error(error_msg)
            return
        res_str = (
            self.config["speak_start"]
            + speak_res.completion_text
            + self.config["speak_end"]
        )
        chain = MessageChain().message(res_str)
        await event.send(chain)
        if self.config["debug_mode"]:
            chain = MessageChain().message(
                f"[DEBUG]reasoning content:{filter_reasoning_res}"
            )
            await event.send(chain)
        event.stop_event()
