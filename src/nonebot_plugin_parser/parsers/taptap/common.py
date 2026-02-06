import asyncio
import json
import re
import httpx
from datetime import datetime
from typing import Optional, Dict, Any, List, Set

from nonebot import logger
from ..base import BaseParser, handle
from ..data import Platform, Author, MediaContent, ImageContent, VideoContent
from ...exception import ParseException
from ...constants import PlatformEnum

class TapTapParser(BaseParser):
    """TapTap 解析器"""
    
    platform = Platform(
        name=PlatformEnum.TAPTAP.value,
        display_name="TapTap"
    )
    
    def __init__(self):
        super().__init__()
        self.base_url = "https://www.taptap.cn"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        }
    
    def _resolve_nuxt_value(self, root_data: list, value: Any) -> Any:
        """Nuxt数据解压"""
        if isinstance(value, int):
            if 0 <= value < len(root_data):
                return root_data[value]
            return value
        return value
    
    async def _fetch_nuxt_data(self, url: str) -> list:
        """获取页面的 Nuxt 数据 (纯 HTTP 请求版)"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers, follow_redirects=True)
                response.raise_for_status()
                response_text = response.text

                nuxt_data: list = []
                
                # 方式1: 尝试原始的 __NUXT_DATA__ 提取
                if "__NUXT_DATA__" in response_text:
                    patterns = [
                        r'<script id="__NUXT_DATA__"[^>]*>(.*?)</script>',
                        r'<script[^>]*id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>',
                        r'<script[^>]*>(.*?__NUXT_DATA__.*?)</script>',
                    ]
                    
                    for pattern in patterns:
                        match = re.search(pattern, response_text, re.DOTALL)
                        if match:
                            try:
                                # 提取json数据部分
                                json_match = re.search(r'__NUXT_DATA__\s*=\s*(\[.*?\])', match.group(1), re.DOTALL)
                                if json_match:
                                    parsed_data = json.loads(json_match.group(1))
                                    if isinstance(parsed_data, list):
                                        nuxt_data = parsed_data
                                        break
                                # 尝试直接解析整个匹配内容
                                parsed_data = json.loads(match.group(1))
                                if isinstance(parsed_data, list):
                                    nuxt_data = parsed_data
                                    break
                            except json.JSONDecodeError:
                                continue
                
                # 方式2: 尝试从 window.__NUXT__ 中提取
                if not nuxt_data and "window.__NUXT__" in response_text:
                    match = re.search(r'window\.__NUXT__\s*=\s*(\[.*?\])', response_text, re.DOTALL)
                    if match:
                        try:
                            parsed_data = json.loads(match.group(1))
                            if isinstance(parsed_data, list):
                                nuxt_data = parsed_data
                        except json.JSONDecodeError:
                            pass

                # 方式3: 尝试从 window.__NUXT_DATA__ 中提取
                if not nuxt_data and "window.__NUXT_DATA__" in response_text:
                    match = re.search(r'window\.__NUXT_DATA__\s*=\s*(\[.*?\])', response_text, re.DOTALL)
                    if match:
                        try:
                            parsed_data = json.loads(match.group(1))
                            if isinstance(parsed_data, list):
                                nuxt_data = parsed_data
                        except json.JSONDecodeError:
                            pass
                
                if not nuxt_data:
                    logger.warning(f"[TapTap] 无法在 HTML 中找到 Nuxt 数据: {url}")
                    return []
                
                return nuxt_data

        except Exception as e:
            logger.error(f"[TapTap] 获取页面 HTML 失败: {url}, error: {e}")
            return []
    
    async def _fetch_api_data(self, post_id: str) -> Optional[Dict[str, Any]]:
        """从TapTap API获取动态详情"""
        api_url = f"https://www.taptap.cn/webapiv2/moment/v3/detail"
        params = {
            "id": post_id,
            "X-UA": "V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC&DS=Android&UID=f69478c8-27a3-4581-877b-45ade0e61b0b&OS=Windows&OSV=10&DT=PC"
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(api_url, params=params, headers=self.headers)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"[TapTap] API请求失败: {e}")
            return None
    
    async def _fetch_comments(self, post_id: str) -> Optional[List[Dict[str, Any]]]:
        """从TapTap API获取评论数据"""
        api_url = "https://www.taptap.cn/webapiv2/moment-comment/v1/by-moment"
        params = {
            "moment_id": post_id,
            "sort": "rank",
            "order": "desc",
            "regulate_all": "false",
            "X-UA": "V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC&DS=Android&UID=f69478c8-27a3-4581-877b-45ade0e61b0b&OS=Windows&OSV=10&DT=PC"
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(api_url, params=params, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                if data.get("success") and data.get("data"):
                    return data["data"].get("list", [])
                return []
        except Exception as e:
            logger.error(f"[TapTap] 获取评论数据失败: {e}")
            return None
    
    async def _parse_post_detail(self, post_id: str) -> Dict[str, Any]:
        """解析动态详情"""
        url = f"{self.base_url}/moment/{post_id}"
        
        # 初始化结果结构
        result = {
            "id": post_id,
            "url": url,
            "title": "",
            "summary": "",
            "content_items": [],
            "images": [],
            "videos": [],
            "video_id": None,
            "video_duration": None,
            "author": {
                "name": "",
                "avatar": "",
                "app_title": "",
                "app_icon": "",
                "honor_title": "",
                "honor_obj_id": "",
                "honor_obj_type": ""
            },
            "created_time": "",
            "publish_time": "",
            "stats": {
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "views": 0,
                "plays": 0
            },
            "video_cover": "",
            "comments": [],
            "seo_keywords": "",
            "footer_images": [],
            "app": {},
            "extra": {}
        }
        
        # ==========================================================
        # 1. 尝试使用API获取数据
        # ==========================================================
        api_data = await self._fetch_api_data(post_id)
        if api_data and api_data.get("success"):
            logger.info(f"[TapTap] 使用API获取数据成功")
            data = api_data.get("data", {})
            moment_data = data.get("moment", {})
            
            # 基础信息
            topic = moment_data.get("topic", {})
            result["title"] = topic.get("title", "TapTap 动态分享")
            result["seo_keywords"] = moment_data.get("seo", {}).get("keywords", "")
            
            # 底部图片
            footer_images = topic.get("footer_images", [])
            result["footer_images"] = footer_images
            for img_item in footer_images:
                original_url = img_item.get("original_url")
                if original_url and original_url not in result["images"]:
                    result["images"].append(original_url)
            
            # 时间
            result["created_time"] = moment_data.get("created_time", "")
            result["publish_time"] = moment_data.get("publish_time", "")
            
            # 作者信息
            author_data = moment_data.get("author", {})
            user_data = author_data.get("user", {})
            result["author"]["name"] = user_data.get("name", "")
            result["author"]["avatar"] = user_data.get("avatar", "")
            
            app_data = author_data.get("app", {})
            result["author"]["app_title"] = app_data.get("title", "")
            result["author"]["app_icon"] = app_data.get("icon", {}).get("original_url", "")
            
            # 游戏信息
            moment_app = moment_data.get("app", {})
            if moment_app:
                result["app"] = {
                    "title": moment_app.get("title", ""),
                    "icon": moment_app.get("icon", {}).get("original_url", ""),
                    "rating": moment_app.get("stat", {}).get("rating", {}).get("score", ""),
                    "latest_score": moment_app.get("stat", {}).get("rating", {}).get("latest_score", ""),
                    "tags": moment_app.get("tags", [])
                }
            
            # 统计信息
            stats_data = moment_data.get("stat", {})
            result["stats"]["likes"] = stats_data.get("ups", 0)
            result["stats"]["comments"] = stats_data.get("comments", 0)
            result["stats"]["shares"] = stats_data.get("shares", 0) or 0
            result["stats"]["views"] = stats_data.get("pv_total", 0)
            result["stats"]["plays"] = stats_data.get("play_total", 0)
            
            # 视频检测
            pin_video = topic.get("pin_video", {})
            video_id = pin_video.get("video_id")
            if video_id:
                logger.debug(f"[TapTap] 从API获取到视频ID: {video_id}")
                result["video_id"] = video_id
                
                thumbnail = pin_video.get("thumbnail", {})
                if thumbnail:
                    result["video_cover"] = thumbnail.get("original_url", "")
                
                # Try fetch video url directly via API
                play_info_url = f"https://www.taptap.cn/video/v1/play-info"
                play_info_params = {"video_id": video_id}
                
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        play_response = await client.get(play_info_url, params=play_info_params, headers=self.headers)
                        play_response.raise_for_status()
                        play_data = play_response.json()
                        
                        if play_data.get("data") and play_data["data"].get("url"):
                            real_url = play_data["data"]["url"]
                            result["videos"].append(real_url)
                            logger.success(f"[TapTap] 从play-info接口获取到视频链接: {real_url[:50]}...")
                except Exception as e:
                    logger.warning(f"[TapTap] 获取视频play-info失败: {e}")
            
            # 内容解析 (Text & Images)
            first_post = data.get("first_post", {})
            contents = first_post.get("contents", {})
            json_contents = contents.get("json", [])
            
            text_parts = []
            
            for content_item in json_contents:
                item_type = content_item.get("type")
                result["content_items"].append({
                    "type": item_type,
                    "data": content_item
                })
                
                if item_type == "paragraph":
                    paragraph_text = []
                    children = content_item.get("children", [])
                    for child in children:
                        if isinstance(child, dict):
                            child_type = child.get("type")
                            if child_type == "tap_emoji":
                                img_info = child.get("info", {}).get("img", {})
                                original_url = img_info.get("original_url")
                                if original_url:
                                    paragraph_text.append(f'<img src="{original_url}" alt="表情" style="width: 20px; height: 20px; vertical-align: middle; margin: 0 2px; object-fit: contain;">')
                            elif child_type == "hashtag":
                                tag_text = child.get("text", "")
                                if tag_text:
                                    web_url = child.get("info", {}).get("web_url", "")
                                    if web_url:
                                        web_url = web_url.strip()
                                        paragraph_text.append(f'<a href="{web_url}" style="color: #3498db; text-decoration: none; background-color: #f0f8ff; padding: 2px 6px; border-radius: 4px; font-weight: 500; margin: 0 2px;">{tag_text}</a>')
                                    else:
                                        paragraph_text.append(f'<span style="color: #3498db; background-color: #f0f8ff; padding: 2px 6px; border-radius: 4px; font-weight: 500; margin: 0 2px;">{tag_text}</span>')
                            elif "text" in child:
                                paragraph_text.append(child["text"])
                        elif isinstance(child, str):
                            paragraph_text.append(child)
                    if paragraph_text:
                        text_parts.append("".join(paragraph_text))
                        text_parts.append("\n")

                elif item_type == "image":
                    image_info = content_item.get("info", {}).get("image", {})
                    original_url = image_info.get("original_url")
                    if original_url:
                        result["images"].append(original_url)
            
            if text_parts:
                result["text"] = ("".join(text_parts)).replace("<br>", "\n").replace("<br />", "\n")
            
            logger.debug(f"API解析结果: videos={len(result['videos'])}, images={len(result['images'])}, content_items={len(result['content_items'])}, text={result.get('text', '')[:30]}...")
        else:
            # 如果API失败，抛出异常，不再使用浏览器兜底
            raise ParseException(f"TapTap API 请求失败: {post_id}")

        # ==========================================================
        # 2. 获取评论数据
        # ==========================================================
        comments = await self._fetch_comments(post_id)
        if comments:
            logger.debug(f"获取到 {len(comments)} 条评论")
            processed_comments = []
            for comment in comments[:10]:  # 只保留前10条评论
                created_time = comment.get("created_time") or comment.get("updated_time")
                formatted_time = ""
                if created_time:
                    try:
                        dt = datetime.fromtimestamp(created_time)
                        formatted_time = dt.strftime('%Y-%m-%d %H:%M')
                    except (ValueError, TypeError):
                        formatted_time = ""
                
                author = comment.get("author", {})
                badges = author.get("badges", [])
                
                processed_badges = []
                for badge in badges:
                    if badge.get("title"):
                        if badge.get("icon", {}).get("small"):
                            badge_icon = badge["icon"]["small"]
                            processed_badges.append(f'<img src="{badge_icon}" alt="{badge["title"]}" title="{badge["title"]}" style="width: 16px; height: 16px; vertical-align: middle; margin: 0 2px; object-fit: contain;">')
                        processed_badges.append(f'<span class="badge-text" style="color: #3498db; font-size: 12px; margin: 0 2px;">{badge["title"]}</span>')
                
                processed_comment = {
                    "id": comment.get("id", ""),
                    "author": {
                        "id": author.get("id", ""),
                        "name": author.get("name", ""),
                        "avatar": author.get("avatar", ""),
                        "badges": badges,
                        "processed_badges": "".join(processed_badges)
                    },
                    "content": "",
                    "created_time": created_time,
                    "formatted_time": formatted_time,
                    "ups": comment.get("ups", 0),
                    "comments": comment.get("comments", 0),
                    "child_posts": []
                }
                
                if comment.get("contents", {}).get("json"):
                    content_json = comment["contents"]["json"]
                    for item in content_json:
                        item_type = item.get("type")
                        if item_type == "paragraph":
                            for child in item.get("children", []):
                                if child.get("text"):
                                    processed_comment["content"] += child["text"]
                                if child.get("type", '') == "tap_emoji":
                                    image_info = child.get("info", {}).get("image", {})
                                    original_url = image_info.get("original_url")
                                    if original_url:
                                        tap_emoji_text = child.get("children", [])[0]['text']
                                        processed_comment["content"] += f'<img src="{original_url}" alt="表情" class="comment-badge" title="{tap_emoji_text}" style="width: 20px; height: 20px; vertical-align: middle; margin: 0 2px; object-fit: contain;">'
                        elif item_type == "image":
                            image_info = item.get("info", {}).get("image", {})
                            original_url = image_info.get("original_url")
                            if original_url:
                                processed_comment["content"] += f'<div class="comment-image" style="margin: 10px 0;"><img src="{original_url}" alt="评论图片" style="max-width: 100%; height: auto; border-radius: 8px;"></div>'
                
                # 处理回复
                if 'child_posts' in comment:
                    for reply in comment['child_posts'][:5]:
                        reply_created_time = reply.get("created_time") or reply.get("updated_time")
                        reply_formatted_time = ""
                        if reply_created_time:
                            try:
                                dt = datetime.fromtimestamp(reply_created_time)
                                reply_formatted_time = dt.strftime('%Y-%m-%d %H:%M')
                            except (ValueError, TypeError):
                                reply_formatted_time = ""
                        
                        reply_author = reply.get("author", {})
                        reply_badges = reply_author.get("badges", [])
                        
                        processed_reply_badges = []
                        for badge in reply_badges:
                            if badge.get("title"):
                                if badge.get("icon", {}).get("small"):
                                    badge_icon = badge["icon"]["small"]
                                    processed_reply_badges.append(f'<img src="{badge_icon}" alt="{badge["title"]}" title="{badge["title"]}" style="width: 16px; height: 16px; vertical-align: middle; margin: 0 2px; object-fit: contain;">')
                                processed_reply_badges.append(f'<span class="badge-text" style="color: #3498db; font-size: 12px; margin: 0 2px;">{badge["title"]}</span>')
                        
                        processed_reply = {
                            "id": reply.get("id", ""),
                            "author": {
                                "id": reply_author.get("id", ""),
                                "name": reply_author.get("name", ""),
                                "avatar": reply_author.get("avatar", ""),
                                "badges": reply_badges,
                                "processed_badges": "".join(processed_reply_badges)
                            },
                            "content": "",
                            "created_time": reply_created_time,
                            "formatted_time": reply_formatted_time,
                            "ups": reply.get("ups", 0)
                        }
                        
                        if reply.get("contents", {}).get("json"):
                            reply_json = reply["contents"]["json"]
                            for item in reply_json:
                                item_type = item.get("type")
                                if item_type == "paragraph":
                                    for child in item.get("children", []):
                                        if child.get("text"):
                                            processed_reply["content"] += child["text"]
                                        if child.get("type", '') == "tap_emoji":
                                            image_info = child.get("info", {}).get("image", {})
                                            original_url = image_info.get("original_url")
                                            if original_url:
                                                tap_emoji_text = child.get("children", [])[0]['text']
                                                processed_reply["content"] += f'<img src="{original_url}" alt="表情" class="comment-badge" title="{tap_emoji_text}" style="width: 20px; height: 20px; vertical-align: middle; margin: 0 2px; object-fit: contain;">'
                                elif item_type == "image":
                                    image_info = item.get("info", {}).get("image", {})
                                    original_url = image_info.get("original_url")
                                    if original_url:
                                        processed_reply["content"] += f'<div class="comment-image" style="margin: 10px 0;"><img src="{original_url}" alt="回复图片" style="max-width: 100%; height: auto; border-radius: 8px;"></div>'
                        
                        processed_comment["child_posts"].append(processed_reply)
                
                processed_comments.append(processed_comment)
            
            result['comments'] = processed_comments

        return result
    
    async def _parse_user_latest_post(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户最新动态"""
        url = f"{self.base_url}/user/{user_id}"
        # 这里使用改造后的 _fetch_nuxt_data (纯HTTP)
        data = await self._fetch_nuxt_data(url)
        
        candidates = []
        moment_signature = ['id_str', 'author', 'topic', 'created_time']
        
        for item in data:
            if isinstance(item, dict) and all(key in item for key in moment_signature):
                moment_id = self._resolve_nuxt_value(data, item.get('id_str'))
                if not (moment_id and isinstance(moment_id, str) and moment_id.isdigit() and len(moment_id) > 10):
                    continue
                
                topic_index = item.get('topic')
                if not isinstance(topic_index, int) or topic_index >= len(data):
                    continue
                topic_obj = data[topic_index]
                if not isinstance(topic_obj, dict):
                    continue
                
                candidates.append({
                    'id': moment_id,
                    'title': self._resolve_nuxt_value(data, topic_obj.get('title')),
                    'summary': self._resolve_nuxt_value(data, topic_obj.get('summary'))
                })
        
        if not candidates:
            return None
        return max(candidates, key=lambda x: int(x['id']))
    
    async def _fetch_review_comments(self, review_id: str) -> List[Dict[str, Any]]:
        """获取评论的评论列表"""
        api_url = f"https://www.taptap.cn/webapiv2/review-comment/v1/by-review"
        params = {
            "review_id": review_id,
            "show_top": "true",
            "regulate_all": "false",
            "order": "asc",
            "X-UA": "V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC&DS=Android&UID=f69478c8-27a3-4581-877b-45ade0e61b0b&OS=Windows&OSV=10&DT=PC"
        }
        
        comments = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(api_url, params=params, headers=self.headers)
                response.raise_for_status()
                api_data = response.json()
                
                if api_data and api_data.get("success"):
                    data = api_data.get("data", {})
                    comment_list = data.get("list", [])
                    
                    for comment in comment_list:
                        # 格式化时间
                        created_time = comment.get("created_time")
                        formatted_time = ""
                        if created_time:
                            try:
                                dt = datetime.fromtimestamp(created_time)
                                formatted_time = dt.strftime('%Y-%m-%d %H:%M')
                            except (ValueError, TypeError):
                                formatted_time = ""
                        
                        # 处理作者徽章
                        author = comment.get("author", {})
                        badges = author.get("badges", [])
                        processed_badges = []
                        for badge in badges:
                            if badge.get("title"):
                                if badge.get("icon", {}).get("small"):
                                    badge_icon = badge["icon"]["small"]
                                    processed_badges.append(f'<img src="{badge_icon}" alt="{badge["title"]}" title="{badge["title"]}" style="width: 16px; height: 16px; vertical-align: middle; margin: 0 2px; object-fit: contain;">')
                                processed_badges.append(f'<span class="badge-text" style="color: #3498db; font-size: 12px; margin: 0 2px;">{badge["title"]}</span>')
                        
                        processed_comment = {
                            "id": comment.get("id", ""),
                            "author": {
                                "id": author.get("id", ""),
                                "name": author.get("name", ""),
                                "avatar": author.get("avatar", ""),
                                "badges": badges,
                                "processed_badges": "".join(processed_badges)
                            },
                            "content": comment.get("contents", {}).get("text", ""),
                            "created_time": created_time,
                            "formatted_time": formatted_time,
                            "ups": comment.get("ups", 0),
                            "comments": 0,
                            "child_posts": []
                        }
                        
                        comments.append(processed_comment)
                    
                    logger.info(f"[TapTap] 获取评论的评论成功: {len(comments)} 条")
        except Exception as e:
            logger.error(f"[TapTap] 获取评论的评论失败: {e}")
        
        return comments
    
    async def _parse_review_detail(self, review_id: str) -> Dict[str, Any]:
        """解析评论详情"""
        url = f"{self.base_url}/review/{review_id}"
        
        # 初始化结果结构
        result = {
            "id": review_id,
            "url": url,
            "title": "TapTap 评论详情",
            "summary": "",
            "content_items": [],
            "images": [],
            "videos": [],
            "video_id": None,
            "video_duration": None,
            "author": {
                "name": "",
                "avatar": "",
                "app_title": "",
                "app_icon": "",
                "honor_title": "",
                "honor_obj_id": "",
                "honor_obj_type": ""
            },
            "created_time": "",
            "publish_time": "",
            "stats": {
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "views": 0,
                "plays": 0
            },
            "video_cover": "",
            "comments": [],
            "seo_keywords": "",
            "footer_images": [],
            "app": {},
            "extra": {}
        }
        
        # 从API获取评论详情
        api_url = f"https://www.taptap.cn/webapiv2/review/v2/detail"
        params = {
            "id": review_id,
            "X-UA": "V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC&DS=Android&UID=f69478c8-27a3-4581-877b-45ade0e61b0b&OS=Windows&OSV=10&DT=PC"
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(api_url, params=params, headers=self.headers)
                response.raise_for_status()
                api_data = response.json()
                
                if api_data and api_data.get("success"):
                    data = api_data.get("data", {})
                    moment_data = data.get("moment", {})
                    review_data = moment_data.get("review", {})
                    app_data = moment_data.get("app", {})
                    author_data = moment_data.get("author", {})
                    user_data = author_data.get("user", {})
                    
                    # 作者信息
                    result["author"]["name"] = user_data.get("name", "")
                    result["author"]["avatar"] = user_data.get("avatar", "")
                    
                    # 评论内容
                    result["summary"] = review_data.get("contents", {}).get("text", "").replace("<br>", "\n").replace("<br />", "\n")
                    
                    # 评论图片
                    for img_item in review_data.get("images", []):
                        original_url = img_item.get("original_url")
                        if original_url:
                            result["images"].append(original_url)
                    
                    # 发布时间
                    result["created_time"] = moment_data.get("created_time", "")
                    result["publish_time"] = moment_data.get("publish_time", "")
                    
                    # 统计信息
                    stat_data = moment_data.get("stat", {})
                    result["stats"]["likes"] = stat_data.get("ups", 0)
                    result["stats"]["views"] = stat_data.get("pv_total", 0)
                    result["stats"]["comments"] = stat_data.get("comments", 0) or 0
                    
                    # 游戏信息
                    result["app"] = {
                        "title": app_data.get("title", ""),
                        "icon": app_data.get("icon", {}).get("original_url", ""),
                        "rating": app_data.get("stat", {}).get("rating", {}).get("score", ""),
                        "tags": app_data.get("tags", [])
                    }
                    
                    # 评论额外信息
                    result["extra"]["extra"] = {
                        "review": review_data,
                        "author": {
                            "device": moment_data.get("device", ""),
                            "released_time": moment_data.get("release_time", "")
                        },
                        "ratings": review_data.get("ratings", []),
                        "stage": review_data.get("stage", 0),
                        "stage_label": review_data.get("stage_label", "")
                    }
                    
                    # 获取评论的评论
                    result["comments"] = await self._fetch_review_comments(review_id)
                    
                    logger.info(f"[TapTap] 评论详情解析成功: {result['author']['name']} - {result['app']['title']}")
                else:
                    logger.error(f"[TapTap] 评论详情API获取失败")
        except Exception as e:
            logger.error(f"[TapTap] 解析评论详情失败: {e}")
            raise ParseException(f"获取评论详情失败: {url}")
        
        return result
    
    @handle(keyword="taptap.cn/user", pattern=r"taptap\.cn/user/(\d+)")
    async def handle_user(self, matched):
        """处理用户链接，返回最新动态"""
        user_id = matched.group(1)
        latest_post = await self._parse_user_latest_post(user_id)
        
        if not latest_post:
            raise ParseException(f"用户 {user_id} 暂无动态")
        
        detail = await self._parse_post_detail(latest_post['id'])
        return self._build_result(detail)
    
    @handle(keyword="taptap.cn/moment", pattern=r"taptap\.cn/moment/(\d+)")
    async def handle_moment(self, matched):
        """处理动态链接"""
        post_id = matched.group(1)
        detail = await self._parse_post_detail(post_id)
        return self._build_result(detail)
    
    @handle(keyword="taptap.cn/topic", pattern=r"taptap\.cn/topic/(\d+)")
    async def handle_topic(self, matched):
        """处理话题链接"""
        topic_id = matched.group(1)
        # 话题链接暂时返回动态列表，这里简化处理
        url = f"{self.base_url}/topic/{topic_id}"
        data = await self._fetch_nuxt_data(url)
        
        # 简单提取话题名称
        topic_name = "TapTap 话题"
        for item in data:
            if isinstance(item, dict) and 'title' in item:
                title = self._resolve_nuxt_value(data, item['title'])
                if title and isinstance(title, str):
                    topic_name = title
                    break
        
        return self.result(
            title=topic_name,
            text=f"查看话题详情: {url}",
            url=url
        )
    
    @handle(keyword="taptap.cn/review", pattern=r"taptap\.cn/review/(\d+)")
    async def handle_review(self, matched):
        """处理评论详情链接"""
        review_id = matched.group(1)
        detail = await self._parse_review_detail(review_id)
        return self._build_result(detail)
    
    def _build_result(self, detail: Dict[str, Any]):
        """构建解析结果"""
        contents = []
        media_contents = []
        
        # 添加图片
        for img_url in detail['images']:
            contents.append(self.create_image_contents([img_url])[0])
        
        # 添加视频
        for video_url in detail['videos']:
            # 简单处理，不获取封面和时长
            video_content = self.create_video_content(video_url)
            contents.append(video_content)
            # 将视频添加到media_contents中，用于延迟发送
            media_contents.append((VideoContent, video_content))
        
        # 构建作者对象
        author = None
        if detail['author']['name']:
            author = self.create_author(
                name=detail['author']['name'],
                avatar_url=detail['author']['avatar']
            )
        
        # 处理发布时间，转换为时间戳
        timestamp = None
        publish_time = detail['publish_time']
        if publish_time:
            # 如果已经是整数，直接使用
            if isinstance(publish_time, int):
                timestamp = publish_time
            else:
                # 尝试解析不同格式的时间字符串
                try:
                    # 示例：2023-12-25T14:30:00+08:00
                    dt = datetime.fromisoformat(str(publish_time).replace('Z', '+00:00'))
                    timestamp = int(dt.timestamp())
                except (ValueError, TypeError):
                    # 如果解析失败，使用None
                    pass
        
        # 格式化时间函数
        def format_time(timestamp):
            if timestamp:
                try:
                    if isinstance(timestamp, int):
                        dt = datetime.fromtimestamp(timestamp)
                        return dt.strftime('%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError):
                    pass
            return ''
        
        # 格式化时间
        formatted_publish_time = format_time(detail.get('publish_time'))
        formatted_created_time = format_time(detail.get('created_time'))
        
        # 评论时间已经在_fetch_comments中格式化好了，这里直接使用
        formatted_comments = detail.get('comments', [])
        
        # 构建解析结果，先准备extra数据
        extra_data = {
            "stats": detail["stats"],
            "images": detail["images"],  # 将图片列表放入extra，用于模板渲染
            "content_items": detail.get("content_items", []),
            "author": detail.get("author", {}),
            "created_time": detail.get("created_time", ""),
            "publish_time": detail.get("publish_time", ""),
            "formatted_created_time": formatted_created_time,
            "formatted_publish_time": formatted_publish_time,
            "video_cover": detail.get("video_cover", ""),
            "app": detail.get("app", {}),  # 添加游戏信息
            "seo_keywords": detail.get("seo_keywords", ""),  # 添加SEO关键词
            "footer_images": detail.get("footer_images", []),  # 添加footer_images
            "comments": formatted_comments  # 添加格式化后的评论数据
        }
        
        # 合并原始detail中的extra字段内容，用于标识游戏评论
        if detail.get("extra"):
            extra_data.update(detail["extra"])
        
        result = self.result(
            title=detail["title"],
            text=detail.get("text", detail["summary"]),
            url=detail["url"],
            author=author,
            timestamp=timestamp,
            contents=contents,
            extra=extra_data
        )
        
        # 设置media_contents，用于延迟发送
        result.media_contents = media_contents
        logger.debug(f"构建解析结果完成: title={detail['title']}, images={len(detail['images'])}, videos={len(detail['videos'])}, media_contents={len(media_contents)}")
        
        return result