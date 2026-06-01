import asyncio
import copy
import json
import shutil
import time
import traceback

from astrbot.api import logger


class SmartFilterFileManager:
    def __init__(self):
        self.data_dir = None
        self.file_path = None
        self._fm_lock = asyncio.Lock()
        self._last_write_time = 0
        self._UPDATE_SECONDS = 5

    async def initialize(self, file_path):
        """文件管理器初始化
        Args:
            file_path(path):插件的文件存储路径
        """
        self.data_dir = file_path
        if not self.data_dir.exists():
            self.data_dir.mkdir(parents=True)
        self.file_path = self.data_dir / "banlist.json"
        if not self.file_path.exists():
            await self._create_default_file()

    async def _create_default_file(self):
        default_list = {
            "available_platforms": [],
            "prohibits": {},
            "banners": {},
            "white_list": {},
            "pending_notifications": [],
            "data_migrate_tag": [],
        }
        async with self._fm_lock:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(default_list, f, ensure_ascii=False, indent=4)
        return default_list

    async def read_file(self):
        """读取数据文件
        Returns:
            banlist(dict):标准化后的核心数据文件
        """
        async with self._fm_lock:
            try:
                with open(self.file_path, encoding="utf-8") as f:
                    banlist = json.load(f)
                    return self._check_list(banlist)
            except json.JSONDecodeError:
                logger.error("读取smart_filter文件失败：json解码错误，正在备份文件...")
                backup_path = self.data_dir / f"banlist.json.backup.{int(time.time())}"
                try:
                    shutil.copy(self.file_path, backup_path)
                    logger.info(f"已备份损坏文件到: {backup_path}")
                except Exception:
                    logger.warning("smart_filter数据文件备份失败")
                    error_msg = traceback.format_exc()
                    logger.error(error_msg)
            except Exception:
                logger.error("smart_filter文件读取失败")
                error_msg = traceback.format_exc()
                logger.error(error_msg)
        return await self._create_default_file()

    def _check_list(self, banlist):
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
                "name": "data_migrate_tag",
                "type": list,
                "default": [],
            },
        ]
        for std_item in legal_list_format:
            if not (
                banlist.get(std_item["name"], None) is not None
                and isinstance(banlist[std_item["name"]], std_item["type"])
            ):
                banlist[std_item["name"]] = copy.deepcopy(std_item["default"])
        return banlist

    async def write_file(self, banlist, force: bool = False):
        """写入数据文件
        Args:
            banlist(dict):需要写入的核心数据文件
            force(bool):是否忽略文件读写缓冲时间强制落盘
        """
        async with self._fm_lock:
            if time.time() - self._last_write_time >= self._UPDATE_SECONDS or force:
                try:
                    with open(self.file_path, "w", encoding="utf-8") as f:
                        json.dump(banlist, f, ensure_ascii=False, indent=4)
                    self._last_write_time = time.time()
                except Exception as e:
                    logger.error(e)
                    raise e


file_manager = SmartFilterFileManager()
