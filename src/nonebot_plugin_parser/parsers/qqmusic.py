import re
import asyncio
from typing import ClassVar
from re import Match

from nonebot import logger

from .base import (
    BaseParser,
    PlatformEnum,
    ParseException,
    handle,
)
from .data import Platform, MediaContent, AudioContent, ImageContent
from ..constants import COMMON_HEADER
from httpx import AsyncClient

async def get_async_client():
    return AsyncClient()


class QQMusicParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.QQMUSIC, display_name="QQ音乐")
    
    def __init__(self):
        super().__init__()
        # 音质优先级列表
        self.audio_qualities = [
            "flac",  # 无损音质
            "320",   # 高品质
            "128"    # 标准音质
        ]
    
    async def _get_redirect_url(self, url: str) -> str:
        """获取重定向后的URL"""
        from httpx import AsyncClient
        
        headers = COMMON_HEADER.copy()
        async with AsyncClient(headers=headers, verify=False, follow_redirects=True, timeout=self.timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return str(response.url)
    
    async def parse_qqmusic(self, qqmusic_url: str) -> dict:
        """解析QQ音乐链接"""
        # 处理短链接
        if "c.y.qq.com" in qqmusic_url:
            qqmusic_url = await self._get_redirect_url(qqmusic_url)
        
        # 获取QQ音乐歌曲id
        matched = re.search(r"song/(\d+)", qqmusic_url)
        if not matched:
            # 尝试从其他格式提取id
            matched = re.search(r"id=(\d+)", qqmusic_url)
        
        if not matched:
            raise ParseException(f"无效QQ音乐链接: {qqmusic_url}")
        
        qqmusic_id = matched.group(1)
        logger.info(f"成功提取ID: {qqmusic_id} 来自 {qqmusic_url}")
        
        # 使用API解析
        try:
            from httpx import AsyncClient
            
            headers = COMMON_HEADER.copy()
            headers.update({
                "Content-Type": "application/json",
                "User-Agent": "API-Client/1.0"
            })
            
            async with AsyncClient(headers=headers, verify=False, timeout=self.timeout) as client:
                api_url = "https://api.bugpk.com/api/qq_music"
                params = {
                    "id": qqmusic_id
                }
                resp = await client.get(api_url, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                # 检查接口返回状态
                if data.get("code") != 200:
                    raise ParseException(f"QQ音乐接口返回错误: {data.get('msg')}")
                
                music_data = data["data"]
                logger.info(f"QQ音乐解析成功: {music_data['name']} - {music_data['singer']}")
                
                # 提取音频URL
                audio_url = music_data.get("url")
                if not audio_url or not audio_url.startswith("http"):
                    raise ParseException("无效音乐URL")
                
                # 创建有意义的音频文件名
                audio_name = f"{music_data['name']}-{music_data['singer']}.mp3"
                
                # 构建返回结果
                return {
                    "title": music_data["name"],
                    "author": music_data["singer"],
                    "audio_info": f"音质: {music_data.get('quality', '未知')} | 大小: {music_data.get('size', '未知')}",
                    "cover_url": music_data.get("pic"),
                    "audio_url": audio_url,
                    "lyric": music_data.get("lyric")
                }
        except Exception as e:
            raise ParseException(f"QQ音乐解析失败: {e}")
    
    @handle("y.qq.com", r"https?://[^\s]*?y\.qq\.com.*?(?:id=\d+|song/\d+)")
    @handle("c.y.qq.com", r"https?://[^\s]*?c\.y\.qq\.com.*?(?:id=\d+|song/\d+)")
    async def _parse_qqmusic(self, searched: Match[str]):
        """解析QQ音乐分享链接"""
        share_url = searched.group(0)
        logger.debug(f"触发QQ音乐解析: {share_url}")
        
        # 解析QQ音乐
        result = await self.parse_qqmusic(share_url)
        
        # 创建有意义的音频文件名
        audio_name = f"{result['title']}-{result['author']}.mp3"
        # 创建音频内容
        audio_content = self.create_audio_content(
            result["audio_url"],
            0.0,  # 暂时无法从API获取准确时长
            audio_name=audio_name
        )
        
        # 创建封面图片内容
        contents: list[MediaContent] = [audio_content]
        if result.get("cover_url"):
            from ..download import DOWNLOADER
            cover_content = ImageContent(
                DOWNLOADER.download_img(result["cover_url"], ext_headers=self.headers)
            )
            contents.insert(0, cover_content)
        
        # 构建文本内容
        text = f"{result['audio_info']}"
        if result.get("lyric"):
            text += f"\n歌词:\n{result['lyric']}"
        
        # 构建额外信息
        extra = {
            "info": result["audio_info"],
            "type": "audio",
            "type_tag": "音乐",
            "type_icon": "fa-music",
        }
        
        return self.result(
            title=result["title"],
            author=self.create_author(result["author"]),
            url=share_url,
            text=text,
            contents=contents,
            extra=extra,
        )
