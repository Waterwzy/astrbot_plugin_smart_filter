import asyncio
import copy
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
        """同步初始化行为，主要是为了定义自身的各种属性"""
        super().__init__(context)
        self.config = config
        self.ban_list = None
        self._sf_lock = asyncio.Lock()
        # 记录上次全量清理过期封禁的时间戳，用于定期触发 unban_all
        self._last_unban_ts: float = 0.0
        # 全量清理的最小间隔（秒），默认 5 分钟
        self._unban_interval: float = 300.0
        # 记录上次重试通知的时间戳
        self._last_retry_ts: float = 0.0
        # 后台重试任务
        self._retry_task = None
        # 管理员的 unified_msg_origin，用于主动发送通知
        self._admin_umo: str = None

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
                logger.warning("[违规通知] 管理员未注册，请先使用 /sf_register_admin 命令注册")
                return False

            # 构建通知消息（优化格式，添加长度限制）
            time_str = datetime.datetime.fromtimestamp(violation_info["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")

            # 限制消息长度，避免过长
            msg_content = violation_info['message']
            if len(msg_content) > 200:
                msg_content = msg_content[:200] + "..."

            notify_msg = f"【违规消息通知】\n"
            notify_msg += f"━━━━━━━━━━━━━━━━\n"
            notify_msg += f"⏰ 时间：{time_str}\n"
            notify_msg += f"📱 平台：{violation_info['platform']}\n"
            notify_msg += f"👤 用户：{violation_info['user_id']}\n"
            notify_msg += f"💬 消息：{msg_content}\n"

            if violation_info.get('reasoning'):
                reasoning = violation_info['reasoning']
                if len(reasoning) > 150:
                    reasoning = reasoning[:150] + "..."
                notify_msg += f"🔍 理由：{reasoning}\n"

            notify_msg += f"━━━━━━━━━━━━━━━━\n"
            notify_msg += f"💡 使用 /sf_check {violation_info['user_id']} 查看详情"

            # 创建消息链
            chain = MessageChain().message(notify_msg)

            logger.info(f"[违规通知] 准备发送通知给管理员 (umo: {self._admin_umo})")

            # 使用保存的 admin_umo 发送消息
            await self.context.send_message(self._admin_umo, chain)

            logger.info(f"[违规通知] 通知发送成功")
            return True

        except Exception as e:
            logger.error(f"[违规通知] 发送失败: {e}")
            logger.debug(traceback.format_exc())
            return False

    async def retry_failed_notifications(self):
        """定期重试发送失败的通知"""
        while True:
            try:
                await asyncio.sleep(self.config.get("notify_retry_interval", 60))

                async with self._sf_lock:
                    # 检查 ban_list 是否已初始化
                    if not self.ban_list or not self.ban_list.get("pending_notifications"):
                        continue

                    max_retries = self.config.get("notify_max_retries", 3)
                    failed_items = []

                    for item in self.ban_list["pending_notifications"]:
                        retry_count = item.get("retry_count", 0)

                        if retry_count >= max_retries:
                            logger.warning(f"[违规通知] 通知重试次数已达上限，丢弃: {item['user_id']}@{item['platform']}")
                            continue

                        # 尝试重新发送
                        logger.info(f"[违规通知] 重试发送通知 (第{retry_count + 1}次): {item['user_id']}@{item['platform']}")
                        success = await self.send_notify_to_admin(item)

                        if not success:
                            item["retry_count"] = retry_count + 1
                            failed_items.append(item)

                    # 更新队列，只保留失败的
                    self.ban_list["pending_notifications"] = failed_items
                    if failed_items:
                        self.write_ban(self.ban_list)
                        logger.info(f"[违规通知] 队列中还有 {len(failed_items)} 条待重试通知")

            except asyncio.CancelledError:
                logger.info("[违规通知] 重试任务已取消")
                break
            except Exception as e:
                logger.error(f"[违规通知] 重试任务异常: {e}")
                logger.debug(traceback.format_exc())

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        async with self._sf_lock:
            self.ban_list = self.get_ban_list()
            await self.handle_update()

            # 从持久化存储中恢复管理员 umo
            if self.ban_list.get("admin_umo"):
                self._admin_umo = self.ban_list["admin_umo"]
                logger.info(f"[违规通知] 已恢复管理员注册信息，umo: {self._admin_umo}")

        # 配置验证
        if self.config.get("enable_notify", False):
            if not self._admin_umo:
                logger.warning("[违规通知] 违规通知已启用，但管理员尚未注册")
                logger.warning("[违规通知] 请使用 /sf_register_admin 命令注册管理员以接收通知")
            else:
                logger.info(f"[违规通知] 已启用违规通知功能，通知将发送至管理员")
                logger.info(f"[违规通知] 重试配置：间隔 {self.config.get('notify_retry_interval', 60)}秒，最多重试 {self.config.get('notify_max_retries', 3)}次")

                # 启动后台重试任务
                if self._retry_task is None or self._retry_task.done():
                    self._retry_task = asyncio.create_task(self.retry_failed_notifications())
                    logger.info("[违规通知] 已启动通知重试后台任务")
        else:
            logger.info("[违规通知] 违规通知功能未启用")

    async def handle_update(self):
        """处理配置项的更新行为"""
        for key in list(self.ban_list["prohibits"]):
            if key not in self.config["available_platforms"]:
                self.ban_list["prohibits"].pop(key, None)
        for key in list(self.ban_list["banners"]):
            if key not in self.config["available_platforms"]:
                self.ban_list["banners"].pop(key, None)
        for key in list(self.ban_list["white_list"]):
            if key not in self.config["available_platforms"]:
                self.ban_list["white_list"].pop(key, None)
        for key in self.config["available_platforms"]:
            if key not in self.ban_list["prohibits"]:
                self.ban_list["prohibits"][key] = {}
            if key not in self.ban_list["banners"]:
                self.ban_list["banners"][key] = {}
            if key not in self.ban_list["white_list"]:
                self.ban_list["white_list"][key] = []
        self.ban_list["available_platforms"] = self.config["available_platforms"]
        await self.handle_white_list_update(self.config)
        self.write_ban(self.ban_list)

    async def handle_white_list_update(self, config: AstrBotConfig):
        """处理白名单的更新行为"""
        for key in self.ban_list["available_platforms"]:
            self.ban_list["white_list"][key] = []
        for user_item in config["white_list"]:
            if (
                user_item["__template_key"] != "white_list_temp"
                or user_item["platform"] not in self.ban_list["available_platforms"]
            ):
                continue
            self.ban_list["white_list"][user_item["platform"]].append(
                user_item["user_id"]
            )

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
            logger.warning(f"非管理员用户尝试使用管理命令，user id:{user_id}")
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
            else:
                plat_name = [plat_name]

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

    @filter.command("sf_register_admin")
    async def sf_register_admin(self, event: AstrMessageEvent):
        """注册管理员，保存 unified_msg_origin 用于接收违规通知

        管理员需要先执行此命令，才能接收主动推送的违规通知
        """
        async with self._sf_lock:
            config = self.context.get_config(event.unified_msg_origin)
            sender_id = event.get_sender_id()

            # 权限检查
            if sender_id not in config["admins_id"]:
                chain = MessageChain().message("此命令仅管理员有权使用")
                await event.send(chain)
                return

            # 保存管理员的 umo
            self._admin_umo = event.unified_msg_origin

            # 持久化保存到 banlist.json
            self.ban_list["admin_umo"] = self._admin_umo
            self.write_ban(self.ban_list)

            logger.info(f"[违规通知] 管理员已注册，umo: {self._admin_umo}")

            chain = MessageChain().message(
                f"✅ 管理员注册成功！\n"
                f"现在可以接收违规消息的主动推送通知了。\n\n"
                f"会话ID: {self._admin_umo}"
            )
            await event.send(chain)

    @filter.command("sf_notify")
    async def sf_notify(self, event: AstrMessageEvent, action: str = "check"):
        """查看或清空待通知的违规消息

        参数:
            action: 操作类型，"check"查看待通知消息，"clear"清空待通知消息
        """
        async with self._sf_lock:
            config = self.context.get_config(event.unified_msg_origin)
            sender_id = event.get_sender_id()

            # 权限检查
            if sender_id not in config["admins_id"]:
                chain = MessageChain().message("此命令仅管理员有权使用")
                await event.send(chain)
                return

            if action == "check":
                if not self.ban_list["pending_notifications"]:
                    chain = MessageChain().message("当前没有待通知的违规消息")
                else:
                    notify_str = f"待通知的违规消息（共{len(self.ban_list['pending_notifications'])}条）：\n\n"
                    for idx, item in enumerate(self.ban_list["pending_notifications"], 1):
                        time_str = datetime.datetime.fromtimestamp(item["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                        notify_str += f"[{idx}] {time_str}\n"
                        notify_str += f"平台：{item['platform']} | 用户：{item['user_id']}\n"
                        notify_str += f"消息：{item['message']}\n"
                        if item.get("reasoning"):
                            notify_str += f"审核理由：{item['reasoning']}\n"
                        notify_str += "\n"
                    notify_str += "使用 /sf_notify clear 清空所有待通知消息"
                    chain = MessageChain().message(notify_str)
            elif action == "clear":
                count = len(self.ban_list["pending_notifications"])
                self.ban_list["pending_notifications"] = []
                self.write_ban(self.ban_list)
                chain = MessageChain().message(f"已清空 {count} 条待通知的违规消息")
            else:
                chain = MessageChain().message("无效的操作类型，请使用 'check' 或 'clear'")

        await event.send(chain)

    def check_list_format(self, ban_list):
        legal_list_format = [
            {
                "name": "available_platforms",
                "type": list,
                "default": [],
            },
            {
                "name": "prohibits",
                "type": dict,
                "default": {},
            },
            {
                "name": "banners",
                "type": dict,
                "default": {},
            },
            {
                "name": "white_list",
                "type": dict,
                "default": {},
            },
            {
                "name": "pending_notifications",
                "type": list,
                "default": [],
            },
            {
                "name": "admin_umo",
                "type": str,
                "default": None,
            },
        ]
        for std_item in legal_list_format:
            if not (
                ban_list.get(std_item["name"], None) is not None
                and isinstance(ban_list[std_item["name"]], std_item["type"])
            ):
                ban_list[std_item["name"]] = copy.deepcopy(std_item["default"])
        return ban_list

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
                "white_list": {},
                "pending_notifications": [],
                "admin_umo": None,
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(default_banlist, f, ensure_ascii=False, indent=4)
            return default_banlist

        try:
            with open(file_path, encoding="utf-8") as f:
                banlist = json.load(f)
                banlist = self.check_list_format(banlist)
        except json.JSONDecodeError as e:
            logger.error(f"[违规通知] banlist.json 文件损坏，正在备份并重新创建: {e}")
            # 备份损坏的文件
            backup_path = data_dir / f"banlist.json.backup.{int(time.time())}"
            try:
                import shutil
                shutil.copy(file_path, backup_path)
                logger.info(f"[违规通知] 已备份损坏文件到: {backup_path}")
            except Exception as backup_error:
                logger.warning(f"[违规通知] 备份失败: {backup_error}")

            # 创建新的默认文件
            default_banlist = {
                "available_platforms": [],
                "prohibits": {},
                "banners": {},
                "white_list": {},
                "pending_notifications": [],
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(default_banlist, f, ensure_ascii=False, indent=4)
            logger.info("[违规通知] 已重新创建 banlist.json 文件")
            return default_banlist
        except Exception as e:
            logger.error(f"[违规通知] 读取 banlist.json 失败: {e}")
            logger.error(traceback.format_exc())
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
        """插件销毁时的清理钩子，清理后台任务"""
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
            logger.info("[违规通知] 已停止通知重试后台任务")

    # 注册指令的装饰器。
    @filter.on_llm_request()
    async def check_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """这是一个检查用户输入的函数"""  # 这是 handler 的描述，将会被解析方便用户了解插件内容。建议填写。
        sender_id = event.get_sender_id()
        msg_str = event.get_message_str()
        sender_plat = event.platform_meta.name

        # 将所有 ban_list 访问都置于锁保护内，避免竞态条件
        async with self._sf_lock:
            if sender_plat not in self.ban_list["available_platforms"]:
                return

            # 定期全量清理过期封禁（避免字典无限膨胀）
            now = time.time()
            if now - self._last_unban_ts >= self._unban_interval:
                if await self.unban_all():
                    self.write_ban(self.ban_list)
                self._last_unban_ts = now

            # 检查封禁状态
            ban_chain: MessageChain | None = None
            if sender_id in self.ban_list["banners"].get(sender_plat, {}):
                if time.time() >= self.ban_list["banners"][sender_plat][sender_id]:
                    # 封禁已到期，自动解封并清除违规记录
                    self.ban_list["banners"][sender_plat].pop(sender_id)
                    self.ban_list["prohibits"][sender_plat].pop(sender_id, None)
                    self.write_ban(self.ban_list)
                else:
                    ban_ts = self.ban_list["banners"][sender_plat][sender_id]
                    except_time = datetime.datetime.fromtimestamp(ban_ts)
                    ban_chain = MessageChain().message(
                        f"你在被封禁中，具体情况请联系管理员。预计解封时间:{except_time.strftime('%Y年%m月%d日 %H:%M:%S')}"
                    )
                    event.stop_event()

            # 检查白名单（与封禁检查同在锁内，避免锁外读取竞态）
            in_white_list = sender_id in self.ban_list["white_list"].get(sender_plat, [])

        if ban_chain is not None:
            await event.send(ban_chain)
            return

        # 处理白名单逻辑
        if in_white_list:
            logger.info(f"用户{sender_id}在白名单内，跳过插件处理逻辑")
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
            self.ban_list["prohibits"][sender_plat][sender_id].append(msg_str)

            self.write_ban(self.ban_list)

        # 立即发送违规通知给管理员（如果开启了通知功能）
        if self.config.get("enable_notify", False):
            # 提取 reasoning 文本内容
            reasoning_text = ""
            if filter_reasoning_res:
                # filter_reasoning_res 可能是字符串或对象，需要安全提取
                if isinstance(filter_reasoning_res, str):
                    reasoning_text = filter_reasoning_res
                else:
                    # 如果是对象，尝试获取文本内容
                    reasoning_text = str(filter_reasoning_res) if filter_reasoning_res else ""

            notification_item = {
                "timestamp": time.time(),
                "platform": sender_plat,
                "user_id": sender_id,
                "message": msg_str,
                "reasoning": reasoning_text,
                "retry_count": 0
            }

            logger.info(f"[违规通知] 检测到违规消息：用户 {sender_id}@{sender_plat}")

            # 尝试立即发送通知
            success = await self.send_notify_to_admin(notification_item)

            # 如果发送失败，加入重试队列
            if not success:
                async with self._sf_lock:
                    self.ban_list["pending_notifications"].append(notification_item)
                    self.write_ban(self.ban_list)
                    logger.warning(f"[违规通知] 通知发送失败，已加入重试队列（当前队列长度：{len(self.ban_list['pending_notifications'])}）")

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
