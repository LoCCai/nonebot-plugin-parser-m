import asyncio
import time
import random
import re
from typing import List, Dict, Any, Optional, Union
from pathlib import Path

from nonebot import on_command, on_regex, require, logger, get_bot
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg, RegexGroup
from nonebot.permission import SUPERUSER

# 引入依赖
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler
from src.utils.message_fx import send_combined_message
# 引入封装好的构造虚构事件函数
from src.utils.user_group_fx import create_fake_message_event

# 引入本地模块
from .config import config_manager
from .data_source import taptap_spider
from .download import downloader

# 插件元数据
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="TapTap开发者订阅",
    description="定时检测TapTap开发者主页更新并推送到群",
    usage="taptap订阅 [用户ID]\ntaptap取消订阅 [用户ID]\ntaptap最新 [用户ID]\n直接发送TapTap链接自动解析",
    extra={}
)

# 全局配置
# 默认为每小时第1分钟开始每隔5分钟执行
TAPTAP_CHECK_CRON = "1/5 * * * *"

# --- 注册命令 ---

sub_cmd = on_command("taptap订阅", aliases={"TapTap订阅"}, permission=SUPERUSER, priority=5, block=True)
unsub_cmd = on_command("taptap取消订阅", aliases={"TapTap取消订阅"}, permission=SUPERUSER, priority=5, block=True)
check_cmd = on_command("taptap最新", aliases={"TapTap最新", "ds_tap"}, priority=10, block=True)

# 管理命令
check_update_cmd = on_command("检查更新", aliases={"check_update", "taptap更新"}, permission=SUPERUSER, priority=5, block=True)
cron_status_cmd = on_command("定时状态", aliases={"cron_status", "定时任务状态"}, permission=SUPERUSER, priority=5, block=True)
force_check_cmd = on_command("强制检查", aliases={"force_check", "立即检查"}, permission=SUPERUSER, priority=5, block=True)

# 正则匹配 TapTap 链接
taptap_link_matcher = on_regex(
    r"taptap\.cn/(user|moment|topic)/(\d+)", 
    priority=50, 
    block=True
)

# --- 核心工具函数 ---

def build_combined_nodes(data: Dict) -> List[Any]:
    """构建合并消息节点列表"""
    nodes = []
    
    # 标题
    nodes.append(f"【TapTap动态更新】\n{data['title']}")
    
    # 摘要
    if data['summary']:
        summary = data['summary'][:2000] + "..." if len(data['summary']) > 2000 else data['summary']
        nodes.append(summary)
    
    # 图片
    if data['images']:
        for img_url in data['images']:
            nodes.append(MessageSegment.image(img_url))
    
    # 尾部链接
    nodes.append(f"原文链接: {data['url']}")
    
    return nodes

async def send_post_content(
    bot: Bot, 
    target_id: int, 
    target_type: str, 
    detail: Dict, 
    mention_all: bool = False
):
    """
    通用发送函数：发送图文合并消息 + 视频
    target_type: 'group' 或 'private'
    """
    is_group = (target_type == 'group')
    
    # 1. @全体成员 (仅群聊且开启)
    if mention_all and is_group:
        try:
            # 构造消息: @全体 + 标题
            notify_msg = MessageSegment.at("all") + Message(f" {detail.get('title', 'TapTap动态更新')}")
            await bot.send_group_msg(group_id=target_id, message=notify_msg)
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"发送@全体成员失败: {e}")
            mention_all = False

    if not mention_all:
        notify_msg = Message(f" {detail.get('title', 'TapTap动态更新')}")
        await bot.send_group_msg(group_id=target_id, message=notify_msg)
    
    # 2. 发送图文内容 (合并消息)
    combined_nodes = build_combined_nodes(detail)
    
    try:
        # 使用封装好的 create_fake_message_event
        if is_group:
            fake_event = create_fake_message_event(
                bot=bot,
                message_type="group",
                group_id=target_id,
                user_id=bot.self_id, 
                nickname="TapTap助手",
                message="TapTap Push"
            )
        else:
            fake_event = create_fake_message_event(
                bot=bot,
                message_type="private",
                user_id=target_id, # 私聊对象ID
                nickname="TapTap助手",
                message="TapTap Push"
            )

        # 统一调用
        await send_combined_message(bot, fake_event, combined_nodes)

    except Exception as e:
        logger.error(f"发送合并消息失败: {e}")
        # 降级发送纯链接
        link_msg = f"内容解析失败，请查看原文: {detail['url']}"
        try:
            if is_group:
                await bot.send_group_msg(group_id=target_id, message=link_msg)
            else:
                await bot.send_private_msg(user_id=target_id, message=link_msg)
        except:
            pass
        return

    # 3. 单独发送视频
    if detail.get('videos'):
        for i, vid_url in enumerate(detail['videos']):
            await asyncio.sleep(2)
            try:
                video_seg = None
                
                # 情况A: m3u8 -> 下载 -> 发送
                if '.m3u8' in vid_url:
                    # 非自动推送时给个提示
                    if not mention_all:
                        hint = "⏳ 正在下载视频资源，请稍候..."
                        if is_group:
                            await bot.send_group_msg(group_id=target_id, message=hint)
                        else:
                            await bot.send_private_msg(user_id=target_id, message=hint)
                    
                    file_id = f"{detail['id']}_{i}"
                    video_path = await downloader.download_video(vid_url, file_id)
                    
                    if video_path and video_path.exists():
                        video_seg = MessageSegment.video(video_path.absolute())
                
                # 情况B: mp4 -> 直接发送
                elif '.mp4' in vid_url:
                    video_seg = MessageSegment.video(vid_url)
                
                # 发送视频消息
                if video_seg:
                    if is_group:
                        await bot.send_group_msg(group_id=target_id, message=video_seg)
                    else:
                        await bot.send_private_msg(user_id=target_id, message=video_seg)
                else:
                    # 降级链接
                    link_text = f"🎬 视频下载失败，请点击观看:\n{vid_url}"
                    if is_group:
                        await bot.send_group_msg(group_id=target_id, message=link_text)
                    else:
                        await bot.send_private_msg(user_id=target_id, message=link_text)

            except Exception as e:
                logger.error(f"视频发送异常: {e}")
                err_text = f"⚠️ 视频发送出错:\n{vid_url}"
                if is_group:
                    await bot.send_group_msg(group_id=target_id, message=err_text)
                else:
                    await bot.send_private_msg(user_id=target_id, message=err_text)

