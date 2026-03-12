from msgspec import Struct


class User(Struct):
    username: str
    avatar_url: str


class Comment(Struct):
    user: User
    content: list
    create_at: int
    up: int
    child_num: int
    ip_location: str


class CommentWrapper(Struct):
    comment: list[Comment]


class Link(Struct):
    title: str
    content: list
    create_at: int
    click: int
    link_award_num: int
    comment_num: int
    forward_num: int
    favour_count: int
    user: User


class BaseResult(Struct):
    link: Link
    comments: list[CommentWrapper]
