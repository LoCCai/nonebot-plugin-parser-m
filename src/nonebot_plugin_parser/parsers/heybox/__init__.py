from re import Match
from typing import ClassVar

from msgspec import convert
from nonebot.log import logger
from httpx import AsyncClient

from ...browser_pool import BROWSER
from ...utils import format_num
from ..base import BaseParser, ParseException, Platform, PlatformEnum, handle
from ..data import Comment

from .encrypt import build_url
from .model import BaseResult


class HeyBoxParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.HEYBOX, display_name="小黑盒"
    )
    x_xhh_tokenid: str = ""

    def __init__(self):
        super().__init__()
        self.headers.update(
            {
                "Referer": "https://www.xiaoheihe.cn/",
                "Host": "api.xiaoheihe.cn",
                "Origin": "https://www.xiaoheihe.cn",
                "Accept": "application/json, text/plain, */*",
            }
        )

    @handle(
        "api.xiaoheihe.cn/v3/bbs/app/api/web/share",
        r"link_id=(?P<link_id>[A-Za-z0-9]+)",
    )
    @handle("xiaoheihe.cn/bbs/post_share", r"link_id=(?P<link_id>[A-Za-z0-9]+)")
    @handle("xiaoheihe.cn/app/bbs", r"link\/(?P<link_id>[A-Za-z0-9]+)")
    async def _parse(self, searched: Match[str]):
        link_id = searched["link_id"]

        if not self.x_xhh_tokenid:
            tab = await BROWSER.new_tab(url="https://www.xiaoheihe.cn/")
            try:
                self.x_xhh_tokenid = await tab.run_js("window.SMSdk.getDeviceId()", as_expr=True)
                logger.info(f"成功获取到小黑盒tokenid: {self.x_xhh_tokenid[:5]}...")
            finally:
                await tab.close()

        async with AsyncClient(
            headers=self.headers,
            cookies={"x_xhh_tokenid": self.x_xhh_tokenid},
        ) as client:
            response = await client.get(build_url(link_id))
            response.raise_for_status()
            res = response.json()

        if res.get("status") != "ok":
            raise ParseException(f"小黑盒解析失败: {res}")

        data = convert(res["result"], BaseResult)
        comments = self._build_comments(data)

        # 将 content 列表转换为字符串
        content_text = "\n".join(str(item) for item in data.link.content) if data.link.content else None
        
        return self.result(
            title=data.link.title,
            text=content_text,
            timestamp=data.link.create_at,
            url=f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}",
            author=self.create_author(
                name=data.link.user.username,
                avatar_url=data.link.user.avatar_url,
            ),
        )

    def _build_comments(self, data: BaseResult) -> list[Comment]:
        """
        根据小黑盒返回的数据构建评论和子回复列表。该方法会处理根评论和其下的所有子评论。

        :param data: 已转换好的帖子结果数据。
        :return: Comment 列表。
        """
        comments: list[Comment] = []

        for wrapper in data.comments:
            comment_list = wrapper.comment
            if not comment_list:
                continue

            root = comment_list[0]
            # 将 content 列表转换为字符串
            root_content = "\n".join(str(item) for item in root.content) if root.content else None
            
            root_comment = self.create_comment(
                author=self.create_author(
                    name=root.user.username,
                    avatar_url=root.user.avatar_url,
                ),
                content=root_content,
                timestamp=root.create_at,
                stats=self.create_stats(
                    like_count=format_num(root.up),
                    comment_count=format_num(root.child_num),
                ),
                location=root.ip_location,
            )

            for child in comment_list[1:]:
                # 将 content 列表转换为字符串
                child_content = "\n".join(str(item) for item in child.content) if child.content else None
                
                root_comment.replies.append(
                    self.create_comment(
                        author=self.create_author(
                            name=child.user.username,
                            avatar_url=child.user.avatar_url,
                        ),
                        content=child_content,
                        timestamp=child.create_at,
                        stats=self.create_stats(
                            like_count=format_num(child.up),
                            comment_count=format_num(child.child_num),
                        ),
                        location=child.ip_location,
                    )
                )

            comments.append(root_comment)

        return comments
