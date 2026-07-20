"""测试插件: 图文混合原生markdown 使用本地图片 + @用户

按 COS → B站 → QQ频道 顺序尝试已启用的图床, 任一成功即用。
注: 图片域名需先在 QQ开放平台 → 机器人 → 开发设置 → 消息URL配置 报备, 否则不显示。
注: image_hosting.upload_qq 返回的是 MD5 拼接 URL (404), 故本插件直接调频道 API 解析 attachments[0].url。
"""

import os
import struct
import random

from core.plugin.decorators import handler
from core.base.logger import get_logger, PLUGIN

log = get_logger(PLUGIN, "测试图片")

_IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'images')
_IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')


# ==================== 选图 / 读尺寸 ====================

def _pick_random_image():
    if not os.path.isdir(_IMAGES_DIR):
        return None
    files = [f for f in os.listdir(_IMAGES_DIR) if f.lower().endswith(_IMAGE_EXTS)]
    return os.path.join(_IMAGES_DIR, random.choice(files)) if files else None


def _image_size(data):
    """不依赖 PIL, 解析 PNG/JPEG/GIF/WebP 文件头"""
    try:
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return struct.unpack('>II', data[16:24])
        if data[:3] == b'GIF':
            return struct.unpack('<HH', data[6:10])
        if data[:2] == b'\xff\xd8':
            i = 2
            while i < len(data):
                while i < len(data) and data[i] != 0xFF: i += 1
                while i < len(data) and data[i] == 0xFF: i += 1
                marker = data[i]; i += 1
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack('>HH', data[i + 3:i + 7])
                    return (w, h)
                i += struct.unpack('>H', data[i:i + 2])[0]
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            ck = data[12:16]
            if ck == b'VP8 ':
                w, h = struct.unpack('<HH', data[26:30])
                return (w & 0x3fff, h & 0x3fff)
            if ck == b'VP8L':
                b0, b1, b2, b3 = data[21:25]
                return (1 + (((b1 & 0x3f) << 8) | b0),
                        1 + (((b3 & 0x0f) << 10) | (b2 << 2) | ((b1 & 0xc0) >> 6)))
            if ck == b'VP8X':
                return (1 + (data[24] | (data[25] << 8) | (data[26] << 16)),
                        1 + (data[27] | (data[28] << 8) | (data[29] << 16)))
    except Exception as e:
        log.warning(f"解析图片头失败: {e}")
    return (300, 300)


# ==================== 三种图床上传 (返回 URL 或 None) ====================

async def _up_cos(hosting, event, data, filename):
    r = await hosting.upload_cos(data, filename, user_id=event.user_id)
    if isinstance(r, dict) and r.get('file_url'):
        return r['file_url']
    log.error(f"COS 上传失败: {r}")
    return None


async def _up_bilibili(hosting, event, data, filename):
    r = await hosting.upload_bilibili(data)
    if isinstance(r, str) and r.startswith('http'):
        return r
    log.error(f"B站上传失败: {r}")
    return None


async def _up_qq_channel(hosting, event, data, filename):
    """直接调频道 API 并解析 attachments[0].url (绕过 image_hosting 的错误 URL)"""
    cid = (hosting._cfg.get('qq_channel') or {}).get('channel_id', '')
    bot = _bot_mgr().get_bot(event.appid)
    tm = getattr(bot, 'token_manager', None) if bot else None
    if not (cid and tm):
        log.error(f"QQ频道: channel_id 或 token_manager 缺失 (cid={cid!r}, tm={bool(tm)})")
        return None
    token = await tm.get_token()
    api = getattr(tm, 'api_base', 'https://api.sgroup.qq.com')

    import aiohttp, ssl as _ssl
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    ext = (os.path.splitext(filename)[1].lstrip('.').lower() or 'jpg')
    mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'gif': 'image/gif', 'webp': 'image/webp'}.get(ext, 'image/jpeg')
    form = aiohttp.FormData()
    form.add_field('file_image', data, filename=f'image.{ext}', content_type=mime)
    form.add_field('msg_id', '1')

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(f"{api}/channels/{cid}/messages", data=form,
                              headers={'Authorization': f'QQBot {token}'}, ssl=ctx) as resp:
                status, body = resp.status, await resp.json(content_type=None)
    except Exception as e:
        log.error(f"QQ频道上传异常: {e}")
        return None

    if status != 200:
        log.error(f"QQ频道上传失败 HTTP={status}: {body}")
        return None
    atts = (body or {}).get('attachments') or []
    u = atts[0].get('url') if atts and isinstance(atts[0], dict) else None
    if u:
        return u if u.startswith('http') else 'https://' + u.lstrip('/')
    log.error(f"QQ频道响应未含图片 URL: {body}")
    return None


_UPLOADERS = (('cos', _up_cos), ('bilibili', _up_bilibili), ('qq_channel', _up_qq_channel))


# ==================== 上传调度 ====================

def _bot_mgr():
    try:
        from core.bot.manager import _bot_manager_ref
        return _bot_manager_ref
    except Exception:
        return None


async def _upload(event, data, filename):
    bm = _bot_mgr()
    hosting = bm.module_manager.get('image_hosting') if bm and bm.module_manager else None
    if not hosting:
        log.error("image_hosting 模块未启用")
        return None

    status = hosting.status()
    log.info(f"图床状态: {status}")
    enabled = [n for n, _ in _UPLOADERS if status.get(n)]
    if not enabled:
        log.error("无任何图床启用")
        return None

    for name, fn in _UPLOADERS:
        if not status.get(name):
            continue
        log.info(f"尝试 {name} 上传 ...")
        try:
            url = await fn(hosting, event, data, filename)
        except Exception as e:
            log.error(f"{name} 上传异常: {e}")
            url = None
        if url:
            log.info(f"{name} 成功: {url}")
            return url

    log.error(f"已启用图床全部失败: {enabled}")
    return None


# ==================== 处理器 ====================

@handler(r'^测试图片$', name='测试图文混合markdown', desc='图文混合原生markdown + 本地图片 + @用户')
async def test_markdown_image(event, match):
    path = _pick_random_image()
    if not path:
        return await event.reply(f"❌ 未找到图片: {_IMAGES_DIR}")

    with open(path, 'rb') as f:
        data = f.read()
    filename = os.path.basename(path)
    width, height = _image_size(data)
    log.info(f"选中: {filename} 大小={len(data)}B 尺寸={width}x{height}")

    url = await _upload(event, data, filename)
    if not url:
        return await event.reply(f"<@{event.user_id}>\n❌ 图床上传失败, 详见后台日志")

    md = (
        f"<@{event.user_id}> 这是一张随机本地图片 🖼️\n\n"
        f"**文件名:** `{filename}`\n"
        f"**尺寸:** {width} x {height}\n\n"
        f"![{filename} #{width}px #{height}px]({url})\n\n"
        f"> 图文混合原生 markdown 测试"
    )
    await event.reply(md)
