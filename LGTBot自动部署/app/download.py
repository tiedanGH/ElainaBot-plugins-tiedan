"""/download —— 所有人可用: 把上传目录下某个游戏目录整个打成 zip 发回来。

谁能用、在哪用 (见 handle): 主人哪儿都能用 (私聊也行); 其他人只在面板「生效范围」列出的
群里生效, 其它地方一律静默 —— 与 /upload 一样。每次下载都占整个机器人共享的「每日发送
文件容量」, 所以同一时间只处理一个, 每人两次下载之间至少隔 _COOLDOWN 秒 (主人不受限)。

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
import math
import os
import time
import zipfile

from core.base.logger import PLUGIN, get_logger, report_error

from . import config, deploy, flow, store

log = get_logger(PLUGIN, 'LGTBot自动部署')

MAX_BYTES = 200 * 1024 * 1024        # QQ 官方单个文件上限
# 每人两次下载的最短间隔 (秒)。框架的 @handler(cooldown=...) 并不生效 —— 只存进 handler
# 字典, core 里没人读 —— 只能在这里自己限
_COOLDOWN = 120
_last: dict = {}                     # user_id → 上次受理下载的时刻 (time.monotonic)
# 同一时间只打包 / 上传一个: 同步标记, 判定与置位之间没有 await (理由同 flow._busy)
_busy = False

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


def _is_owner(event) -> bool:
    """与框架 core/plugin/_blacklist.py 的 _is_owner 同源: 按 appid 取 owner_ids 比对。

    框架没有公开这个判断, 别的插件 (LGTBot游戏问答、AI画图) 也是这么照抄的。配置取不到
    就按普通用户处理。"""
    if not getattr(event, 'user_id', ''):
        return False
    try:
        from core.base.config import cfg
        bot_cfg = cfg.get_bot_config(str(getattr(event, 'appid', '') or ''))
    except Exception:  # noqa: BLE001
        return False
    return isinstance(bot_cfg, dict) and event.user_id in (bot_cfg.get('owner_ids') or [])


def _zip_name(name: str) -> str:
    """发出去的 zip 文件名: 目录名能复述才用它, 否则用一个中性的名字。

    文件名会显示在群里的文件卡片上, 同样受「没过审的用户输入不进群」约束 (插件目录
    CLAUDE.md)。经 /upload 落地的目录, 名字在送审正文里, 最近一次落地审核通过
    (flow._reviewed) 才算过了审; force 与面板关了审核的 (verdict=force/skip) 根本没审,
    不用它的名字。没有上传记录的是上游自带或管理员手放的, 不是群友打的字, 照用。
    """
    rec = store.last_game_record(name)
    if rec is None or flow._reviewed(rec):
        return f'{name}.zip'
    return 'lgtbot_game.zip'


def _api_error(event) -> str:
    """event.error (框架记下的上传失败响应) → 一句说明: 错误码 + 官方文案或已知含义。"""
    err = getattr(event, 'error', None)
    if not isinstance(err, dict):
        return '原因不明, 详见框架日志'
    code = str(err.get('code') or err.get('err_code') or '')
    text = _HINTS.get(code) or str(err.get('message') or err.get('msg') or '')[:60]
    return '、'.join(x for x in (f'错误码 {code}' if code else '', text) if x) or '原因不明'


async def handle(event, argline: str) -> bool:
    """``/download <文件夹名>`` 入口 —— 所有人可用。

    主人哪儿都能用 (私聊也行); 其他人只在面板「生效范围」列出的群里生效, 插件关闭或别的
    群 / 私聊一律静默 (只记日志), 与 /upload 一样, 免得把提示刷到无关群。主人碰到这两种
    情况则明确回一句, 免得对着没反应的指令排查。

    回复一律不复述指令参数 (插件目录 CLAUDE.md); 发出去的 zip 的文件名见 _zip_name。
    """
    global _busy
    cfg = config.all_config(refresh=True)
    owner = _is_owner(event)
    if not cfg.get('enabled'):
        if owner:
            await flow._send(event, '❌ 插件已在后台面板关闭, 请先启用')
        return False
    if not owner and not config.is_group_allowed(event.group_id):
        log.info(f'群 {event.group_id or "(私聊)"} 不在允许列表, 忽略 /download')
        return False
    args = (argline or '').split()
    if len(args) != 1:
        await flow._send(event, '❌ 用法: /download <文件夹名>\n'
                                '把 lgtbot 下这个游戏目录打包成 zip 上传压缩包')
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

    # 限流放在参数都校验过之后: 打错名字不该白白吃掉一次冷却。
    # 从判定到置位、建任务之间不能有 await (回完一句就 return 的除外), 否则并发的指令会一起受理
    if _busy:
        await flow._send(event, '⏳ 已有下载任务正在处理, 请稍后再试')
        return False
    uid = str(getattr(event, 'user_id', '') or '')
    now = time.monotonic()
    wait = _COOLDOWN - (now - _last[uid]) if uid in _last and not owner else 0
    if wait > 0:
        await flow._send(event, f'⏳ 下载冷却中, 请 {math.ceil(wait)} 秒后再试')
        return False
    for k in [k for k, t in _last.items() if now - t >= _COOLDOWN]:     # 冷却过了就不必再记
        del _last[k]
    _last[uid] = now
    _busy = True
    try:
        # 打包 + 上传放后台: 大文件分片上传可能超过框架给处理器的 300 秒
        flow._spawn(_run(event, path, name))
    except Exception:
        _busy = False
        raise
    return True


async def _run(event, path: str, name: str):
    global _busy
    try:
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
        sent = await event.reply_file(data, f'📦 共 {count} 个文件, {size}', file_name=_zip_name(name))
        if sent is None:
            await flow._send(event, f'❌ 文件发送失败 ({_api_error(event)})')
            return
        log.info(f'已发送打包的游戏目录 {name} ({count} 个文件, {size})')
    except Exception as e:  # noqa: BLE001 — 兜底: 未预期的异常也要回一句
        report_error(PLUGIN, 'LGTBot自动部署', e, context={'game': name, 'group_id': event.group_id})
        # 异常原文只进日志: 里面可能有服务器路径与文件名
        await flow._send(event, '❌ 打包下载出现异常, 详情见日志')
    finally:
        _busy = False
