from typing import Any, ClassVar
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

from nonebot import get_driver, require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_plugin_cache_dir, get_plugin_config_dir, get_plugin_data_dir

from .utils import _store


__plugin_meta__ = PluginMetadata(
    name="nonebot-plugin-parser",
    description="Nonebot2 链接分享自动解析插件",
    usage="无需任何命令，直接发送链接即可",
    homepage="https://github.com/fllesser/nonebot-plugin-parser",
    type="application",
    config=lambda: Config,
    supported_adapters={"~onebot.v11", "~onebot.v12"},
)


class EmojiStyle(str, Enum):
    APPLE = "apple"
    GOOGLE = "google"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    MESSENGER = "messenger"


# 默认配置
ELK_SH_CDN = "https://emojicdn.elk.sh"
MQRIO_DEV_CDN = "https://emoji-cdn.mqrio.dev"

_driver = get_driver()
_nickname = _driver.config.nickname
_cache_dir: Path = _store.get_plugin_cache_dir()
_config_dir: Path = _store.get_plugin_config_dir()
_data_dir: Path = _store.get_plugin_data_dir()


@dataclass
class Config:
    # 基础配置
    nickname: str
    