# --- 检查器类与定时任务 ---

class TaptapUpdateChecker:
    def __init__(self):
        self._running = False
        self._check_lock = asyncio.Lock()
        
    async def check_all_subscriptions(self):
        """检查所有订阅的核心逻辑"""
        async with self._check_lock:
            if self._running:
                logger.info("[TapTap] 已有检查任务正在运行，跳过本次检查")
                return
            
            self._running = True
            try:
                logger.info("[TapTap] 开始检查所有订阅更新...")
                subscriptions = config_manager.get_all_subscriptions()
                
                if not subscriptions:
                    logger.info("[TapTap] 当前没有订阅任何用户")
                    return

                processed_count = 0
                for user_id, targets in subscriptions.items():
                    try:
                        # 1. 获取最新动态简略信息
                        latest_simple = await taptap_spider.fetch_user_latest_post(user_id)
                        if not latest_simple:
                            continue
                        
                        latest_id = latest_simple['id']
                        last_seen_id = config_manager.get_last_id(user_id)
                        
                        # 2. 对比ID，如果有更新
                        if latest_id != last_seen_id:
                            logger.info(f"[TapTap] 用户 {user_id} 发现新动态: {latest_id}")
                            
                            # 3. 获取详情 (含视频嗅探)
                            detail = await taptap_spider.fetch_post_detail(latest_id, latest_simple)
                            
                            try:
                                bot: Bot = get_bot()
                            except Exception:
                                logger.error("[TapTap] 获取bot实例失败，无法推送")
                                continue
                            
                            # 4. 推送给群 (mention_all=True)
                            for group_id in targets.get("groups", []):
                                await send_post_content(bot, group_id, 'group', detail, mention_all=True)
                                await asyncio.sleep(2)
                            
                            # 5. 推送给好友
                            for user_qq in targets.get("friends", []):
                                await send_post_content(user_qq, 'private', detail, mention_all=False)
                                await asyncio.sleep(2)
                            
                            # 6. 更新历史记录
                            config_manager.update_last_id(user_id, latest_id)
                            processed_count += 1
                        
                        # 每个用户检查间隔，防止IP被封
                        await asyncio.sleep(5)
                        
                    except Exception as e:
                        logger.error(f"[TapTap] 检查用户 {user_id} 时发生错误: {e}")
                        continue
                
                logger.info(f"[TapTap] 检查完成，处理了 {processed_count} 个更新")
            finally:
                self._running = False
    
    def is_running(self):
        return self._running

# 创建检查器实例
taptap_checker = TaptapUpdateChecker()

# 注册定时任务
# 注意：scheduler.add_job 需要在插件加载时执行
@scheduler.scheduled_job("cron", minute=TAPTAP_CHECK_CRON.split()[0], id="taptap_check_update")
async def scheduled_check_updates():
    await taptap_checker.check_all_subscriptions()

# --- 命令响应处理 ---

@check_update_cmd.handle()
async def handle_check_update():
    """手动触发检查更新"""
    if taptap_checker.is_running():
        await check_update_cmd.finish("检查更新任务正在运行中，请稍后再试")
    
    await check_update_cmd.send("开始手动检查TapTap更新...")
    try:
        await taptap_checker.check_all_subscriptions()
        await check_update_cmd.finish("检查完成")
    except Exception as e:
        logger.error(f"[TapTap] 手动检查失败: {e}")
        await check_update_cmd.finish(f"检查失败: {str(e)}")

