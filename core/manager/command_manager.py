import datetime

import pendulum

from astrbot.api.event import AstrMessageEvent, MessageChain

from .file_manager import file_manager


class SmartFilterCommandFilter:
    """SmartFilter指令处理器
    未发布，统一处理插件指令，main.py仅负责注册指令并且转发
    全局单实例
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._plugin = None
        return cls._instance

    def initialize(self, plugin):
        """该类初始化方法，传入插件主类实例
        Args:
            plugin:插件实例
        """
        self._plugin = plugin

    async def ban(
        self,
        event: AstrMessageEvent,
        user_id: str,
        times: str,
        plat_name: str | None = None,
    ):
        async with self._plugin._sf_lock:
            if plat_name is None:
                plat_name = event.platform_meta.name
            chain = self._plugin.check_user([plat_name], times)

            if chain is None:
                ban_time = pendulum.parse(times)
                state, detail = await self._plugin.ban_user(
                    user_id, plat_name, ban_time
                )  # type:ignore
                if state == "Success":
                    chain = MessageChain().message(
                        f"用户{user_id}封禁成功，预计解封时间{detail}"
                    )
                else:
                    chain = MessageChain().message(f"{detail}")

                await file_manager.write_file(self._plugin.ban_list)
        await event.send(chain)

    async def unban(
        self, event: AstrMessageEvent, user_id: str, plat_name: str | None = None
    ):
        async with self._plugin._sf_lock:
            if plat_name is None:
                plat_name = event.platform_meta.name

            chain = self._plugin.check_user([plat_name])
            if chain is None:
                if user_id not in self._plugin.ban_list["banners"][plat_name]:
                    chain = MessageChain().message("该用户不在封禁列表中，请核实后重试")
                else:
                    self._plugin.ban_list["banners"][plat_name].pop(user_id)
                    if user_id in self._plugin.ban_list["prohibits"][plat_name]:
                        self._plugin.ban_list["prohibits"][plat_name].pop(user_id)
                    await file_manager.write_file(self._plugin.ban_list)

                    chain = MessageChain().message("解封操作成功！")
        await event.send(chain)

    async def bancount(
        self,
        event: AstrMessageEvent,
        count: int,
        times: str,
        plat_name: str | None = None,
    ):
        async with self._plugin._sf_lock:
            if plat_name is not None:
                plat_list = [plat_name]
            else:
                plat_list = self._plugin.ban_list["available_platforms"]

            chain = self._plugin.check_user(plat_list, times)

            if chain is None:
                if await self._plugin.unban_all():
                    await file_manager.write_file(self._plugin.ban_list)
                ban_time = pendulum.parse(times)
                res_str = "封禁结果返回：\n"
                for plat in plat_list:
                    res_str += f"平台{plat}:\n"
                    for key, user in self._plugin.ban_list["prohibits"][plat].items():
                        if len(user) >= count:
                            res, detail = await self._plugin.ban_user(
                                key, plat, ban_time
                            )  # type:ignore
                            res_str += f"用户{key}:"
                            if res == "Success":
                                res_str += f"封禁成功，预计解封时间{detail}\n"
                            else:
                                res_str += f"{detail}\n"
                await file_manager.write_file(self._plugin.ban_list)
                chain = MessageChain().message(res_str)
        await event.send(chain)

    async def check(self, event: AstrMessageEvent, plat_name: str | None = None):
        async with self._plugin._sf_lock:
            if plat_name is None:
                plat_list = self._plugin.config["platform_config"][
                    "available_platforms"
                ]
            else:
                plat_list = [plat_name]

            chain = self._plugin.check_user(plat_list)

            if chain is None:
                if await self._plugin.unban_all():
                    await file_manager.write_file(self._plugin.ban_list)
                prohibit_str = "最近的违规历史消息：\n"
                for key in plat_list:
                    flag = False
                    prohibit_str += f"消息平台{key}:\n"
                    for user, msg_list in self._plugin.ban_list["prohibits"][
                        key
                    ].items():
                        if (
                            self._plugin.ban_list["banners"][key].get(user) is not None
                            and not self._plugin.config["command_config"][
                                "check_show_ban"
                            ]
                        ):
                            continue
                        flag = True
                        prohibit_str += f"用户id：{user} 违规消息数:{len(msg_list)}条"
                        if self._plugin.ban_list["banners"][key].get(user) is not None:
                            prohibit_str += "（已封禁）"
                        prohibit_str += "\n"
                        show_flag = False
                        for words in msg_list:
                            if words["show"]:
                                prohibit_str += f"{words['word']}\n"
                                show_flag = True
                        if not show_flag:
                            prohibit_str += "(当前所有违规消息已被折叠)"
                        prohibit_str += "\n"
                    if not flag:
                        prohibit_str += "当前平台不存在违规消息\n"
                prohibit_str += "💡部分消息可能被折叠，通过'/sf checku 用户ID 消息平台'以查看特定用户的详细信息"
                chain = MessageChain().message(prohibit_str.strip())
        await event.send(chain)

    async def checku(
        self, event: AstrMessageEvent, user_id: str, plat_name: str | None = None
    ):
        async with self._plugin._sf_lock:
            if plat_name is not None:
                plat_list = [plat_name]
            else:
                plat_list = [event.get_platform_name()]

            chain = self._plugin.check_user(plat_list)

            if chain is None:
                if (
                    self._plugin.ban_list["prohibits"][plat_list[0]].get(user_id)
                    is None
                ):
                    chain = MessageChain().message(
                        f"未找到用户{user_id}的违规记录，有可能是相应消息平台不存在该用户或用户不存在记录中的违规消息。"
                    )
                else:
                    prohibit_str = (
                        f"用户{user_id}的违规消息记录（在消息平台{plat_list[0]}）：\n"
                    )
                    for word in self._plugin.ban_list["prohibits"][plat_list[0]][
                        user_id
                    ]:
                        prohibit_str += f"{word['word']}\n"
                    chain = MessageChain().message(prohibit_str.strip())
        await event.send(chain)

    async def checkban(self, event: AstrMessageEvent, plat_name: str | None = None):
        async with self._plugin._sf_lock:
            if plat_name is not None:
                plat_list = [plat_name]
            else:
                plat_list = self._plugin.ban_list["available_platforms"]

            chain = self._plugin.check_user(plat_list)

            if chain is None:
                if await self._plugin.unban_all():
                    await file_manager.write_file(self._plugin.ban_list)

                ban_str = "目前封禁中的用户：\n"
                for key in plat_list:
                    ban_str += f"消息平台：{key}\n"
                    for user, times in self._plugin.ban_list["banners"][key].items():
                        except_time = datetime.datetime.fromtimestamp(times)
                        ban_str += f"用户{user},预计解封时间为{except_time.strftime('%Y年%m月%d日 %H:%M:%S')}\n"
                    if len(self._plugin.ban_list["banners"][key].items()) == 0:
                        ban_str += "当前平台没有正在封禁中的用户\n"
                chain = MessageChain().message(ban_str.strip())
        await event.send(chain)

    async def clear(
        self, event: AstrMessageEvent, user_id: str, plat_name: str | None = None
    ):
        async with self._plugin._sf_lock:
            if plat_name is None:
                plat_list = self._plugin.ban_list["available_platforms"]
            else:
                plat_list = [plat_name]

            chain = self._plugin.check_user(plat_list)

            if chain is None:
                for plat in plat_list:
                    if user_id in self._plugin.ban_list["prohibits"][plat]:
                        send_str = f"用户{user_id}的违规消息将会被清除"
                        self._plugin.ban_list["prohibits"][plat].pop(user_id)
                        chain = MessageChain().message(send_str)
                        await file_manager.write_file(self._plugin.ban_list)
                        break
                if not chain:
                    send_str = f"未找到用户{user_id}的违规消息，请使用/sf check来查看当前记录的所有平台的违规消息"
                    chain = MessageChain().message(send_str)
        await event.send(chain)

    async def notify(self, event: AstrMessageEvent, action: str = "check"):
        """查看或清空待通知的违规消息
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            action(str): 操作类型，"check"查看待通知消息，"clear"清空待通知消息
        """
        async with self._plugin._sf_lock:
            chain = self._plugin.check_user([event.get_platform_name()])

            if chain is None:
                if action == "check":
                    if not self._plugin.ban_list["pending_notifications"]:
                        chain = MessageChain().message("当前没有待通知的违规消息")
                    else:
                        notify_str = f"待通知的违规消息（共{len(self._plugin.ban_list['pending_notifications'])}条）：\n\n"
                        for idx, item in enumerate(
                            self._plugin.ban_list["pending_notifications"], 1
                        ):
                            time_str = datetime.datetime.fromtimestamp(
                                item["timestamp"]
                            ).strftime("%Y-%m-%d %H:%M:%S")
                            notify_str += f"[{idx}] {time_str}\n"
                            notify_str += (
                                f"平台：{item['platform']} | 用户：{item['user_id']}\n"
                            )
                            notify_str += f"消息：{item['message']}\n"
                            if item.get("reasoning"):
                                notify_str += f"审核理由：{item['reasoning']}\n"
                            notify_str += "\n"
                        notify_str += "使用 /sf notify clear 清空所有待通知消息"
                        chain = MessageChain().message(notify_str)
                elif action == "clear":
                    count = len(self._plugin.ban_list["pending_notifications"])
                    self._plugin.ban_list["pending_notifications"] = []
                    await file_manager.write_file(self._plugin.ban_list)
                    chain = MessageChain().message(f"已清空 {count} 条待通知的违规消息")
                else:
                    chain = MessageChain().message(
                        "无效的操作类型，请使用 'check' 或 'clear'"
                    )

        await event.send(chain)


command_manager = SmartFilterCommandFilter()
