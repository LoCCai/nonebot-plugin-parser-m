from msgspec import Struct


class Sender(Struct):
    id: int
    username: str
    avatar: str


class Photo(Struct):
    path: str


class BlogData(Struct):
    id: int
    msg: str
    add_datetime_ts: int
    like_count: int
    favorite_count: int
    reply_count: int
    photo: Photo
    sender: Sender


class AtlasData(Struct):
    id: int
    desc: str
    img_list: list[str]
    visit_count: int
    like_count: int
    favorite_count: int
    comment_count: int
    created_at: int
    sender: Sender


class SubComment(Struct):
    content: str
    add_datetime_ts: int
    ipaddr: str
    sender: Sender


class RootComment(Struct):
    content: str
    create_time: int
    like_count: int
    reply_count: int
    ipaddr: str
    sender: Sender
    replies: list[SubComment]
    img_list: list[str]


class CommentData(Struct):
    object_list: list[RootComment]