@force_check_cmd.handle()
async def handle_force_check():
    """强制立即检查"""
    await force_check_cmd.send("开始强制检查TapTap更新...")
    try:
        await taptap_checker.check_all_subscriptions()
        await force_check_cmd.finish("强制检查完成")
    except Exception as e:
        logger.error(f"[TapTap] 强制检查失败: {e}")
        await force_check_cmd.finish(f"强制检查失败: {str(e)}")

@cron_status_cmd.handle()
async def handle_cron_status(args: Message = CommandArg()):
    """查看和管理定时任务状态"""
    # 【修复】将 global 声明移至函数最上方
    global TAPTAP_CHECK_CRON
    
    arg_text = args.extract_plain_text().strip()
    job_id = "taptap_check_update"
    
    if not arg_text:
        status = "运行中" if taptap_checker.is_running() else "空闲"
        job = scheduler.get_job(job_id)
        next_run = job.next_run_time if job else "未调度"
        
        await cron_status_cmd.finish(
            f"TapTap定时任务状态:\n"
            f"• 运行状态: {status}\n"
            f"• Cron表达式: {TAPTAP_CHECK_CRON}\n"
            f"• 下次运行: {next_run}\n"
            f"• 指令: 定时状态 [pause|resume|set 分钟]"
        )
    
    elif arg_text == "pause":
        scheduler.pause_job(job_id)
        await cron_status_cmd.finish("已暂停定时任务")
    
    elif arg_text == "resume":
        scheduler.resume_job(job_id)
        await cron_status_cmd.finish("已恢复定时任务")
    
    elif arg_text.startswith("set "):
        new_val = arg_text[4:].strip()
        try:
            # 这里的 new_val 是分钟数，比如 "1/10"
            scheduler.reschedule_job(job_id, trigger='cron', minute=new_val)
            # 更新全局变量
            TAPTAP_CHECK_CRON = f"{new_val} * * * *" 
            await cron_status_cmd.finish(f"已更新定时频率: 每小时 {new_val} 分钟执行")
        except Exception as e:
            await cron_status_cmd.finish(f"设置失败: {e}")

@sub_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = args.extract_plain_text().strip()
    if not user_id.isdigit():
        await sub_cmd.finish("ID必须为纯数字")
    
    await sub_cmd.send(f"正在查询 TapTap 用户 {user_id}...")
    profile = await taptap_spider.fetch_user_profile(user_id)
    
    if not profile:
        await sub_cmd.finish("未找到该用户，请检查ID")
    
    nickname = profile.get("nickname", "未知")
    
    if isinstance(event, GroupMessageEvent):
        sub_type = "groups"
        sub_id = event.group_id
    else:
        sub_type = "friends"
        sub_id = event.user_id
        
    msg = config_manager.add_subscription(user_id, sub_type, sub_id)
    await sub_cmd.finish(f"✅ {msg}\n目标: 【{nickname}】")

@unsub_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    user_id = args.extract_plain_text().strip()
    if not user_id:
        await unsub_cmd.finish("请提供用户ID")
        
    if isinstance(event, GroupMessageEvent):
        sub_type = "groups"
        sub_id = event.group_id
    else:
        sub_type = "friends"
        sub_id = event.user_id
        
    msg = config_manager.del_subscription(user_id, sub_type, sub_id)
    await unsub_cmd.finish(msg)

@check_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = args.extract_plain_text().strip()
    if not user_id:
        await check_cmd.finish("请输入TapTap用户ID")
    
    await check_cmd.send("🔍 正在获取最新动态...")
    
    latest = await taptap_spider.fetch_user_latest_post(user_id)
    if not latest:
        await check_cmd.finish("未找到动态")
        
    detail = await taptap_spider.fetch_post_detail(latest['id'], latest)
    
    target_type = 'group' if isinstance(event, GroupMessageEvent) else 'private'
    target_id = event.group_id if isinstance(event, GroupMessageEvent) else event.user_id
    
    await send_post_content(bot, target_id, target_type, detail)

@taptap_link_matcher.handle()
async def _(bot: Bot, event: MessageEvent, matched: tuple = RegexGroup()):
    link_type, link_id = matched
    logger.info(f"[TapTap] 解析链接: {link_type} {link_id}")
    
    await taptap_link_matcher.send("🔍 正在解析链接...")
    
    target_type = 'group' if isinstance(event, GroupMessageEvent) else 'private'
    target_id = event.group_id if isinstance(event, GroupMessageEvent) else event.user_id
    
    try:
        if link_type == 'user':
            latest = await taptap_spider.fetch_user_latest_post(link_id)
            if latest:
                detail = await taptap_spider.fetch_post_detail(latest['id'], latest)
                await send_post_content(bot, target_id, target_type, detail)
            else:
                await taptap_link_matcher.finish("该用户暂无动态")
        
        elif link_type in ['moment', 'topic']:
            detail = await taptap_spider.fetch_post_detail(link_id)
            if detail and detail.get('title'):
                await send_post_content(bot, target_id, target_type, detail)
            else:
                await taptap_link_matcher.finish("获取动态详情失败")
                
    except Exception as e:
        logger.error(f"解析异常: {e}")
        await taptap_link_matcher.finish("❌ 解析出错")