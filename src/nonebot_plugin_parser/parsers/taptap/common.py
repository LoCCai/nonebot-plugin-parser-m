import asyncio
import json
import re
import httpx
from datetime import datetime
from typing import Optional, Dict, Any, List, Set
from contextlib import asynccontextmanager

from nonebot import logger
from ..base import BaseParser, handle
from ..data import Platform, Author, MediaContent, ImageContent, VideoContent
from ...exception import ParseException
from ...constants import PlatformEnum

# ==========================================================
# 兼容性导入逻辑
# 优先尝试导入本地高性能浏览器池，如果不存在则使用插件自带池
# ==========================================================
HAS_LOCAL_POOL = False
try:
    from src.utils.browser_pool_fx import (
        browser_pool as local_browser_pool,
        create_anti_captcha_config,
        get_proxy_config
    )
    from src.utils.browser_utils import simulate_human_behavior
    HAS_LOCAL_POOL = True
    logger.info("[TapTap] 检测到本地浏览器池 (browser_pool_fx)，将启用增强模式")
except ImportError:
    logger.info("[TapTap] 未检测到本地浏览器池，回退至插件标准浏览器池")
    # 回退到插件自带的浏览器池
    from ...browser_pool import browser_pool as standard_browser_pool, safe_browser_context


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
        
        # 代理配置（仅在本地池模式下可能用到）
        self.proxy_server = None 
        self.use_proxy = False 

    @asynccontextmanager
    async def _get_browser_page(self):
        """
        统一获取浏览器页面的上下文管理器
        屏蔽了本地池和标准池的调用差异
        """
        if HAS_LOCAL_POOL:
            # === 使用本地增强池 ===
            proxy_config = None
            if self.use_proxy and self.proxy_server:
                proxy_config = get_proxy_config(
                    proxy_server=self.proxy_server,
                    proxy_username="",
                    proxy_password="",
                    use_proxy=True
                )
            # 配置防验证
            config = create_anti_captcha_config(
                proxy=proxy_config,
                headless=True
            )
            async with local_browser_pool.get_context_and_page(config) as (context, page):
                yield page
        else:
            # === 使用插件标准池 ===
            async with standard_browser_pool.get_browser() as browser:
                # 注入基础防检测特征
                async with safe_browser_context(browser) as (context, page):
                    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                    await context.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});")
                    yield page

    async def _simulate_human(self, page):
        """统一的人类行为模拟"""
        if HAS_LOCAL_POOL:
            # 使用本地工具库
            await simulate_human_behavior(page, ["scroll", "wait_random"])
        else:
            # 简单的模拟滚动
            try:
                await page.evaluate("window.scrollTo(0, 200)")
                await asyncio.sleep(0.5)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight/3)")
                await asyncio.sleep(0.5)
            except Exception:
                pass

    def _resolve_nuxt_value(self, root_data: list, value: Any) -> Any:
        """Nuxt数据解压"""
        if isinstance(value, int):
            if 0 <= value < len(root_data):
                return root_data[value]
            return value
        return value
    
    async def _fetch_nuxt_data(self, url: str) -> list:
        """
        获取页面的 Nuxt 数据 (纯 HTTP 请求版)
        注：为了保持轻量级，基础数据获取尽量不使用浏览器，
        除非在 _parse_post_detail 中因为视频嗅探被迫启动浏览器时才使用 DOM 提取。
        """
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
                                json_match = re.search(r'__NUXT_DATA__\s*=\s*(\[.*?\])', match.group(1), re.DOTALL)
                                if json_match:
                                    parsed_data = json.loads(json_match.group(1))
                                    if isinstance(parsed_data, list):
                                        nuxt_data = parsed_data
                                        break
                                parsed_data = json.loads(match.group(1))
                                if isinstance(parsed_data, list):
                                    nuxt_data = parsed_data
                                    break
                            except json.JSONDecodeError:
                                continue
                
                # 方式2: window.__NUXT__
                if not nuxt_data and "window.__NUXT__" in response_text:
                    match = re.search(r'window\.__NUXT__\s*=\s*(\[.*?\])', response_text, re.DOTALL)
                    if match:
                        try:
                            parsed_data = json.loads(match.group(1))
                            if isinstance(parsed_data, list):
                                nuxt_data = parsed_data
                        except json.JSONDecodeError:
                            pass

                # 方式3: window.__NUXT_DATA__
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
                    # 如果纯HTTP没拿到数据，返回空列表，后续逻辑可能会根据情况决定是否启动浏览器
                    logger.debug(f"[TapTap] HTTP正则提取Nuxt失败: {url}")
                    return []
                
                return nuxt_data

        except Exception as e:
            logger.warning(f"[TapTap] HTTP获取Nuxt数据失败: {url}, error: {e}")
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
                # 增加 JSON 解析的异常捕获，防止 API 返回 HTML 报错（WAF）
                try:
                    return response.json()
                except json.JSONDecodeError:
                    logger.warning(f"[TapTap] API 返回非 JSON 数据 (可能是验证盾): {response.text[:100]}")
                    return None
        except Exception as e:
            logger.error(f"[TapTap] API请求异常: {e}")
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
                try:
                    data = response.json()
                    if data.get("success") and data.get("data"):
                        return data["data"].get("list", [])
                except json.JSONDecodeError:
                    pass
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
        api_success = False
        api_data = await self._fetch_api_data(post_id)
        
        if api_data and api_data.get("success"):
            logger.info(f"[TapTap] 使用API获取数据成功")
            data = api_data.get("data", {})
            moment_data = data.get("moment", {})
            
            # --- 基础信息 ---
            topic = moment_data.get("topic", {})
            result["title"] = topic.get("title", "TapTap 动态分享")
            result["seo_keywords"] = moment_data.get("seo", {}).get("keywords", "")
            
            # --- 图片 ---
            footer_images = topic.get("footer_images", [])
            result["footer_images"] = footer_images
            for img_item in footer_images:
                original_url = img_item.get("original_url")
                if original_url and original_url not in result["images"]:
                    result["images"].append(original_url)
            
            # --- 时间 ---
            result["created_time"] = moment_data.get("created_time", "")
            result["publish_time"] = moment_data.get("publish_time", "")
            
            # --- 作者 ---
            author_data = moment_data.get("author", {})
            user_data = author_data.get("user", {})
            result["author"]["name"] = user_data.get("name", "")
            result["author"]["avatar"] = user_data.get("avatar", "")
            app_data = author_data.get("app", {})
            result["author"]["app_title"] = app_data.get("title", "")
            result["author"]["app_icon"] = app_data.get("icon", {}).get("original_url", "")
            
            # --- 游戏/APP ---
            moment_app = moment_data.get("app", {})
            if moment_app:
                result["app"] = {
                    "title": moment_app.get("title", ""),
                    "icon": moment_app.get("icon", {}).get("original_url", ""),
                    "rating": moment_app.get("stat", {}).get("rating", {}).get("score", ""),
                    "latest_score": moment_app.get("stat", {}).get("rating", {}).get("latest_score", ""),
                    "tags": moment_app.get("tags", [])
                }
            
            # --- 统计 ---
            stats_data = moment_data.get("stat", {})
            result["stats"]["likes"] = stats_data.get("ups", 0)
            result["stats"]["comments"] = stats_data.get("comments", 0)
            result["stats"]["shares"] = stats_data.get("shares", 0) or 0
            result["stats"]["views"] = stats_data.get("pv_total", 0)
            result["stats"]["plays"] = stats_data.get("play_total", 0)
            
            # --- 视频信息 (仅获取ID) ---
            pin_video = topic.get("pin_video", {})
            video_id = pin_video.get("video_id")
            if video_id:
                logger.debug(f"[TapTap] 从API获取到视频ID: {video_id}")
                result["video_id"] = video_id
                thumbnail = pin_video.get("thumbnail", {})
                if thumbnail:
                    result["video_cover"] = thumbnail.get("original_url", "")
                
                # 尝试通过 play-info 获取链接
                play_info_url = f"https://www.taptap.cn/video/v1/play-info"
                play_info_params = {"video_id": video_id}
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        play_response = await client.get(play_info_url, params=play_info_params, headers=self.headers)
                        if play_response.status_code == 200:
                            play_data = play_response.json()
                            if play_data.get("data") and play_data["data"].get("url"):
                                real_url = play_data["data"]["url"]
                                result["videos"].append(real_url)
                                logger.success(f"[TapTap] API获取视频链接成功: {real_url[:30]}...")
                except Exception as e:
                    logger.warning(f"[TapTap] API获取视频链接失败，后续将尝试浏览器嗅探: {e}")

            # --- 文本内容 ---
            first_post = data.get("first_post", {})
            contents = first_post.get("contents", {})
            json_contents = contents.get("json", [])
            text_parts = []
            
            for content_item in json_contents:
                item_type = content_item.get("type")
                result["content_items"].append({"type": item_type, "data": content_item})
                
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
            
            api_success = True
        else:
            logger.warning(f"[TapTap] API获取数据失败，准备使用浏览器解析")
            api_success = False

        # ==========================================================
        # 2. 获取评论 (独立)
        # ==========================================================
        comments = await self._fetch_comments(post_id)
        if comments:
            processed_comments = []
            for comment in comments[:10]:
                created_time = comment.get("created_time") or comment.get("updated_time")
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
                    "content": "",
                    "created_time": created_time,
                    "formatted_time": formatted_time,
                    "ups": comment.get("ups", 0),
                    "comments": comment.get("comments", 0),
                    "child_posts": []
                }
                
                # 提取评论内容
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

        # ==========================================================
        # 3. 浏览器兜底与嗅探
        # 条件：API失败 或者 (有视频ID但没有链接)
        # ==========================================================
        has_video_id = bool(result.get("video_id"))
        has_video_url = len(result["videos"]) > 0
        
        need_browser = (not api_success) or (has_video_id and not has_video_url)

        if need_browser:
            logger.info(f"[TapTap] 启动浏览器处理 (API成功: {api_success}, 缺视频: {has_video_id and not has_video_url})")
            captured_videos: Set[str] = set()
            
            try:
                # 使用统一的浏览器页面获取方法
                async with self._get_browser_page() as page:
                    page.set_default_timeout(40000)
                    
                    # --- 监听器：嗅探视频 ---
                    async def handle_response(response):
                        try:
                            resp_url = response.url
                            
                            # 1. 捕获 .m3u8 (含签名)
                            if '.m3u8' in resp_url and 'sign=' in resp_url and 'taptap.cn' in resp_url:
                                logger.debug(f"[TapTap] 嗅探到 M3U8: {resp_url[:50]}...")
                                captured_videos.add(resp_url)
                            
                            # 2. 捕获 MP4 直链
                            if '.mp4' in resp_url and 'taptap' in resp_url:
                                captured_videos.add(resp_url)

                            # 3. 捕获 play-info 接口
                            if 'video/v1/play-info' in resp_url and response.status == 200:
                                try:
                                    json_data = await response.json()
                                    if json_data.get('data') and json_data['data'].get('url'):
                                        captured_videos.add(json_data['data']['url'])
                                except:
                                    pass
                        except Exception:
                            pass
                    
                    page.on("response", handle_response)
                    
                    # --- 访问页面 ---
                    logger.info(f"[TapTap] 正在访问详情页: {url}")
                    await page.goto(url, wait_until="domcontentloaded")
                    await self._simulate_human(page)
                    
                    # --- 如果API完全失败，使用浏览器提取 Nuxt 数据 ---
                    if not api_success:
                        logger.info("[TapTap] API失败，从浏览器 DOM 提取数据")
                        data = []
                        try:
                            # 尝试获取 __NUXT_DATA__
                            try:
                                await page.wait_for_selector('#__NUXT_DATA__', timeout=10000, state='attached')
                                json_str = await page.evaluate('document.getElementById("__NUXT_DATA__").textContent')
                                if json_str: data = json.loads(json_str)
                            except:
                                # 尝试 window.__NUXT__
                                data = await page.evaluate('window.__NUXT__ || window.__NUXT_DATA__ || []')
                        except Exception as e:
                            logger.error(f"[TapTap] DOM提取数据异常: {e}")

                        # 解析浏览器获取的 Nuxt 数据 (复用原有的详细解析逻辑)
                        if data:
                            all_text_parts = []
                            for item in data:
                                if not isinstance(item, dict): continue
                                
                                # 作者
                                if 'user' in item:
                                    user_ref = item['user']
                                    user_obj = self._resolve_nuxt_value(data, user_ref)
                                    if isinstance(user_obj, dict):
                                        result['author']['name'] = self._resolve_nuxt_value(data, user_obj.get('name', '')) or ''
                                        if 'avatar' in user_obj:
                                            avatar = self._resolve_nuxt_value(data, user_obj['avatar'])
                                            if isinstance(avatar, str) and avatar.startswith('http'):
                                                result['author']['avatar'] = avatar
                                            elif isinstance(avatar, dict) and 'original_url' in avatar:
                                                result['author']['avatar'] = self._resolve_nuxt_value(data, avatar['original_url']) or ''
                                
                                # 标题摘要
                                if 'title' in item and 'summary' in item:
                                    title = self._resolve_nuxt_value(data, item['title'])
                                    summary = self._resolve_nuxt_value(data, item['summary'])
                                    if title and isinstance(title, str): result['title'] = title
                                    if summary and isinstance(summary, str): all_text_parts.append(summary)
                                
                                # 统计
                                if 'stat' in item:
                                    stat_ref = item['stat']
                                    stat_obj = self._resolve_nuxt_value(data, stat_ref)
                                    if isinstance(stat_obj, dict):
                                        result['stats']['likes'] = stat_obj.get('supports', 0) or stat_obj.get('likes', 0)
                                        result['stats']['comments'] = stat_obj.get('comments', 0)
                                
                                # 内容
                                if 'contents' in item:
                                    contents = self._resolve_nuxt_value(data, item['contents'])
                                    if isinstance(contents, list):
                                        for content_item in contents:
                                            if isinstance(content_item, dict):
                                                if 'text' in content_item:
                                                    text = self._resolve_nuxt_value(data, content_item['text'])
                                                    if text and isinstance(text, str): all_text_parts.append(text)
                                
                                # 视频补全
                                if 'pin_video' in item:
                                    video_info = self._resolve_nuxt_value(data, item['pin_video'])
                                    if isinstance(video_info, dict):
                                        if 'video_id' in video_info:
                                            result['video_id'] = self._resolve_nuxt_value(data, video_info['video_id'])

                                # 图片补全
                                if 'original_url' in item:
                                    img_url = self._resolve_nuxt_value(data, item['original_url'])
                                    if img_url and isinstance(img_url, str) and img_url.startswith('http'):
                                        img_blacklist = ['appicon', 'avatars', 'logo', 'badge', 'emojis', 'market']
                                        if not any(k in img_url.lower() for k in img_blacklist):
                                            if img_url not in result['images']:
                                                result['images'].append(img_url)

                            # 合并文本
                            unique_text = []
                            seen = set()
                            for t in all_text_parts:
                                if t not in seen:
                                    seen.add(t)
                                    unique_text.append(t)
                            if unique_text:
                                result['summary'] = '\n'.join(unique_text)
                                if not result['text']: result['text'] = result['summary']

                    # 等待视频加载
                    if result.get('video_id'):
                        try:
                            await page.evaluate("window.scrollTo(0, 300)")
                            await asyncio.sleep(3)
                        except: pass
                    
                    # --- 视频链接优选与合并 ---
                    unique_videos = []
                    video_list = list(captured_videos)
                    
                    # 优先处理 TapTap 视频 ID
                    video_dict = {}
                    for v_url in video_list:
                        match = re.search(r'/hls/([a-zA-Z0-9\-_]+)', v_url)
                        if match:
                            vid_id = match.group(1)
                            if vid_id not in video_dict: video_dict[vid_id] = []
                            video_dict[vid_id].append(v_url)
                        else:
                            if v_url not in unique_videos and v_url not in result["videos"]:
                                unique_videos.append(v_url)
                    
                    # 选择最高分辨率
                    for vid_id, urls in video_dict.items():
                        quality_priority = ['2208', '2206', '2204', '2202']
                        def get_quality_priority(url):
                            for i, quality in enumerate(quality_priority):
                                if f'/{quality}.m3u8' in url: return i
                            return len(quality_priority)
                        urls.sort(key=get_quality_priority)
                        unique_videos.append(urls[0])
                    
                    for v in unique_videos:
                        if v not in result["videos"]:
                            result["videos"].append(v)
                    
                    if result["videos"]:
                        logger.success(f"[TapTap] 浏览器嗅探成功，共 {len(result['videos'])} 个视频")

            except Exception as e:
                logger.error(f"[TapTap] 浏览器处理流程失败: {e}")
                # 如果API也失败了，且浏览器也失败了，抛出异常
                if not api_success:
                    raise ParseException(f"解析失败: {e}")

        return result
    
    async def _parse_user_latest_post(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户最新动态"""
        url = f"{self.base_url}/user/{user_id}"
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
                        created_time = comment.get("created_time")
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
            "stats": {"likes": 0, "comments": 0, "shares": 0, "views": 0, "plays": 0},
            "video_cover": "",
            "comments": [],
            "seo_keywords": "",
            "footer_images": [],
            "app": {},
            "extra": {}
        }
        
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
                    
                    result["author"]["name"] = user_data.get("name", "")
                    result["author"]["avatar"] = user_data.get("avatar", "")
                    result["summary"] = review_data.get("contents", {}).get("text", "").replace("<br>", "\n").replace("<br />", "\n")
                    
                    for img_item in review_data.get("images", []):
                        original_url = img_item.get("original_url")
                        if original_url: result["images"].append(original_url)
                    
                    result["created_time"] = moment_data.get("created_time", "")
                    result["publish_time"] = moment_data.get("publish_time", "")
                    
                    stat_data = moment_data.get("stat", {})
                    result["stats"]["likes"] = stat_data.get("ups", 0)
                    result["stats"]["views"] = stat_data.get("pv_total", 0)
                    result["stats"]["comments"] = stat_data.get("comments", 0) or 0
                    
                    result["app"] = {
                        "title": app_data.get("title", ""),
                        "icon": app_data.get("icon", {}).get("original_url", ""),
                        "rating": app_data.get("stat", {}).get("rating", {}).get("score", ""),
                        "tags": app_data.get("tags", [])
                    }
                    
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
        user_id = matched.group(1)
        latest_post = await self._parse_user_latest_post(user_id)
        if not latest_post:
            raise ParseException(f"用户 {user_id} 暂无动态")
        detail = await self._parse_post_detail(latest_post['id'])
        return self._build_result(detail)
    
    @handle(keyword="taptap.cn/moment", pattern=r"taptap\.cn/moment/(\d+)")
    async def handle_moment(self, matched):
        post_id = matched.group(1)
        detail = await self._parse_post_detail(post_id)
        return self._build_result(detail)
    
    @handle(keyword="taptap.cn/topic", pattern=r"taptap\.cn/topic/(\d+)")
    async def handle_topic(self, matched):
        topic_id = matched.group(1)
        url = f"{self.base_url}/topic/{topic_id}"
        data = await self._fetch_nuxt_data(url)
        topic_name = "TapTap 话题"
        for item in data:
            if isinstance(item, dict) and 'title' in item:
                title = self._resolve_nuxt_value(data, item['title'])
                if title and isinstance(title, str):
                    topic_name = title
                    break
        return self.result(title=topic_name, text=f"查看话题详情: {url}", url=url)
    
    @handle(keyword="taptap.cn/review", pattern=r"taptap\.cn/review/(\d+)")
    async def handle_review(self, matched):
        review_id = matched.group(1)
        detail = await self._parse_review_detail(review_id)
        return self._build_result(detail)
    
    def _build_result(self, detail: Dict[str, Any]):
        """构建解析结果"""
        contents = []
        media_contents = []
        
        for img_url in detail['images']:
            contents.append(self.create_image_contents([img_url])[0])
        
        for video_url in detail['videos']:
            video_content = self.create_video_content(video_url)
            contents.append(video_content)
            media_contents.append((VideoContent, video_content))
        
        author = None
        if detail['author']['name']:
            author = self.create_author(
                name=detail['author']['name'],
                avatar_url=detail['author']['avatar']
            )
        
        timestamp = None
        publish_time = detail['publish_time']
        if publish_time:
            if isinstance(publish_time, int):
                timestamp = publish_time
            else:
                try:
                    dt = datetime.fromisoformat(str(publish_time).replace('Z', '+00:00'))
                    timestamp = int(dt.timestamp())
                except (ValueError, TypeError):
                    pass
        
        def format_time(timestamp):
            if timestamp:
                try:
                    if isinstance(timestamp, int):
                        dt = datetime.fromtimestamp(timestamp)
                        return dt.strftime('%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError):
                    pass
            return ''
        
        formatted_publish_time = format_time(detail.get('publish_time'))
        formatted_created_time = format_time(detail.get('created_time'))
        formatted_comments = detail.get('comments', [])
        
        extra_data = {
            "stats": detail["stats"],
            "images": detail["images"],
            "content_items": detail.get("content_items", []),
            "author": detail.get("author", {}),
            "created_time": detail.get("created_time", ""),
            "publish_time": detail.get("publish_time", ""),
            "formatted_created_time": formatted_created_time,
            "formatted_publish_time": formatted_publish_time,
            "video_cover": detail.get("video_cover", ""),
            "app": detail.get("app", {}),
            "seo_keywords": detail.get("seo_keywords", ""),
            "footer_images": detail.get("footer_images", []),
            "comments": formatted_comments
        }
        
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
        result.media_contents = media_contents
        logger.debug(f"构建解析结果完成: title={detail['title']}, images={len(detail['images'])}, videos={len(detail['videos'])}, media_contents={len(media_contents)}")
        return result