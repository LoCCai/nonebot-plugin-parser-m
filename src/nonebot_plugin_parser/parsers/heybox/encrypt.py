import hashlib
import time


def build_url(link_id: str) -> str:
    """
    构建小黑盒API请求URL
    
    Args:
        link_id: 小黑盒分享链接ID
        
    Returns:
        完整的API请求URL
    """
    timestamp = str(int(time.time()))
    app_version = "3.10.30"
    device_id = "b074f8c3-bd01-4a45-89a2-7193c7f64453"
    key = "XiaoHeiHe"
    
    # 生成签名
    sign_str = f"app_version={app_version}&device_id={device_id}&link_id={link_id}&timestamp={timestamp}{key}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    
    # 构建URL
    url = (
        f"https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?"
        f"app_version={app_version}&"
        f"device_id={device_id}&"
        f"link_id={link_id}&"
        f"timestamp={timestamp}&"
        f"sign={sign}"
    )
    
    return url
