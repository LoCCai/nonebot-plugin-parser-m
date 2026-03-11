from re import Match
from typing import ClassVar, List
from datetime import datetime

import aiotieba

from .base import (
    BaseParser,
    handle,
)
from .data import Platform, MediaContent
from ..constants import PlatformEnum


def build_comment_content(contents):
    """
    构建贴吧评论HTML内容

    :param contents: 评论内容对象
    :return: HTML字符串
    """
    content = ""
    if hasattr(contents, "objs"):
        for part in contents.objs:
            if hasattr(part, "origin_src"):
                content += (
                    '<div class="images-container">'
                    '<div class="images-grid single">'
                    '<div class="image-item">'
                    f'<img src="{part.origin_src}">'
                    '</div></div></div>'
                )
            elif hasattr(part, "id") and hasattr(part, "desc"):
                content += f'<img class="sticker small" src="https://tb3.bdstatic.com/emoji/{part.id}@2x.png">'
            elif hasattr(part, "user_id"):
                content += f"@{part.text}&nbsp;"
            elif hasattr(part, "raw_url"):
                content += f'<a href="{part.url}" target="_blank">{part.url}</a>'
            elif hasattr(part, "text"):
                content += part.text
    elif hasattr(contents, "text"):
        content = contents.text
    else:
        content = str(contents)
    return content


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
            posts = await client.get_posts(int(post_id), pn=1, with_comments=True)

        # 提取主题帖信息
        thread = posts.thread
        forum = posts.forum

        # 提取作者信息
        author = self.create_author(
            name=thread.user.show_name,
            avatar_url=f"https://gss0.baidu.com/7Ls0a8Sm2Q5IlBGlnYG/sys/portrait/item/{thread.user.portrait}",
        )

        # 主楼正文内容
        contents: List[MediaContent] = []
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
            # 处理视频内容
            # 注意：aiotieba库的Contents_p对象可能没有videos属性
            # 我们需要使用更通用的方式来检查视频内容
            if hasattr(main_post.contents, "objs"):
                for part in main_post.contents.objs:
                    # 检查是否是视频内容
                    if hasattr(part, "src") and hasattr(part, "cover_src"):
                        # 像B站一样，将视频作为媒体资源处理
                        async def download_video():
                            from .base import DOWNLOADER, DurationLimitException, pconfig

                            # 生成唯一的文件名
                            video_id = part.src.split("/")[-1].split(".")[0] if "/" in part.src else "tieba_video"
                            output_path = pconfig.cache_dir / f"tieba-{video_id}.mp4"

                            # 如果文件已存在，直接返回
                            if output_path.exists():
                                return output_path

                            # 检查视频时长
                            if hasattr(part, "duration") and part.duration > pconfig.duration_maximum:
                                raise DurationLimitException

                            # 下载视频
                            return await DOWNLOADER.download_video(part.src, video_name=output_path.name)

                        # 创建视频内容
                        video_content = self.create_video_content(
                            download_video,
                            part.cover_src,
                            part.duration if hasattr(part, "duration") else 0
                        )
                        contents.append(video_content)

        # 处理评论
        comments = []
        if posts and posts.objs:
            # 获取前10条评论（优先显示楼主的评论）
            main_author_id = thread.user.user_id
            main_comments = []
            other_comments = []

            for post in posts.objs[1:]:  # 跳过主楼
                if post.user.user_id == main_author_id:
                    main_comments.append(post)
                else:
                    other_comments.append(post)

            # 合并评论，优先显示楼主的评论
            combined_comments = main_comments[:5] + other_comments[:5]

            for post in combined_comments:
                # 处理评论作者信息
                comment_author = {
                    "name": post.user.show_name,
                    "avatar": f"https://gss0.baidu.com/7Ls0a8Sm2Q5IlBGlnYG/sys/portrait/item/{post.user.portrait}"
                }

                # 处理评论内容
                comment_content = build_comment_content(post.contents)

                # 处理评论时间
                formatted_time = ""
                if hasattr(post, "create_time") and post.create_time:
                    try:
                        dt = datetime.fromtimestamp(post.create_time)
                        formatted_time = dt.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass

                # 处理楼中楼评论
                child_posts = []
                if hasattr(post, "comments") and post.comments:
                    for comment in post.comments[:3]:  # 每个评论最多显示3条楼中楼
                        child_author = {
                            "name": comment.user.show_name,
                            "avatar": f"https://gss0.baidu.com/7Ls0a8Sm2Q5IlBGlnYG/sys/portrait/item/{comment.user.portrait}"
                        }

                        child_content = build_comment_content(comment.contents)

                        child_formatted_time = ""
                        if hasattr(comment, "create_time") and comment.create_time:
                            try:
                                dt = datetime.fromtimestamp(comment.create_time)
                                child_formatted_time = dt.strftime("%Y-%m-%d %H:%M")
                            except Exception:
                                pass

                        child_posts.append({
                            "author": child_author,
                            "content": child_content,
                            "formatted_time": child_formatted_time,
                            "ups": comment.agree
                        })

                comments.append({
                    "author": comment_author,
                    "content": comment_content,
                    "formatted_time": formatted_time,
                    "ups": post.agree,
                    "comments": len(child_posts),
                    "child_posts": child_posts
                })

        extra = {
            "forum": {
                "name": forum.fname,
            },
            "comments": comments
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
