from re import Match
from typing import ClassVar

import aiotieba

from .base import (
    BaseParser,
    handle,
)
from .data import Platform, MediaContent
from ..constants import PlatformEnum


class TiebaParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.TIEBA, display_name="百度贴吧"
    )

    @handle("tieba.baidu.com", r"tieba\.baidu\.com/p/(?P<post_id>\d+)")
    async def _parse(self, searched: Match[str]):
        # TODO: 显示吧头像
        post_id = searched.group("post_id")

        async with aiotieba.Client() as client:
            # 获取帖子内容
            posts = await client.get_posts(int(post_id), pn=1)

        # 提取主题帖信息
        thread = posts.thread
        forum = posts.forum
        
        # 提取作者信息
        author = self.create_author(
            name=thread.user.show_name,
            avatar_url=f"https://gss0.baidu.com/7Ls0a8Sm2Q5IlBGlnYG/sys/portrait/item/{thread.user.portrait}",
        )

        # 主楼正文内容
        contents: list[MediaContent] = []
        text_parts = []
        
        # 提取帖子标题
        text_parts.append(thread.title)
        text_parts.append("\n")
        
        # 提取帖子正文
        if posts and posts.objs:
            main_post = posts.objs[0]
            # 处理文本内容
            text_parts.append(main_post.text)
            # 处理图片内容
            for image in main_post.contents.imgs:
                contents.append(
                    self.create_graphics_content(image_url=image.origin_src)
                )

        extra = {
            "forum": {
                "name": forum.fname,
            }
        }

        return self.result(
            title=thread.title,
            text="".join(text_parts),
            author=author,
            contents=contents,
            timestamp=thread.create_time,
            url=f"https://tieba.baidu.com/p/{post_id}",
            extra=extra,
        )
