"""GitHub 链接解析: 群消息含 GitHub 链接时, 发送其 OpenGraph 预览图

实现说明:
  1. 不抓 github.com HTML (国内直连超时), 而是直接构造 GitHub OpenGraph 图片 URL:
        https://opengraph.githubassets.com/<hash>/<owner>/<repo>...
     <hash> 仅作 CDN 缓存键, 传任意值即可 (这里用 path 的 md5)。
  2. bot 下载该 og 图 bytes → 通过 image_hosting 模块上传到腾讯云 COS。
  3. 用 markdown 发送 COS 图床链接 (而非直接嵌 opengraph URL):
     opengraph.githubassets.com 未在 QQ 白名单, 直发不显示; COS 自定义域名可控。

依赖:
  · image_hosting 模块需启用 COS 图床 (modules/image_hosting/data/config.yaml: cos.enabled=true)
  · bot 需能访问 opengraph.githubassets.com (Fastly CDN, 通常比 github.com 通畅)
"""

import re
import hashlib
import aiohttp

from core.plugin.decorators import handler
from core.base.logger import get_logger, PLUGIN

log = get_logger(PLUGIN, "GitHub解析")


# URL 主体排除空白/尖括号/引号/中文及中文标点, 防止把后续中文吃进 URL
_URL_BODY = r"[^\s<>\"'，。！？；：、）（【】「」]*"
# 匹配 github.com / www.github.com, 捕获 owner/repo... 路径部分
_GITHUB_URL = re.compile(
    rf"https?://(?:www\.)?github\.com/({_URL_BODY})",
    re.IGNORECASE,
)

_OG_BASE = "https://opengraph.githubassets.com"
_TRAILING = '.,;:!?)）】」』、/'
_OG_W, _OG_H = 1200, 600  # GitHub OG 卡片图固定 1200×600

_DL_TIMEOUT_S = 15.0
_DL_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ElainaBot/1.0; +github-preview)'}
_MAX_IMG = 10 * 1024 * 1024  # og 图一般 < 100KB, 10MB 上限足够防御


def _build_og_url(path):
    """github.com 路径 → opengraph 图片 URL; 路径为空返回 None"""
    path = path.split('?')[0].split('#')[0]
    path = path.strip().rstrip(_TRAILING).strip('/')
    if not path:
        return None
    h = hashlib.md5(path.encode('utf-8')).hexdigest()
    return path, f"{_OG_BASE}/{h}/{path}"


def _get_hosting():
    """从 BotManager 取 image_hosting 模块, 未启用返回 None"""
    try:
        from core.bot.manager import _bot_manager_ref
        bm = _bot_manager_ref
        if bm is None or bm.module_manager is None:
            return None
        return bm.module_manager.get('image_hosting')
    except Exception:
        return None


async def _download(url):
    """下载 og 图 bytes; 失败返回 None"""
    try:
        timeout = aiohttp.ClientTimeout(total=_DL_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout, headers=_DL_HEADERS) as s:
            async with s.get(url) as resp:
                if resp.status != 200:
                    log.warning(f"og 图下载非 200: {resp.status} ({url})")
                    return None
                # 预检 Content-Length 防超大文件 OOM
                clen = resp.headers.get('Content-Length')
                if clen and clen.isdigit() and int(clen) > _MAX_IMG:
                    log.warning(f"og 图 Content-Length {clen} 超上限, 丢弃")
                    return None
                # 必须用 resp.read() 读完整 body 到 EOF;
                # resp.content.read(n) 只读到内部缓冲块边界, 会截断成半张图
                data = await resp.read()
                if not data:
                    log.warning(f"og 图为空 ({url})")
                    return None
                if len(data) > _MAX_IMG:
                    log.warning(f"og 图超过 {_MAX_IMG} 字节上限, 丢弃")
                    return None
                log.info(f"og 图下载完成: {len(data)} 字节")
                return data
    except Exception as e:
        log.warning(f"og 图下载异常 {type(e).__name__}: {e} ({url})")
        return None


@handler(_GITHUB_URL.pattern,
         name='GitHub链接解析',
         desc='群消息含 GitHub 链接时上传 COS 后发送预览图',
         ignore_at_check=True)
async def github_preview(event, match):
    built = _build_og_url(match.group(1))
    if not built:
        return
    path, og_url = built
    log.info(f"检测到 GitHub 链接 → og 图: {og_url}  (event_type={event.event_type})")

    # 1. 下载 opengraph 图
    img = await _download(og_url)
    if not img:
        log.info(f"og 图获取失败, 跳过: {path}")
        return

    # 2. 上传到 COS 图床
    hosting = _get_hosting()
    if not hosting:
        log.warning("image_hosting 模块未启用, 无法上传 COS")
        return
    filename = path.replace('/', '_') + '.png'
    result = await hosting.upload_cos_url(img, filename, user_id=event.user_id or None)
    if isinstance(result, tuple):  # (False, 原因)
        log.warning(f"COS 上传失败: {result[1]}")
        return
    cos_url = result
    log.info(f"COS 上传成功: {cos_url}")

    # 3. 用 markdown 发送 COS 链接 (COS 域名在白名单内, 可显示)
    try:
        await event.reply(f"![GitHub #{_OG_W}px #{_OG_H}px]({cos_url})")
        log.info(f"预览图已发送: {path}")
    except Exception as e:
        log.warning(f"发送失败 {type(e).__name__}: {e}")
