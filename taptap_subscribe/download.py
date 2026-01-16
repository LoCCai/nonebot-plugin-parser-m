import os
import aiohttp
import aiofiles
import asyncio
from pathlib import Path
from urllib.parse import urljoin
from nonebot import logger

# 视频缓存目录
CACHE_DIR = Path("data/taptap_subscribe/cache")
if not CACHE_DIR.exists():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

class TapTapDownloader:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.taptap.cn/",
            "Origin": "https://www.taptap.cn"
        }

    async def download_video(self, m3u8_url: str, file_id: str) -> Path:
        """
        下载 m3u8 视频并合并为 mp4
        """
        final_video_path = CACHE_DIR / f"{file_id}.mp4"
        temp_ts_path = CACHE_DIR / f"{file_id}_temp.ts"

        if final_video_path.exists():
            return final_video_path

        logger.info(f"[TapTap] 开始下载视频流: {file_id}")

        try:
            # 1. 智能解析 m3u8 (自动处理嵌套列表)
            ts_urls = await self._smart_parse_m3u8(m3u8_url)
            
            if not ts_urls:
                raise Exception("m3u8 解析结果为空")

            # 2. 下载并追加写入
            downloaded_bytes = 0
            async with aiohttp.ClientSession() as session:
                async with aiofiles.open(temp_ts_path, "wb") as f:
                    for i, ts_url in enumerate(ts_urls):
                        for retry in range(3):
                            try:
                                async with session.get(ts_url, headers=self.headers, timeout=15) as resp:
                                    if resp.status == 200:
                                        content = await resp.read()
                                        await f.write(content)
                                        downloaded_bytes += len(content)
                                        break
                            except Exception:
                                await asyncio.sleep(1)
            
            # 3. 校验文件大小 (防止空文件送给 FFmpeg)
            if downloaded_bytes < 1024:
                raise Exception(f"下载文件过小 ({downloaded_bytes} bytes)，可能下载失败")

            # 4. 转封装处理
            if await self._has_ffmpeg():
                await self._remux_to_mp4(temp_ts_path, final_video_path)
            else:
                if temp_ts_path.exists():
                    temp_ts_path.rename(final_video_path)

            if final_video_path.exists() and final_video_path.stat().st_size > 1024:
                logger.success(f"[TapTap] 视频下载完成: {final_video_path}")
                return final_video_path
            else:
                return None

        except Exception as e:
            logger.error(f"[TapTap] 视频下载流程出错: {e}")
            if temp_ts_path.exists():
                os.remove(temp_ts_path)
            return None

    async def _fetch_text(self, url: str) -> str:
        """辅助函数：获取文本内容"""
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, timeout=10) as resp:
                if resp.status != 200:
                    raise Exception(f"请求失败: {resp.status}")
                return await resp.text()

    async def _smart_parse_m3u8(self, m3u8_url: str) -> list[str]:
        """
        智能解析 m3u8，支持 Master Playlist (嵌套) 和 Media Playlist
        """
        content = await self._fetch_text(m3u8_url)
        base_url = m3u8_url.rsplit('/', 1)[0] + '/'

        # 检查是否是 Master Playlist (包含子 m3u8 链接)
        if "#EXT-X-STREAM-INF" in content:
            logger.debug("[TapTap] 检测到 Master Playlist，正在提取最高画质链接...")
            lines = content.splitlines()
            sub_playlists = []
            
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    # 处理相对路径
                    if not line.startswith('http'):
                        line = urljoin(base_url, line)
                    sub_playlists.append(line)
            
            if sub_playlists:
                # 通常最后一个是最高画质，或者是第一个。
                # 递归调用自己去解析真正的 Media Playlist
                logger.debug(f"[TapTap] 转向子播放列表: {sub_playlists[-1]}")
                return await self._smart_parse_m3u8(sub_playlists[-1])
            else:
                raise Exception("Master Playlist 解析失败，未找到子链接")

        # 处理 Media Playlist (真正的 TS 列表)
        ts_urls = []
        lines = content.splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            if line.startswith('http'):
                ts_urls.append(line)
            else:
                ts_urls.append(urljoin(base_url, line))
        
        return ts_urls

    async def _has_ffmpeg(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_shell(
                "ffmpeg -version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            return proc.returncode == 0
        except:
            return False

    async def _remux_to_mp4(self, input_path: Path, output_path: Path):
        # 增加 -f mp4 强制格式，增加 probeSize 防止开头数据分析失败
        cmd = f'ffmpeg -y -v error -probesize 50M -analyzeduration 100M -i "{input_path}" -c copy -bsf:a aac_adtstoasc "{output_path}"'
        proc = await asyncio.create_subprocess_shell(cmd)
        await proc.communicate()
        
        if output_path.exists() and input_path.exists():
            os.remove(input_path)

downloader = TapTapDownloader()