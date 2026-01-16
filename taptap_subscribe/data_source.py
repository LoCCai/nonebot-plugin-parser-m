import json
import re  # <--- 新增这个
import asyncio
from typing import Optional, Dict, Any, List, Set
from nonebot import logger

# 引入现有的浏览器池
from src.plugins.multimodal_gemini.utils import browser_pool

class TapTapSpider:
    def __init__(self):
        self.base_url = "https://www.taptap.cn"
        self.ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    def _resolve_nuxt_value(self, root_data: list, value: Any) -> Any:
        """Nuxt数据解压"""
        if isinstance(value, int):
            # 索引越界保护：如果value是大整数(如video_id)，直接返回value
            if 0 <= value < len(root_data):
                return root_data[value]
            return value
        return value

    async def _get_stealth_page(self, browser):
        """创建带有防检测功能的页面"""
        context = await browser.new_context(
            user_agent=self.ua,
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            locale="zh-CN",
            timezone_id="Asia/Shanghai"
        )
        
        # 注入防检测脚本
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        await context.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});")
        await context.add_init_script("""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Google Inc. (NVIDIA)';
                if (parameter === 37446) return 'NVIDIA GeForce GTX 1050 Ti Direct3D11 vs_5_0 ps_5_0';
                return getParameter(parameter);
            };
        """)

        page = await context.new_page()
        page.set_default_timeout(40000)
        return context, page

    async def _extract_nuxt_from_page(self, page, url):
        """从页面提取数据的具体逻辑"""
        # 注意：这里不再负责 goto，由外部控制 goto 以便设置监听器
        try:
            # WAF 处理
            try:
                waf_element = await page.wait_for_selector('#renderData', timeout=3000, state='attached')
                if waf_element:
                    logger.info("[TapTap] 检测到 WAF 验证页，等待跳转...")
            except Exception:
                pass

            await page.wait_for_selector('#__NUXT_DATA__', timeout=25000, state='attached')
            json_str = await page.evaluate('document.getElementById("__NUXT_DATA__").textContent')
            
            if not json_str:
                return None
            return json.loads(json_str)
        except Exception as e:
            logger.error(f"[TapTap] 页面加载 Nuxt 数据异常: {e}")
            return None

    async def _fetch_nuxt_data(self, url: str) -> Optional[list]:
        """简单的页面数据获取（用于列表页）"""
        async with browser_pool.get_browser() as browser:
            context = None
            try:
                context, page = await self._get_stealth_page(browser)
                logger.info(f"[TapTap] 正在访问列表页: {url}")
                await page.goto(url, wait_until="domcontentloaded")
                return await self._extract_nuxt_from_page(page, url)
            finally:
                if context:
                    await context.close()

    async def fetch_user_profile(self, user_id: str) -> Optional[Dict]:
        """获取用户基本信息"""
        user_url = f"{self.base_url}/user/{user_id}"
        data = await self._fetch_nuxt_data(user_url)
        if not data:
            return None

        target_int_id = int(user_id)
        for item in data:
            if isinstance(item, dict) and 'id' in item and 'name' in item:
                curr_id = self._resolve_nuxt_value(data, item.get('id'))
                if curr_id == target_int_id:
                    return {
                        "user_id": str(curr_id),
                        "nickname": self._resolve_nuxt_value(data, item.get('name')),
                        "avatar": self._resolve_nuxt_value(data, item.get('avatar'))
                    }
        return None

    async def fetch_user_latest_post(self, user_id: str) -> Optional[Dict]:
        """获取用户最新动态"""
        user_url = f"{self.base_url}/user/{user_id}"
        data = await self._fetch_nuxt_data(user_url)
        if not data:
            return None
        return self._parse_user_data_for_latest(data)

    def _parse_user_data_for_latest(self, data: list) -> Optional[Dict]:
        """解析数据列表"""
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

    async def fetch_post_detail(self, post_id: str, simple_info: Optional[Dict] = None) -> Dict:
        """
        获取详情页数据
        【核心升级】：使用 Network Sniffing 获取真实视频链接 + 智能去重
        """
        url = f"{self.base_url}/moment/{post_id}"
        
        result = {
            "id": post_id,
            "url": url,
            "title": simple_info.get("title") if simple_info else "",
            "summary": simple_info.get("summary") if simple_info else "",
            "images": [],
            "videos": []
        }

        # 使用 set 自动去重完全相同的 URL
        captured_videos: Set[str] = set()

        async with browser_pool.get_browser() as browser:
            context = None
            try:
                context, page = await self._get_stealth_page(browser)
                
                # --- 定义监听器 ---
                async def handle_response(response):
                    try:
                        resp_url = response.url
                        
                        # 1. 捕获 .m3u8 (含签名)
                        if '.m3u8' in resp_url and 'sign=' in resp_url:
                            # 简单的过滤：排除掉非 TapTap 域名的（比如广告）
                            if 'taptap.cn' in resp_url:
                                logger.debug(f"[TapTap] 嗅探到 M3U8: {resp_url[:50]}...")
                                captured_videos.add(resp_url)
                        
                        # 2. 捕获 play-info 接口
                        if 'video/v1/play-info' in resp_url and response.status == 200:
                            try:
                                json_data = await response.json()
                                if json_data.get('data') and json_data['data'].get('url'):
                                    real_url = json_data['data']['url']
                                    captured_videos.add(real_url)
                            except:
                                pass
                    except Exception:
                        pass

                page.on("response", handle_response)

                # --- 访问页面 ---
                logger.info(f"[TapTap] 正在访问详情页(开启嗅探): {url}")
                await page.goto(url, wait_until="domcontentloaded")
                
                # --- 获取基础信息 ---
                data = await self._extract_nuxt_from_page(page, url)
                
                # 额外等待，确保视频请求发出
                try:
                    await page.evaluate("window.scrollTo(0, 200)")
                    await asyncio.sleep(2) 
                except:
                    pass

                if not data:
                    return result

                # 补全标题摘要
                if not result['title'] or not result['summary']:
                    for item in data:
                        if isinstance(item, dict) and 'title' in item and 'summary' in item:
                            t = self._resolve_nuxt_value(data, item['title'])
                            s = self._resolve_nuxt_value(data, item['summary'])
                            if t and isinstance(t, str) and not result['title']:
                                result['title'] = t
                            if s and isinstance(s, str) and not result['summary']:
                                result['summary'] = s
                
                if not result['title']:
                    result['title'] = "TapTap 动态分享"

                # 图片处理
                images = []
                img_blacklist = ['appicon', 'avatars', 'logo', 'badge', 'emojis','market']
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    if 'original_url' in item:
                        img_url = self._resolve_nuxt_value(data, item['original_url'])
                        if img_url and isinstance(img_url, str) and img_url.startswith('http'):
                            lower_url = img_url.lower()
                            if not any(k in lower_url for k in img_blacklist):
                                if img_url not in images:
                                    images.append(img_url)
                    
                    # 尝试从 Nuxt 数据中找 MP4 直链
                    if 'video_url' in item or 'url' in item:
                        u = self._resolve_nuxt_value(data, item.get('video_url') or item.get('url'))
                        if isinstance(u, str) and ('.mp4' in u) and u.startswith('http'):
                            captured_videos.add(u)

                result["images"] = images
                
                # === [关键修改] 视频去重逻辑 ===
                unique_videos = []
                seen_ids = set()

                # 将捕获的视频链接转换为列表，并优先处理主M3U8
                video_list = list(captured_videos)

                # 首先，提取所有视频ID并分类
                video_dict = {}  # video_id -> [urls]
                for v_url in video_list:
                    # 尝试提取 TapTap 视频 ID
                    match = re.search(r'/hls/([a-zA-Z0-9\-_]+)', v_url)
                    
                    if match:
                        vid_id = match.group(1)
                        if vid_id not in video_dict:
                            video_dict[vid_id] = []
                        video_dict[vid_id].append(v_url)
                    else:
                        # 如果没有匹配到ID (可能是 MP4 直链或其他 CDN 格式)，则单独处理
                        if v_url not in unique_videos:
                            unique_videos.append(v_url)

                # 对于每个视频ID，优先选择主M3U8（没有子目录的）
                for vid_id, urls in video_dict.items():
                    if len(urls) == 1:
                        # 只有一个URL，直接使用
                        unique_videos.append(urls[0])
                    else:
                        # 多个URL，优先选择主M3U8
                        main_m3u8 = None
                        child_m3u8s = []
                        
                        for url in urls:
                            # 检查是否是主M3U8（没有子目录）
                            # 主M3U8格式：/hls/ID.m3u8
                            # 子M3U8格式：/hls/ID/分辨率.m3u8
                            if re.search(r'/hls/' + re.escape(vid_id) + r'\.m3u8$', url):
                                main_m3u8 = url
                            else:
                                child_m3u8s.append(url)
                        
                        # 优先使用主M3U8（包含所有清晰度信息）
                        if child_m3u8s:
                            # 按分辨率排序（假设文件名是分辨率）
                            child_m3u8s.sort(key=lambda x: int(re.search(r'/(\d+)\.m3u8$', x).group(1)) if re.search(r'/(\d+)\.m3u8$', x) else 0, reverse=True)
                            highest_res = child_m3u8s[0]
                            unique_videos.append(highest_res)
                            logger.debug(f"[TapTap] 视频 {vid_id} 选择最高分辨率: {highest_res}")
                        elif main_m3u8:
                            # 如果没有分辨率的子M3U8，选择主M3U8
                            unique_videos.append(main_m3u8)
                            logger.debug(f"[TapTap] 视频 {vid_id} 选择主M3U8: {main_m3u8}")

                if unique_videos:
                    logger.success(f"[TapTap] 捕获并去重后得到 {len(unique_videos)} 个视频")
                    result["videos"] = unique_videos
                else:
                    logger.warning("[TapTap] 未检测到视频链接")

            except Exception as e:
                logger.error(f"[TapTap] 详情页抓取流程失败: {e}")
            finally:
                if context:
                    await context.close()
        logger.debug(f"[TapTap] 解析数据：{result}")
        return result

taptap_spider = TapTapSpider()