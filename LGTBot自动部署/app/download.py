"""/download —— 仅主人: 把上传目录下某个游戏目录整个打成 zip 发回来。

发文件走框架的 ``event.reply_file`` (QQ 官方富媒体接口 file_type=4, 群聊与单聊都可用;
官方上限单个文件 200MB, 另有「每日发送文件容量上限」, 超了报错码 40093002)。5MB 以内
框架一次性 base64 上传, 超过自动走分片上传。

zip 里套一层 ``<目录名>/`` —— 与 /upload 收的压缩包同一个结构, 下载下来改完可以原样
传回去 (deploy.deploy_archive 会把同名顶层目录提上来)。

只打包普通文件: 符号链接一律跳过、目录链接不进入 —— 链接可能指到游戏目录以外, 打进去
就把服务器上别的文件带出去了。zip 在内存里拼, 不在插件目录落盘: 一来不留垃圾, 二来游戏
目录里要是有 .py, 落进插件目录会触发框架热重载 (见 archive._reject_scripts)。
"""

from __future__ import annotations

import asyncio
import io
import os
import zipfile

from core.base.logger import PLUGIN, get_logger

from . import config, deploy, flow

log = get_logger(PLUGIN, 'LGTBot自动部署')

MAX_BYTES = 200 * 1024 * 1024        # QQ 官方单个文件上限

# 发送失败时 event.error 里常见的错误码 (官方文档「上传富媒体文件」的错误码表)
_HINTS = {
    '40093002': '超过今日发送文件容量上限, 明天再试',
    '850031': '文件超过大小限制',
    '850018': '机器人被禁言',
    '40093001': '分片上传通道异常, 稍后重试',
}


class TooLarge(Exception):
    """打包结果超过 MAX_BYTES。"""


def find_game(target: dict, name: str) -> str:
    """名字 → 上传目录下那个游戏目录的 realpath; 名字不合法、越界或不存在都返回空串。"""
    if deploy.bad_name(name) or deploy.check_target(target):
        return ''
    base = os.path.realpath(target['path'])
    path = os.path.realpath(os.path.join(base, name))
    if os.path.dirname(path) != base or not os.path.isdir(path):
        return ''
    return path


def pack(folder: str, root: str, limit: int = MAX_BYTES) -> tuple[bytes, int]:
    """把 folder 下的普通文件打成 zip (成员路径 ``<root>/<相对路径>``), 返回 (字节, 文件数)。

    超过 ``limit`` 就中途抛 TooLarge, 不把整个目录读完再说。
    """
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for d, dirs, files in os.walk(folder):
            dirs[:] = sorted(x for x in dirs if not os.path.islink(os.path.join(d, x)))
            for f in sorted(files):
                p = os.path.join(d, f)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                rel = os.path.relpath(p, folder).replace(os.sep, '/')
                zf.write(p, f'{root}/{rel}')
                count += 1
                if buf.tell() > limit:
                    raise TooLarge()
    data = buf.getvalue()
    if len(data) > limit:
        raise TooLarge()
    return data, count


def _api_error(event) -> str:
    """event.error (框架记下的上传失败响应) → 一句说明: 错误码 + 官方文案或已知含义。"""
    err = getattr(event, 'error', None)
    if not isinstance(err, dict):
        return '原因不明, 详见框架日志'
    code = str(err.get('code') or err.get('err_code') or '')
    text = _HINTS.get(code) or str(err.get('message') or err.get('msg') or '')[:60]
    return '、'.join(x for x in (f'错误码 {code}' if code else '', text) if x) or '原因不明'


async def handle(event, argline: str) -> bool:
    """``/download <文件夹名>`` 入口 (主人身份由 main.py 的 owner_only 把关)。

    回复一律不复述指令参数 (插件目录 CLAUDE.md)。只有发出去的 zip 用目录名作文件名:
    指令只有主人能触发, 而且名字必须对上上传目录里已有的目录 —— 是服务器上的东西, 不是
    随手打进来的字。
    """
    cfg = config.all_config(refresh=True)
    if not cfg.get('enabled'):
        await flow._send(event, '❌ 插件已在后台面板关闭, 请先启用')
        return False
    args = (argline or '').split()
    if len(args) != 1:
        await flow._send(event, '❌ 用法: /download <文件夹名>\n'
                                '把 lgtbot 下这个游戏目录打包成 zip 发回来 (仅主人)')
        return False
    name = args[0]
    name_err = deploy.bad_name(name)
    if name_err:
        await flow._send(event, f'❌ 文件夹{name_err}')
        return False
    target = config.upload_target()
    target_err = deploy.check_target(target)
    if target_err:
        await flow._send(event, f'❌ {target_err}')
        return False
    path = find_game(target, name)
    if not path:
        await flow._send(event, '❌ 上传目录下没有这个游戏目录')
        return False
    # 打包 + 上传放后台: 大文件分片上传可能超过框架给处理器的 300 秒
    flow._spawn(_run(event, path, name))
    return True


async def _run(event, path: str, name: str):
    await flow._send(event, '📦 正在打包, 请稍候…')
    try:
        data, count = await asyncio.to_thread(pack, path, name, MAX_BYTES)
    except TooLarge:
        await flow._send(event, f'❌ 打包后超过 {MAX_BYTES // 1048576} MB (QQ 单个文件上限), 无法发送')
        return
    except OSError as e:
        log.warning(f'打包游戏目录失败 ({path}): {e}')
        await flow._send(event, '❌ 打包失败 (服务器读盘出错), 详情见日志')
        return
    if not count:
        await flow._send(event, '❌ 这个游戏目录是空的')
        return
    size = f'{len(data) / 1048576:.2f} MB'
    sent = await event.reply_file(data, f'📦 共 {count} 个文件, {size}', file_name=f'{name}.zip')
    if sent is None:
        await flow._send(event, f'❌ 文件发送失败 ({_api_error(event)})')
        return
    log.info(f'已发送打包的游戏目录 {name} ({count} 个文件, {size})')
