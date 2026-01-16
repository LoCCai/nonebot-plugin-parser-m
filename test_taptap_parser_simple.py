import json
import re
from typing import Optional, Dict, Any, List

# 简化的解析函数，直接测试数据提取逻辑
def resolve_nuxt_value(root_data: list, value: Any) -> Any:
    """Nuxt数据解压"""
    if isinstance(value, int):
        if 0 <= value < len(root_data):
            return root_data[value]
        return value
    return value

def extract_taptap_data(nuxt_data: list) -> Dict[str, Any]:
    """从Nuxt数据中提取TapTap动态信息"""
    result = {
        "title": "",
        "summary": "",
        "author": {
            "name": "",
            "avatar": "",
            "honor_title": ""
        },
        "publish_time": "",
        "stats": {
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "plays": 0,
            "views": 0
        },
        "video_duration": 0,
        "video_id": ""
    }
    
    for item in nuxt_data:
        if not isinstance(item, dict):
            continue
            
        # 处理包含 user 字段的对象，提取作者信息
        if 'user' in item:
            user_ref = item['user']
            user_obj = resolve_nuxt_value(nuxt_data, user_ref)
            if isinstance(user_obj, dict):
                # 提取作者名称
                result['author']['name'] = resolve_nuxt_value(nuxt_data, user_obj.get('name', '')) or ''
                # 提取作者头像
                if 'avatar' in user_obj:
                    avatar = resolve_nuxt_value(nuxt_data, user_obj['avatar'])
                    if isinstance(avatar, str) and avatar.startswith('http'):
                        result['author']['avatar'] = avatar
                    elif isinstance(avatar, dict) and 'original_url' in avatar:
                        result['author']['avatar'] = resolve_nuxt_value(nuxt_data, avatar['original_url']) or ''
        
        # 处理包含 title 和 summary 字段的对象，提取标题和完整摘要
        if 'title' in item and 'summary' in item:
            title = resolve_nuxt_value(nuxt_data, item['title'])
            summary = resolve_nuxt_value(nuxt_data, item['summary'])
            if title and isinstance(title, str):
                result['title'] = title
            if summary and isinstance(summary, str):
                result['summary'] = summary
        
        # 处理包含 stat 字段的对象，提取统计信息
        if 'stat' in item:
            stat_ref = item['stat']
            stat_obj = resolve_nuxt_value(nuxt_data, stat_ref)
            if isinstance(stat_obj, dict):
                # 提取点赞数
                result['stats']['likes'] = stat_obj.get('supports', 0) or stat_obj.get('likes', 0)
                # 提取评论数
                result['stats']['comments'] = stat_obj.get('comments', 0)
                # 提取分享数
                result['stats']['shares'] = stat_obj.get('shares', 0)
                # 提取浏览数
                result['stats']['views'] = stat_obj.get('pv_total', 0)
                # 提取播放数
                result['stats']['plays'] = stat_obj.get('play_total', 0)
        
        # 处理包含 contents 字段的对象，提取额外文本内容
        if 'contents' in item:
            contents = resolve_nuxt_value(nuxt_data, item['contents'])
            if isinstance(contents, list):
                text_parts = []
                for content_item in contents:
                    if isinstance(content_item, dict):
                        # 处理文本内容
                        if 'text' in content_item:
                            text = resolve_nuxt_value(nuxt_data, content_item['text'])
                            if text and isinstance(text, str):
                                text_parts.append(text)
                        # 处理段落内容
                        elif content_item.get('type') == 'paragraph':
                            children = content_item.get('children')
                            if isinstance(children, list):
                                for child in children:
                                    if isinstance(child, dict) and 'text' in child:
                                        child_text = resolve_nuxt_value(nuxt_data, child['text'])
                                        if child_text and isinstance(child_text, str):
                                            text_parts.append(child_text)
                if text_parts and not result['summary']:  # 只有当没有从summary字段提取到内容时，才使用contents字段的内容
                    result['summary'] = '\n'.join(text_parts)
        
        # 提取发布时间
        if 'created_at' in item or 'publish_time' in item:
            publish_time = resolve_nuxt_value(nuxt_data, item.get('created_at') or item.get('publish_time'))
            if publish_time:
                result['publish_time'] = publish_time
        
        # 提取视频信息
        if 'pin_video' in item:
            video_info = resolve_nuxt_value(nuxt_data, item['pin_video'])
            if isinstance(video_info, dict):
                # 提取视频时长
                if 'duration' in video_info:
                    result['video_duration'] = resolve_nuxt_value(nuxt_data, video_info['duration'])
                # 提取视频ID
                if 'video_id' in video_info:
                    result['video_id'] = resolve_nuxt_value(nuxt_data, video_info['video_id'])
        
        # 提取作者等级和标签
        if 'honor_title' in item:
            result['author']['honor_title'] = resolve_nuxt_value(nuxt_data, item['honor_title']) or ''
        if 'honor_obj_id' in item:
            result['author']['honor_obj_id'] = resolve_nuxt_value(nuxt_data, item['honor_obj_id']) or ''
        if 'honor_obj_type' in item:
            result['author']['honor_obj_type'] = resolve_nuxt_value(nuxt_data, item['honor_obj_type']) or ''
    
    if not result['title']:
        result['title'] = "TapTap 动态分享"
    
    return result

def test_extract_taptap_data():
    """测试从HTML文件中提取TapTap数据"""
    try:
        # 读取HTML文件
        with open('src/nonebot_plugin_parser/parsers/taptap/get_html.txt', 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # 提取__NUXT_DATA__内容
        match = re.search(r'<script type="application/json" id="__NUXT_DATA__"[^>]*>(.*?)</script>', html_content, re.DOTALL)
        if not match:
            print("未找到__NUXT_DATA__")
            return None
        
        nuxt_json = match.group(1)
        nuxt_data = json.loads(nuxt_json)
        
        print("提取到Nuxt数据，长度:", len(nuxt_data))
        
        # 提取TapTap动态信息
        result = extract_taptap_data(nuxt_data)
        
        print("\n解析结果:")
        print(f"标题: {result['title']}")
        print(f"摘要: {result['summary']}")
        print(f"作者: {result['author']['name']}")
        print(f"作者头像: {result['author']['avatar']}")
        print(f"作者头衔: {result['author']['honor_title']}")
        print(f"发布时间: {result['publish_time']}")
        print(f"统计信息: {result['stats']}")
        print(f"视频时长: {result['video_duration']}秒")
        print(f"视频ID: {result['video_id']}")
        
        return result
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    test_extract_taptap_data()
