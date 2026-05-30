import asyncio
import copy
import datetime
import time
import traceback

import pendulum

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from .core.context_parser import ContextParser
from .core.manager.file_manager import file_manager

# pyright: reportAttributeAccessIssue=false

SECONDS_PER_DAY = 86400


class SmartFilter(Star):
    """Smart_filter主类"""

    # 初始化与兼容层
    def __init__(self, context: Context, config: AstrBotConfig):
        """同步初始化行为，主要是为了定义自身的各种属性"""
        super().__init__(context)
        self.config = config
        """从AstrBot导入的插件配置"""
        self.ban_list = {}
        """插件核心持久化数据，记录了消息平台；违规记录；封禁信息等"""
        self._sf_lock = asyncio.Lock()
        """smart_filter全局互斥锁，保护ban_list读写和文件安全"""
        self._last_unban_ts: float = 0.0
        """记录上次全量清理过期封禁的时间戳，用于定期触发 unban_all"""
        self._unban_interval: float = 300.0
        """全量清理的最小间隔（秒），默认 5 分钟"""
        self._last_retry_ts: float = 0.0
        """记录上次重试通知的时间戳"""
        self._retry_task = None
        """后台重试任务"""
        self._admin_umo: str = ""
        """管理员的 unified_msg_origin，用于主动发送通知"""

    async def initialize(self):
        """异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        async with self._sf_lock:
            await file_manager.initialize(StarTools.get_data_dir())
            self.ban_list = await file_manager.read_file()
            await self.handle_update()
        # 配置验证
        if self.config["command_config"]["check_disshow_time"] <= 0:
            logger.error("配置参数check_disshow_time不能小于或等于0")
        if self.config["filter_config"]["filter_roles"] < 0:
            logger.error("配置参数filter_roles不能小于0")
        if self.config.get("notify_config", {}).get("enable_notify", False):
            if not self.config["notify_config"]["notify_umo"]:
                logger.warning("[违规通知]已启用违规通知功能，但管理员尚未注册")
            else:
                self._admin_umo = self.config["notify_config"]["notify_umo"]
                logger.info("[违规通知] 已启用违规通知功能，通知将发送至管理员")
                logger.info(
                    f"[违规通知] 重试配置：间隔 {self.config.get('notify_config', {}).get('notify_retry_intrvael', 60)}秒，最多重试 {self.config.get('notify_config', {}).get('notify_max_retries', 3)}次"
                )

                # 启动后台重试任务
                if self._retry_task is None or self._retry_task.done():
                    self._retry_task = asyncio.create_task(
                        self.retry_failed_notifications()
                    )
                    logger.info("[违规通知] 已启动通知重试后台任务")
        else:
            logger.info("[违规通知] 违规通知功能未启用")
        # 兼容层：将未记录时间的违规消息增加时间记录
        async with self._sf_lock:
            if "v2.3.0" not in self.ban_list["data_migrate_tag"]:
                logger.info("正在进行v2.3.0数据更新...")
                for plat, plat_info in self.ban_list["prohibits"].items():
                    for user, pro_list in plat_info.items():
                        for i, pro in enumerate(pro_list):
                            if not isinstance(pro, dict):
                                new_type = {
                                    "word": pro,
                                    "time": pendulum.now().timestamp(),
                                    "show": True,
                                }
                                pro_list[i] = new_type
                self.ban_list["data_migrate_tag"].append("v2.3.0")
                await file_manager.write_file(self.ban_list)
            # 数据清洗层
            if await self.refresh_all_times():
                await file_manager.write_file(self.ban_list)

    async def handle_update(self):
        """处理配置项的更新行为"""
        for key in list(self.ban_list["prohibits"]):
            if key not in self.config["platform_config"]["available_platforms"]:
                self.ban_list["prohibits"].pop(key, None)
        for key in list(self.ban_list["banners"]):
            if key not in self.config["platform_config"]["available_platforms"]:
                self.ban_list["banners"].pop(key, None)
        for key in list(self.ban_list["white_list"]):
            if key not in self.config["platform_config"]["available_platforms"]:
                self.ban_list["white_list"].pop(key, None)
        for key in self.config["platform_config"]["available_platforms"]:
            if key not in self.ban_list["prohibits"]:
                self.ban_list["prohibits"][key] = {}
            if key not in self.ban_list["banners"]:
                self.ban_list["banners"][key] = {}
            if key not in self.ban_list["white_list"]:
                self.ban_list["white_list"][key] = []
        self.ban_list["available_platforms"] = self.config["platform_config"][
            "available_platforms"
        ]
        await self.handle_white_list_update(self.config)
        await file_manager.write_file(self.ban_list)

    async def handle_white_list_update(self, config: AstrBotConfig):
        """处理白名单的更新行为"""
        for key in self.ban_list["available_platforms"]:
            self.ban_list["white_list"][key] = []
        for user_item in config["platform_config"]["white_list"]:
            if (
                user_item["__template_key"] != "white_list_temp"
                or user_item["platform"] not in self.ban_list["available_platforms"]
            ):
                continue
            self.ban_list["white_list"][user_item["platform"]].append(
                user_item["user_id"]
            )

    # 指令定义层
    @filter.command_group("sf")
    def sf(self):
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sf.command("ban")
    async def sf_ban(
        self,
        event: AstrMessageEvent,
        user_id: str,
        times: str,
        plat_name: str | None = None,
    ):
        """按照id封禁某位用户一段时间
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            user_id(str):需要封禁的用户id
            times(str):需要封禁的时间
            plat_name(str|None):封禁的消息平台，默认为None，即当前指令所在的消息平台
        """
        async with self._sf_lock:
            if plat_name is None:
                plat_name = event.platform_meta.name
            chain = self.check_user([plat_name], times)

            if chain is None:
                ban_time = pendulum.parse(times)
                state, detail = await self.ban_user(user_id, plat_name, ban_time)  # type:ignore
                if state == "Success":
                    chain = MessageChain().message(
                        f"用户{user_id}封禁成功，预计解封时间{detail}"
                    )
                else:
                    chain = MessageChain().message(f"{detail}")

                await file_manager.write_file(self.ban_list)
        await event.send(chain)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sf.command("unban")
    async def sf_unban(
        self, event: AstrMessageEvent, user_id: str, plat_name: str | None = None
    ):
        """按照id手动解封某位用户
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            user_id(str):需要解封禁的用户id
            plat_name(str|None):解封禁的消息平台，默认为None，即当前指令所在的消息平台
        """
        async with self._sf_lock:
            if plat_name is None:
                plat_name = event.platform_meta.name

            chain = self.check_user([plat_name])
            if chain is None:
                if user_id not in self.ban_list["banners"][plat_name]:
                    chain = MessageChain().message("该用户不在封禁列表中，请核实后重试")
                else:
                    self.ban_list["banners"][plat_name].pop(user_id)
                    if user_id in self.ban_list["prohibits"][plat_name]:
                        self.ban_list["prohibits"][plat_name].pop(user_id)
                    await file_manager.write_file(self.ban_list)

                    chain = MessageChain().message("解封操作成功！")
        await event.send(chain)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sf.command("bancount")
    async def sf_bancount(
        self,
        event: AstrMessageEvent,
        count: int,
        times: str,
        plat_name: str | None = None,
    ):
        """按照封禁次数自动封禁一批用户一段时间
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            count(int):封禁消息数量阈值
            times(str):需要封禁的时间
            plat_name(str|None):封禁的消息平台，默认为None，即当前指令所在的消息平台
        """
        async with self._sf_lock:
            if plat_name is not None:
                plat_list = [plat_name]
            else:
                plat_list = self.ban_list["available_platforms"]

            chain = self.check_user(plat_list, times)

            if chain is None:
                if await self.unban_all():
                    await file_manager.write_file(self.ban_list)
                ban_time = pendulum.parse(times)
                res_str = "封禁结果返回：\n"
                for plat in plat_list:
                    res_str += f"平台{plat}:\n"
                    for key, user in self.ban_list["prohibits"][plat].items():
                        if len(user) >= count:
                            res, detail = await self.ban_user(key, plat, ban_time)  # type:ignore
                            res_str += f"用户{key}:"
                            if res == "Success":
                                res_str += f"封禁成功，预计解封时间{detail}\n"
                            else:
                                res_str += f"{detail}\n"
                await file_manager.write_file(self.ban_list)
                chain = MessageChain().message(res_str)
        await event.send(chain)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sf.command("check")
    async def sf_check(self, event: AstrMessageEvent, plat_name: str | None = None):
        """查看近期用户发送的违规消息内容
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            plat_name(str|None):需要查看的消息平台，默认为None，即插件配置的所有消息平台
        """
        async with self._sf_lock:
            if plat_name is None:
                plat_list = self.config["platform_config"]["available_platforms"]
            else:
                plat_list = [plat_name]

            chain = self.check_user(plat_list)

            if chain is None:
                if await self.unban_all():
                    await file_manager.write_file(self.ban_list)
                prohibit_str = "最近的违规历史消息：\n"
                for key in plat_list:
                    flag = False
                    prohibit_str += f"消息平台{key}:\n"
                    for user, msg_list in self.ban_list["prohibits"][key].items():
                        if (
                            self.ban_list["banners"][key].get(user) is not None
                            and not self.config["command_config"]["check_show_ban"]
                        ):
                            continue
                        flag = True
                        prohibit_str += f"用户id：{user} 违规消息数:{len(msg_list)}条"
                        if self.ban_list["banners"][key].get(user) is not None:
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

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sf.command("checku")
    async def sf_checku(
        self, event: AstrMessageEvent, user_id: str, plat_name: str | None = None
    ):
        """查看特定用户的所有违规消息
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            user_id(str):用户ID
            plat_name(str|None):查看封禁用户的消息平台，默认为None，即当前指令所在的消息平台
        """
        async with self._sf_lock:
            if plat_name is not None:
                plat_list = [plat_name]
            else:
                plat_list = [event.get_platform_name()]

            chain = self.check_user(plat_list)

            if chain is None:
                if self.ban_list["prohibits"][plat_list[0]].get(user_id) is None:
                    chain = MessageChain().message(
                        f"用户{user_id}不存在，请检查消息平台是否正确"
                    )
                else:
                    prohibit_str = (
                        f"用户{user_id}的违规消息记录（在消息平台{plat_list[0]}）：\n"
                    )
                    for word in self.ban_list["prohibits"][plat_list[0]][user_id]:
                        prohibit_str += f"{word['word']}\n"
                    chain = MessageChain().message(prohibit_str.strip())
        await event.send(chain)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sf.command("checkban")
    async def sf_checkban(self, event: AstrMessageEvent, plat_name: str | None = None):
        """查看目前正在封禁的用户
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            plat_name(str|None):查看封禁用户的消息平台，默认为None，即插件配置的所有消息平台
        """
        async with self._sf_lock:
            if plat_name is not None:
                plat_list = [plat_name]
            else:
                plat_list = self.ban_list["available_platforms"]

            chain = self.check_user(plat_list)

            if chain is None:
                if await self.unban_all():
                    await file_manager.write_file(self.ban_list)

                ban_str = "目前封禁中的用户：\n"
                for key in plat_list:
                    ban_str += f"消息平台：{key}\n"
                    for user, times in self.ban_list["banners"][key].items():
                        except_time = datetime.datetime.fromtimestamp(times)
                        ban_str += f"用户{user},预计解封时间为{except_time.strftime('%Y年%m月%d日 %H:%M:%S')}\n"
                    if len(self.ban_list["banners"][key].items()) == 0:
                        ban_str += "当前平台没有正在封禁中的用户\n"
                chain = MessageChain().message(ban_str.strip())
        await event.send(chain)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sf.command("clear")
    async def sf_clear(
        self, event: AstrMessageEvent, user_id: str, plat_name: str | None = None
    ):
        """手动清理特定用户的违规记录（默认清理找到的第一个）
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            user_id(str):需要清空记录的用户id
            plat_name(str|None):用户所在的消息平台，默认为None，即当前指令所在的消息平台
            **注意：在id不存在冲突时，无论是否填写plat_name，本插件都可以正确找到用户，如果存在id冲突，不指定消息平台的情况下可能存在条件竞争的bug，请在这种情况下填写消息平台**
        """
        async with self._sf_lock:
            if plat_name is None:
                plat_list = self.ban_list["available_platforms"]
            else:
                plat_list = [plat_name]

            chain = self.check_user(plat_list)

            if chain is None:
                for plat in plat_list:
                    if user_id in self.ban_list["prohibits"][plat]:
                        send_str = f"用户{user_id}的违规消息将会被清除"
                        self.ban_list["prohibits"][plat].pop(user_id)
                        chain = MessageChain().message(send_str)
                        await file_manager.write_file(self.ban_list)
                        break
                if not chain:
                    send_str = f"未找到用户{user_id}的违规消息，请使用/sf check来查看当前记录的所有平台的违规消息"
                    chain = MessageChain().message(send_str)
        await event.send(chain)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sf.command("notify")
    async def sf_notify(self, event: AstrMessageEvent, action: str = "check"):
        """查看或清空待通知的违规消息
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            action(str): 操作类型，"check"查看待通知消息，"clear"清空待通知消息
        """
        async with self._sf_lock:
            chain = self.check_user([event.get_platform_name()])

            if chain is None:
                if action == "check":
                    if not self.ban_list["pending_notifications"]:
                        chain = MessageChain().message("当前没有待通知的违规消息")
                    else:
                        notify_str = f"待通知的违规消息（共{len(self.ban_list['pending_notifications'])}条）：\n\n"
                        for idx, item in enumerate(
                            self.ban_list["pending_notifications"], 1
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
                    count = len(self.ban_list["pending_notifications"])
                    self.ban_list["pending_notifications"] = []
                    await file_manager.write_file(self.ban_list)
                    chain = MessageChain().message(f"已清空 {count} 条待通知的违规消息")
                else:
                    chain = MessageChain().message(
                        "无效的操作类型，请使用 'check' 或 'clear'"
                    )

        await event.send(chain)

    # 违规消息主动通知相关模块
    async def send_notify_to_admin(self, violation_info: dict) -> bool:
        """立即发送违规通知给管理员

        Args:
            violation_info: 违规信息字典，包含 platform, user_id, message, reasoning, timestamp

        Returns:
            bool: 发送是否成功
        """
        try:
            # 检查是否已注册管理员 umo
            if not self._admin_umo:
                logger.warning("[违规通知] 管理员未注册，请先通过配置项添加会话umo注册")
                return False

            # 构建通知消息（优化格式，添加长度限制）
            time_str = datetime.datetime.fromtimestamp(
                violation_info["timestamp"]
            ).strftime("%Y-%m-%d %H:%M:%S")

            # 限制消息长度，避免过长
            msg_content = violation_info["message"]
            if len(msg_content) > 200:
                msg_content = msg_content[:200] + "..."

            notify_msg = "【违规消息通知】\n"
            notify_msg += "━━━━━━━━━━━━━━━━\n"
            notify_msg += f"⏰ 时间：{time_str}\n"
            notify_msg += f"📱 平台：{violation_info['platform']}\n"
            notify_msg += f"👤 用户：{violation_info['user_id']}\n"
            notify_msg += f"💬 消息：{msg_content}\n"
            notify_msg += f"📊 总计次数：{violation_info['counts']}\n"

            notify_msg += "━━━━━━━━━━━━━━━━\n"
            notify_msg += f"💡 使用 /sf checku {violation_info['user_id']} {violation_info['platform']} 查看详情"

            # 创建消息链
            chain = MessageChain().message(notify_msg)

            logger.info(f"[违规通知] 准备发送通知给管理员 (umo: {self._admin_umo})")

            # 使用保存的 admin_umo 发送消息
            await self.context.send_message(self._admin_umo, chain)

            logger.info("[违规通知] 通知发送成功")
            return True

        except Exception as e:
            logger.error(f"[违规通知] 发送失败: {e}")
            logger.debug(traceback.format_exc())
            return False

    async def retry_failed_notifications(self):
        """定期重试发送失败的通知"""
        while True:
            try:
                await asyncio.sleep(
                    self.config.get("notify_config", {}).get(
                        "notify_retry_intrvael", 60
                    )
                )

                async with self._sf_lock:
                    # 检查 ban_list 是否已初始化
                    if not self.ban_list or not self.ban_list.get(
                        "pending_notifications"
                    ):
                        continue

                    max_retries = self.config.get("notify_config", {}).get(
                        "notify_max_retries", 3
                    )
                    failed_items = []

                    for item in self.ban_list["pending_notifications"]:
                        retry_count = item.get("retry_count", 0)

                        if retry_count >= max_retries:
                            logger.warning(
                                f"[违规通知] 通知重试次数已达上限，丢弃: {item['user_id']}@{item['platform']}"
                            )
                            continue

                        # 尝试重新发送
                        logger.info(
                            f"[违规通知] 重试发送通知 (第{retry_count + 1}次): {item['user_id']}@{item['platform']}"
                        )
                        success = await self.send_notify_to_admin(item)

                        if not success:
                            item["retry_count"] = retry_count + 1
                            failed_items.append(item)

                    # 更新队列，只保留失败的
                    self.ban_list["pending_notifications"] = failed_items
                    if failed_items:
                        await file_manager.write_file(self.ban_list)
                        logger.info(
                            f"[违规通知] 队列中还有 {len(failed_items)} 条待重试通知"
                        )

            except asyncio.CancelledError:
                logger.info("[违规通知] 重试任务已取消")
                break
            except Exception as e:
                logger.error(f"[违规通知] 重试任务异常: {e}")
                logger.debug(traceback.format_exc())

    # 辅助函数，包括封禁用户的具体方法，检查输入参数，解封用户，拼接违规字符串
    async def ban_user(
        self, user_id: str, platform: str, times: pendulum.Duration
    ) -> tuple:
        """封禁某位用户一段时间。
        Args:
            user_id(str):封禁的用户id
            platform(str):封禁用户所在的消息平台
            times(pemdulum.Duration):封禁用户时间
        Returns:
            tuple:(status:str,detail:str)
            status:是否封禁成功，成功为'Success',否则为'Fail'
            detail:详细消息，如果status为'Success'返回格式化的解封时间，为'Fail'则返回失败原因(用户正在封禁中)
        """
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
        plat_name: list,
        times: str | None = None,
    ) -> MessageChain | None:
        """验证指令合法性
        Args:
            plat_name(list):指令涉及的所有消息平台的列表
            times(str|None):如果指令涉及时间，则传入ISO8601字符串，否则传入None
        Returns:
            MessageChain|None
            如果鉴权失败，则返回可以直接发送的消息链；鉴权成功返回None
        """
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

    async def unban_all(self):
        """解封到达封禁期限的用户，并且更新违规消息的状态
        Returns:
            bool:核心数据是否被更改
        """
        flag = False
        for key_p, item_plat in list(self.ban_list["banners"].items()):
            for key, item in list(item_plat.items()):
                if item <= time.time():
                    self.ban_list["banners"][key_p].pop(key)
                    self.ban_list["prohibits"][key_p].pop(key, None)
                    flag = True
        for key_p, item_plat in self.ban_list["prohibits"].items():
            for user, item in item_plat.items():
                for words in item:
                    if (
                        words["show"]
                        and pendulum.now().timestamp() - words["time"]
                        >= self.config["command_config"]["check_disshow_time"]
                        * SECONDS_PER_DAY
                    ):
                        words["show"] = False
                        flag = True
        return flag

    async def refresh_all_times(self):
        """当配置文件改动时，将原本不显示的用户判断修改状态，相反逻辑在unban_all()中实现，这里不重复实现
        Returns:
            flag(bool):是否有数据改动
        """
        flag = False
        for key_p, item_plat in self.ban_list["prohibits"].items():
            for user, item in item_plat.items():
                for words in item:
                    if (
                        not words["show"]
                        and pendulum.now().timestamp() - words["time"]
                        <= self.config["command_config"]["check_disshow_time"]
                        * SECONDS_PER_DAY
                    ):
                        words["show"] = True
                        flag = True
        return flag

    def create_speak_msg(self, getin: str) -> str:
        """创建违规消息的字符串
        Args:
            getin(str):llm或者fallback的回复消息
        Returns:
            str:增加前后缀后的完整回复消息
        """
        return (
            self.config["speak_config"]["speak_start"]
            + getin
            + self.config["speak_config"]["speak_end"]
        )

    # 插件销毁释放资源
    async def terminate(self):
        """插件销毁时的清理钩子，清理后台任务"""
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
            logger.info("[违规通知] 已停止通知重试后台任务")

    # 主钩子入口，检查用户输入
    @filter.on_llm_request()
    async def check_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """这是一个检查用户输入的函数
        Args:
            event(AstrMessageEvent):AstrBot消息事件
            req(ProviderRequest):AstrBot事件的llm请求详细信息
        """
        sender_id = event.get_sender_id()
        msg_str = event.get_message_str()
        sender_plat = event.platform_meta.name

        # 将所有 ban_list 访问都置于锁保护内，避免竞态条件
        async with self._sf_lock:
            if sender_plat not in self.ban_list["available_platforms"]:
                return
            if (
                event.get_group_id()
                and not self.config["filter_config"]["filter_group"]
            ):
                return

            # 定期全量清理过期封禁（避免字典无限膨胀）
            now = time.time()
            if now - self._last_unban_ts >= self._unban_interval:
                if await self.unban_all():
                    await file_manager.write_file(self.ban_list)
                self._last_unban_ts = now

            # 检查封禁状态
            ban_chain: MessageChain | None = None
            if sender_id in self.ban_list["banners"].get(sender_plat, {}):
                if time.time() >= self.ban_list["banners"][sender_plat][sender_id]:
                    # 封禁已到期，自动解封并清除违规记录
                    self.ban_list["banners"][sender_plat].pop(sender_id)
                    self.ban_list["prohibits"][sender_plat].pop(sender_id, None)
                    await file_manager.write_file(self.ban_list)
                else:
                    ban_ts = self.ban_list["banners"][sender_plat][sender_id]
                    except_time = datetime.datetime.fromtimestamp(ban_ts)
                    ban_chain = MessageChain().message(
                        f"你在被封禁中，具体情况请联系管理员。预计解封时间:{except_time.strftime('%Y年%m月%d日 %H:%M:%S')}"
                    )
                    event.stop_event()

            # 检查白名单（与封禁检查同在锁内，避免锁外读取竞态）
            in_white_list = sender_id in self.ban_list["white_list"].get(
                sender_plat, []
            )

        if ban_chain is not None:
            await event.send(ban_chain)
            return

        # 处理白名单逻辑
        if in_white_list:
            logger.info(f"用户{sender_id}在白名单内，跳过插件处理逻辑")
            return

        system_prompt = (
            await self.context.persona_manager.get_persona(
                self.config["filter_config"]["filter_prompt"]
            )
        ).system_prompt
        context_str = ContextParser(copy.deepcopy(req.contexts)).parse_context(
            self.config["filter_config"]["filter_roles"]
        )
        logger.debug(f"解析结果：\n{context_str}")
        msg = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{context_str}\n最近一轮用户输入:{msg_str}"},
        ]
        # logger.warning(f"获取personl类：{system_prompt}")
        try:
            filter_res = await self.context.llm_generate(
                chat_provider_id=self.config["filter_config"]["filter_provider"],
                contexts=msg,
            )
            if self.config["filter_config"]["filter_mode"]:
                if (
                    self.config["filter_config"]["filter_allow"]
                    in filter_res.completion_text
                ):
                    return
            else:
                if (
                    self.config["filter_config"]["filter_block"]
                    not in filter_res.completion_text
                ):
                    return
            filter_reasoning_res = filter_res.raw_completion or ""
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
            self.ban_list["prohibits"][sender_plat][sender_id].append(
                {"word": msg_str, "time": pendulum.now().timestamp(), "show": True}
            )

            await file_manager.write_file(self.ban_list)

        # 立即发送违规通知给管理员（如果开启了通知功能）
        if self.config.get("notify_config", {}).get("enable_notify", False):
            notification_item = {
                "timestamp": time.time(),
                "platform": sender_plat,
                "user_id": sender_id,
                "message": msg_str,
                "counts": len(self.ban_list["prohibits"][sender_plat][sender_id]),
                "retry_count": 0,
            }

            logger.info(f"[违规通知] 检测到违规消息：用户 {sender_id}@{sender_plat}")

            # 尝试立即发送通知
            success = await self.send_notify_to_admin(notification_item)

            # 如果发送失败，加入重试队列
            if not success:
                async with self._sf_lock:
                    self.ban_list["pending_notifications"].append(notification_item)
                    await file_manager.write_file(self.ban_list)
                    logger.warning(
                        f"[违规通知] 通知发送失败，已加入重试队列（当前队列长度：{len(self.ban_list['pending_notifications'])}）"
                    )

        if self.config["speak_config"]["enable_speak"]:
            speak_prompt_str = (
                await self.context.persona_manager.get_persona(
                    self.config["speak_config"]["speak_prompt"]
                )
            ).system_prompt
            msg = [
                {"role": "system", "content": speak_prompt_str},
                {"role": "user", "content": msg_str},
            ]
            try:
                speak_res = await self.context.llm_generate(
                    chat_provider_id=self.config["speak_config"]["speak_provider"],
                    contexts=msg,
                )
                speak_str = speak_res.completion_text
            except Exception:
                error_msg = traceback.format_exc()
                logger.error(error_msg)
                speak_str = self.config["speak_config"]["speak_fallback"]
            res_str = self.create_speak_msg(speak_str)
        else:
            logger.info("当前不使用第二个llm生成回复，使用默认回复")
            res_str = self.create_speak_msg(
                self.config["speak_config"]["speak_fallback"]
            )
        chain = MessageChain().message(res_str)
        await event.send(chain)
        if self.config["filter_config"]["debug_mode"]:
            chain = MessageChain().message(f"[DEBUG]raw content:{filter_reasoning_res}")
            await event.send(chain)
        event.stop_event()
