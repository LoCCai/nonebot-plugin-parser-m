import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Union

# 数据保存路径
DATA_PATH = Path("data/taptap_subscribe")
CONFIG_FILE = DATA_PATH / "push_config.json"

class ConfigManager:
    def __init__(self):
        self._ensure_file()
        self.config = self._load_config()

    def _ensure_file(self):
        if not DATA_PATH.exists():
            DATA_PATH.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            default_config = {
                "subscriptions": {},  # 结构: {"user_id": {"groups": [], "friends": []}}
                "history": {}         # 结构: {"user_id": "last_post_id"}
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(default_config, f, ensure_ascii=False, indent=4)

    def _load_config(self) -> Dict:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"subscriptions": {}, "history": {}}

    def _save_config(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=4)

    def add_subscription(self, target_id: str, sub_type: str, sub_id: int) -> str:
        """
        添加订阅
        target_id: TapTap用户ID
        sub_type: 'groups' 或 'friends'
        sub_id: 群号或QQ号
        """
        subs = self.config["subscriptions"]
        if target_id not in subs:
            subs[target_id] = {"groups": [], "friends": []}
        
        target_list = subs[target_id].get(sub_type, [])
        if sub_id in target_list:
            return "已存在该订阅，无需重复添加"
        
        target_list.append(sub_id)
        subs[target_id][sub_type] = target_list
        self._save_config()
        return f"订阅成功！当 TapTap 用户 {target_id} 更新时将推送消息。"

    def del_subscription(self, target_id: str, sub_type: str, sub_id: int) -> str:
        """取消订阅"""
        subs = self.config["subscriptions"]
        if target_id not in subs:
            return "未找到该用户的订阅记录"
        
        target_list = subs[target_id].get(sub_type, [])
        if sub_id not in target_list:
            return "未订阅该用户"
        
        target_list.remove(sub_id)
        
        # 如果该用户没有任何订阅者了，清理掉key
        if not subs[target_id]["groups"] and not subs[target_id]["friends"]:
            del subs[target_id]
            # 同时清理历史记录
            if target_id in self.config["history"]:
                del self.config["history"][target_id]
        
        self._save_config()
        return "取消订阅成功"

    def get_all_subscriptions(self) -> Dict:
        return self.config["subscriptions"]

    def get_last_id(self, target_id: str) -> Optional[str]:
        return self.config["history"].get(target_id)

    def update_last_id(self, target_id: str, post_id: str):
        self.config["history"][target_id] = str(post_id)
        self._save_config()

config_manager = ConfigManager()