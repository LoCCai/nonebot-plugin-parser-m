from msgspec import Struct, field
from msgspec.json import Decoder

from ..data import MediaContent
from ..base import BaseParser


class StreamUrl(Struct):
    """Wrapper for stream url"""

    masterUrl: str


class Stream(Struct):
    """Wrapper for image stream"""

    h264: list[StreamUrl] = field(default_factory=list)
    h265: list[StreamUrl] = field(default_factory=list)
    h266: list[StreamUrl] = field(default_factory=list)
    av1: list[StreamUrl] = field(default_factory=list)

    @property
    def url(self) -> str:
        """
        获取第一个非空流列表中的第一个可用 URL

        优先级: h265 > h264 > h266 > av1
        """
        h264, h265, h266, av1 = self.h264, self.h265, self.h266, self.av1
        for stream_list in (h265, h264, h266, av1):
            if stream_list:
                return stream_list[0].masterUrl
        raise ValueError("Stream.url: no available stream url found")


class Media(Struct):
    """视频媒体容器"""

    stream: Stream


class Video(Struct):
    """笔记中的主视频信息"""

    media: Media

    @property
    def video_url(self) -> str:
        """主视频直链"""
        return self.media.stream.url


class Image(Struct):
    urlDefault: str
    livePhoto: bool = False
    stream: Stream = field(default_factory=Stream)


class User(Struct):
    """用户信息"""

    nickname: str
    avatar: str


class InteractInfo(Struct):
    """互动信息"""

    likedCount: str
    collectedCount: str
    commentCount: str
    shareCount: str


class NoteDetail(Struct):
    type: str
    title: str
    desc: str
    user: User
    lastUpdateTime: int
    interactInfo: InteractInfo
    imageList: list[Image] = field(default_factory=list)
    video: Video | None = None

    @property
    def nickname(self) -> str:
        """作者昵称"""
        return self.user.nickname

    @property
    def avatar_url(self) -> str:
        """作者头像地址"""
        return self.user.avatar

    @property
    def image_urls(self) -> list[str]:
        """图片URL列表（向后兼容）"""
        return [item.urlDefault for item in self.imageList]

    @property
    def video_url(self) -> str | None:
        """视频URL（向后兼容）"""
        if self.type != "video" or not self.video:
            return None
        return self.video.video_url

    def get_medias(self, parser: BaseParser) -> list[MediaContent]:
        """
        统一构建当前笔记的媒体内容列表

        - Live Photo -> LivePhotoContent
        - 普通图片   -> ImageContent
        - 主视频     -> VideoContent
        """
        items: list[MediaContent] = []

        for img in self.imageList:
            if img.livePhoto:
                items.append(
                    parser.create_live_photo_content(
                        video_url=img.stream.url,
                        image_url=img.urlDefault,
                    )
                )
            else:
                items.extend(parser.create_image_contents([img.urlDefault]))
        
        if self.video:
            if v_url := self.video.video_url:
                cover_url = self.image_urls[0] if self.image_urls else None
                items.append(
                    parser.create_video_content(v_url, cover_url=cover_url)
                )

        return items


class CommentUser(Struct):
    nickname: str
    image: str
    userId: str


class Comment(Struct):
    userInfo: CommentUser
    createTime: int
    content: str
    likeCount: str
    ipLocation: str
    pictures: list[Image] = field(default_factory=list)
    subComments: list["Comment"] = field(default_factory=list)


class CommentList(Struct):
    comments: list[Comment] = field(default_factory=list)


class NoteDetailWrapper(Struct):
    """Wrapper for note detail, represents the value in noteDetailMap[xhs_id]"""

    note: NoteDetail
    comments_list: CommentList = field(default_factory=CommentList)


class Note(Struct):
    """Top-level note container with noteDetailMap"""

    noteDetailMap: dict[str, NoteDetailWrapper]


class InitialState(Struct):
    """Root structure of window.__INITIAL_STATE__"""

    note: Note


decoder = Decoder(InitialState)
