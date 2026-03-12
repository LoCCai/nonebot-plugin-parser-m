import re
from typing import ClassVar
from urllib.parse import parse_qsl

from httpx import AsyncClient
from msgspec import convert
from nonebot import logger

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle, pconfig
from ..data import MediaContent, Comment
from . import explore, discovery


REDNOTE_PATTERN = re.compile(r"\[(?P<name>[^]]+[a-zA-Z])\]")


class XiaoHongShuParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.XIAOHONGSHU, display_name="小红书")

    def __init__(self):
        super().__init__()
        explore_headers = {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
            )
        }
        self.headers.update(explore_headers)

        discovery_headers = {
            "origin": "https://www.xiaohongshu.com",
            "x-requested-with": "XMLHttpRequest",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        }
        self.ios_headers.update(discovery_headers)

        if pconfig.xhs_ck:
            self.headers["cookie"] = pconfig.xhs_ck
            self.ios_headers["cookie"] = pconfig.xhs_ck

    @handle("xhslink.com", r"xhslink\.com/[A-Za-z0-9._?%&+=/#@-]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url, self.ios_headers)

    @handle(
        "xiaohongshu.com",
        r"(?P<type>explore|search_result|discovery/item)/(?P<note_id>[0-9a-zA-Z]+)\?(?P<qs>[A-Za-z0-9._%&+=/#@-]+)",
    )
    async def _parse_common(self, searched: re.Match[str]):
        xhs_domain = "https://www.xiaohongshu.com"
        note_id = searched["note_id"]
        qs = searched["qs"]

        full_url = f"{xhs_domain}/explore/{note_id}"

        params_dict = dict(parse_qsl(qs, keep_blank_values=True))
        xsec_token = params_dict.get("xsec_token")
        if not xsec_token:
            raise ParseException("缺少 xsec_token, 无法解析小红书链接")

        full_url += f"?xsec_token={xsec_token}&xsec_source=pc_share"

        try:
            return await self.parse_explore(full_url, note_id, xsec_token)
        except Exception as e:
            logger.warning(f"parse_explore failed, error: {e}, fallback to parse_discovery")
            return await self.parse_discovery(f"{xhs_domain}/discovery/item/{note_id}?{qs}")

    async def parse_explore(self, url: str, note_id: str, xsec_token: str):
        """解析小红书笔记详情页"""
        async with AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            raw = await self._fetch_init_state(client, url)
            com_data = await self._fetch_comments(client, note_id, xsec_token)

        init_state = explore.decoder.decode(raw)
        note_data = init_state.note.noteDetailMap.get(note_id)
        if not note_data:
            raise ParseException(f"can't find note detail for note_id: {note_id}")
        
        note_data.comments_list = convert(com_data, explore.CommentList)

        result = self._build_result(note_data)
        result.url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}"
        return result

    async def parse_discovery(self, url: str):
        """解析小红书 discovery 页面（向后兼容）"""
        async with AsyncClient(
            headers=self.ios_headers,
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text

        raw = self._extract_initial_state_raw(html)
        init_state = discovery.decoder.decode(raw)
        note_data = init_state.noteData.data.noteData
        preload_data = init_state.noteData.normalNotePreloadData

        contents: list[MediaContent] = []
        if video_url := note_data.video_url:
            if preload_data:
                img_urls = preload_data.image_urls
            else:
                img_urls = note_data.image_urls
            contents.append(self.create_video_content(video_url, img_urls[0] if img_urls else None))
        elif img_urls := note_data.image_urls:
            contents.extend(self.create_image_contents(img_urls))

        author = self.create_author(note_data.user.nickName, note_data.user.avatar)

        return self.result(
            title=note_data.title,
            author=author,
            contents=contents,
            text=note_data.desc,
            timestamp=note_data.time // 1000,
        )

    async def _fetch_init_state(self, client: AsyncClient, url: str) -> str:
        """获取并提取页面中的 __INITIAL_STATE__ 原始 JSON 字符串"""
        response = await client.get(url)
        response.raise_for_status()
        html = response.text
        return self._extract_initial_state_raw(html)

    def _extract_initial_state_raw(self, html: str) -> str:
        """提取 __INITIAL_STATE__ 原始 JSON 字符串"""
        pattern = r"window\.__INITIAL_STATE__=(.*?)</script>"
        matched = re.search(pattern, html)
        if not matched:
            raise ParseException("小红书分享链接失效或内容已删除")
        return matched.group(1).replace("undefined", '""')

    async def _fetch_comments(
        self, client: AsyncClient, note_id: str, xsec_token: str
    ) -> dict:
        """获取笔记评论原始数据字典形式"""
        response = await client.get(
            "https://edith.xiaohongshu.com/api/sns/web/v2/comment/page",
            params={
                "note_id": note_id,
                "cursor": "",
                "top_comment_id": "",
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
            },
        )
        data = response.json()
        if data.get("code") != 0:
            logger.warning("获取小红书评论数据失败")
            logger.error(response.text)
            return {"comments": []}

        return data.get("data", {"comments": []})

    def _build_result(self, note_data: explore.NoteDetailWrapper):
        """从 note_data 构建最终解析结果"""
        note_detail = note_data.note

        contents = note_detail.get_medias(self)

        author = self.create_author(
            name=note_detail.nickname,
            avatar_url=note_detail.avatar_url,
        )

        comment_list = self._build_comments(note_data)

        return self.result(
            title=note_detail.title,
            text=note_detail.desc,
            author=author,
            contents=contents,
            stats=self.create_stats(
                like_count=note_detail.interactInfo.likedCount,
                comment_count=note_detail.interactInfo.commentCount,
                share_count=note_detail.interactInfo.shareCount,
                collect_count=note_detail.interactInfo.collectedCount,
            ),
            comments=comment_list,
            timestamp=note_detail.lastUpdateTime // 1000,
        )

    def _build_comments(self, note_data: explore.NoteDetailWrapper) -> list[Comment]:
        """从 note_data.comments_list 构建标准 Comment 列表"""
        comment_list: list[Comment] = []

        for c in note_data.comments_list.comments:
            comment = self.create_comment(
                author=self.create_author(
                    name=c.userInfo.nickname,
                    avatar_url=c.userInfo.image,
                ),
                content=self._replace_placeholder_to_sticker(c.content),
                timestamp=c.createTime,
                stats=self.create_stats(
                    like_count=c.likeCount,
                    comment_count=str(len(c.subComments)),
                ),
                location=c.ipLocation,
            )

            for sub in c.subComments:
                comment.replies.append(
                    self.create_comment(
                        author=self.create_author(
                            name=sub.userInfo.nickname,
                            avatar_url=sub.userInfo.image,
                        ),
                        content=self._replace_placeholder_to_sticker(sub.content),
                        timestamp=sub.createTime,
                        stats=self.create_stats(
                            like_count=sub.likeCount,
                        ),
                    )
                )

            comment_list.append(comment)

        return comment_list

    def _replace_placeholder_to_sticker(self, content: str) -> list[str]:
        """替换小红书表情占位符为贴纸"""
        parts: list[str] = []
        last_end = 0

        for match in REDNOTE_PATTERN.finditer(content):
            if match.start() > last_end:
                parts.append(content[last_end : match.start()])
            name = match.group("name")
            parts.append(f"[rednote_{name}]")
            last_end = match.end()

        if last_end < len(content):
            parts.append(content[last_end:])

        return parts if parts else [content]
