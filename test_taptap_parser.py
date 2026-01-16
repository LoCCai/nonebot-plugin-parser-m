import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from src.nonebot_plugin_parser.parsers.taptap.common import TapTapParser

async def test_taptap_parser():
    parser = TapTapParser()
    post_id = "756802464929287464"
    
    try:
        print(f"测试 TapTap Parser 解析动态 {post_id}...")
        result = await parser._parse_post_detail(post_id)
        
        print("\n解析结果:")
        print(f"标题: {result['title']}")
        print(f"摘要: {result['summary']}")
        print(f"作者: {result['author']['name']}")
        print(f"作者头像: {result['author']['avatar']}")
        print(f"发布时间: {result['publish_time']}")
        print(f"统计信息: {result['stats']}")
        print(f"视频数量: {len(result['videos'])}")
        print(f"图片数量: {len(result['images'])}")
        print(f"视频链接: {result['videos'] if result['videos'] else '无'}")
        
        return result
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    asyncio.run(test_taptap_parser())
