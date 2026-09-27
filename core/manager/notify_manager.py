import asyncio
import copy
import uuid
import traceback

from astrbot.api import logger
from astrbot.api.event import MessageChain


class NotifyManager:
    """违规通知管理器（全局单实例）

    统一负责违规消息通知与DEBUG模式消息的主动发送：

    - 消息发送失败后自动进入内存重试队列，按配置的间隔与次数自动重试，超过上限后丢弃；
    - 插件停止（stop）时将尚未发送成功的消息快照到 ``ban_list["pending_notifications"]``，
      下次插件初始化时可通过 ``resume()`` 重新装入队列续传；
    - 绑定到新的插件实例（如配置更新后插件重载）时会重新读取配置并启动新的后台任务。
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        """单例：进程内只保留一个管理器实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._plugin = None
        return cls._instance

    def __init__(self, plugin):
        """绑定插件实例并读取通知相关配置

        Args:
            plugin: 插件主类实例（SmartFilter）
        """
        task = getattr(self, "_retry_task", None)
        if self._plugin is plugin and task is not None and not task.done():
            return
        self._stop_retry_task()
        self._plugin = plugin
        self.admin_umo: str = self._plugin.config["notify_config"]["notify_umo"]
        self.retry_times: int = self._plugin.config["notify_config"][
            "notify_max_retries"
        ]
        self.retry_interval: int = self._plugin.config["notify_config"][
            "notify_retry_intrvael"
        ]
        self._retry_content: list[dict] = []
        self._nm_lock = asyncio.Lock()
        self._retry_task: asyncio.Task = asyncio.create_task(self.retry_task())

    def _stop_retry_task(self):
        """取消当前绑定的后台重试任务（仅在任务仍存活于当前事件循环时）"""
        task = getattr(self, "_retry_task", None)
        if task is None or task.done():
            return
        try:
            if task.get_loop() is asyncio.get_running_loop():
                task.cancel()
        except RuntimeError:
            pass

    async def send_once(self, msg: str, is_retry: bool) -> bool:
        """向管理员会话发送一条消息

        Args:
            msg(str): 完整的消息文本
            is_retry(bool): 是否为重试发送，重试失败不会重复入队

        Returns:
            bool: 是否发送成功
        """
        try:
            await self._plugin.context.send_message(
                self.admin_umo, self._add_at_in_msg(MessageChain().message(msg))
            )
            return True
        except Exception:
            logger.error(
                f"消息“{msg}”发送至umo：{self.admin_umo}的任务失败，请检查相关配置，详细堆栈消息：\n{traceback.format_exc()}"
            )
            if not is_retry:
                await self._add_retry(msg)
            return False

    def _add_at_in_msg(self, msg: MessageChain) -> MessageChain:
        """给消息通知加上@消息组件

        Args:
            msg(MessageChain): 未添加@的消息链

        Returns:
            new_chain(MessageChain): 完整的消息链
        """
        if not self._plugin.config["notify_config"]["notify_at_ids"]:
            return msg
        new_chain = MessageChain()
        for user_id in self._plugin.config["notify_config"]["notify_at_ids"]:
            new_chain.at("", user_id)
        new_chain.message("\u200b\n\u200b")
        new_chain.chain.extend(msg.chain)
        return new_chain

    async def _add_retry(self, msg: str):
        """向内存重试队列追加一条待发送消息

        Args:
            msg(str): 完整的消息文本
        """
        async with self._nm_lock:
            self._retry_content.append(
                {"id": str(uuid.uuid4()), "times": 0, "content": msg}
            )

    async def _pop_item(self, item: dict):
        """按id从内存重试队列移除条目

        Args:
            item(dict): 待移除的队列条目
        """
        async with self._nm_lock:
            for i, itm in enumerate(self._retry_content):
                if itm["id"] == item["id"]:
                    self._retry_content.pop(i)
                    break

    async def _add_item_times(self, item: dict):
        """将条目的失败次数加一

        Args:
            item(dict): 需要累计失败次数的队列条目
        """
        async with self._nm_lock:
            for itm in self._retry_content:
                if itm["id"] == item["id"]:
                    itm["times"] += 1
                    break

    async def retry_task(self):
        """后台重试循环：按配置间隔轮询并重发队列中尚未成功的消息，超过上限后丢弃"""
        while True:
            # 先休眠一个间隔，避免启动或重启时立即触发发送
            await asyncio.sleep(self.retry_interval)
            async with self._nm_lock:
                if not self._retry_content:
                    continue
                copyed_task_list = copy.deepcopy(self._retry_content)

            failed_tasks = []
            for item in copyed_task_list:
                if item["times"] >= self.retry_times:
                    logger.warning(
                        f"通知重试次数已达上限，丢弃：{(item['content'] or '')[:60]}"
                    )
                    await self._pop_item(item)
                    continue
                if await self.send_once(item["content"], True):
                    await self._pop_item(item)
                else:
                    failed_tasks.append(item)
            for item in failed_tasks:
                await self._add_item_times(item)

    async def add_notify(self, msg: str):
        """立即发送一条通知消息，发送失败自动加入内存重试队列

        Args:
            msg(str): 完整的通知消息文本
        """
        await self.send_once(msg, False)

    async def resume(self) -> int:
        """将 ``ban_list["pending_notifications"]`` 中的待发送消息重新装入内存重试队列

        应在插件初始化（initialize）时调用，条目格式与 ``stop()`` 落盘的快照一致。

        Returns:
            int: 恢复的消息条数
        """
        pending = self._plugin.ban_list.get("pending_notifications", []) or []
        restored = [
            copy.deepcopy(item)
            for item in pending
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        ]
        async with self._nm_lock:
            self._retry_content.extend(restored)
        return len(restored)

    async def get_pending(self) -> list:
        """返回当前待发送队列的深拷贝（供/sf notify指令查看）

        Returns:
            list[dict]: 待发送通知条目列表，每项包含id、times、content
        """
        async with self._nm_lock:
            return copy.deepcopy(self._retry_content)

    async def clear(self):
        """清空当前待发送队列（供/sf notify clear指令使用）"""
        async with self._nm_lock:
            self._retry_content.clear()

    async def stop(self):
        """停止后台重试任务，并将尚未发送成功的消息快照到 ``ban_list["pending_notifications"]``

        应在插件销毁（terminate）时调用，随后由调用方负责将ban_list落盘。
        """
        try:
            if not self._retry_task.done():
                self._retry_task.cancel()
                await self._retry_task
        except asyncio.CancelledError:
            pass
        self._plugin.ban_list["pending_notifications"] = copy.deepcopy(
            self._retry_content
        )
