"""AI 画图：任何人可用的「画图 XXX」指令，走中央 AI LLM 生图并留存历史记录。"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time

from core.base.config import cfg
from core.base.logger import PLUGIN, get_logger, report_error
from core.plugin.decorators import handler, on_load, on_unload
from core.plugin.web_pages import register_page, unregister_page

from .app import capability, central, config, fanout, hosting, limiter, media, store, webpanel

__plugin_meta__ = {
    'name': 'AI 画图',
    'author': '铁蛋',
    'description': '所有人可用的「画图 XXX」指令，调用中央 AI LLM 生图，并提供配置与历史记录面板',
    'version': '1.7.0',
    'license': 'MIT',
}

log = get_logger(PLUGIN, 'AI画图')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
PAGE_KEY = 'ai-draw'
_capability_task: asyncio.Task | None = None
MESSAGE_EVENTS = [
    'GROUP_AT_MESSAGE_CREATE',
    'GROUP_MESSAGE_CREATE',
    'C2C_MESSAGE_CREATE',
    'DIRECT_MESSAGE_CREATE',
    'AT_MESSAGE_CREATE',
    'MESSAGE_CREATE',
]
# QQ 被动回复：每条用户消息群聊（频道同算）最多回 5 次，私聊 4 次
_REPLY_QUOTA_GROUP = 5
_REPLY_QUOTA_DIRECT = 4
_NO_REPLY_LEFT = '这条消息的被动回复次数已用完（群聊 5 次、私聊 4 次），图没能发出'
_TIME_UP = (
    f'从开始审核算起已过 {fanout.DEADLINE_SECONDS} 秒，放弃出图（QQ 被动回复 5 分钟后失效）'
)

_ICON = (
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="3" y="3" width="18" height="18" rx="2"/>'
    '<circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>'
)


@on_load
async def initialize() -> None:
    await asyncio.to_thread(config.init, DATA_DIR)
    await asyncio.to_thread(store.connect, DATA_DIR)
    limiter.reset()
    webpanel.register_routes()
    register_page(
        key=PAGE_KEY,
        label='AI 画图',
        source='plugin',
        source_name='AI画图',
        icon=_ICON,
        html_file=os.path.join(BASE_DIR, 'panel.html'),
    )
    global _capability_task
    if _capability_task is None or _capability_task.done():
        _capability_task = asyncio.create_task(_watch_capability())
    log.info('AI 画图插件已加载')


@on_unload
async def cleanup() -> None:
    global _capability_task
    if _capability_task is not None:
        _capability_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _capability_task
        _capability_task = None
    capability.unregister()
    # 对外调用的会话可能还在画：先收尾留档，再关库
    await capability.shutdown()
    unregister_page(PAGE_KEY)
    limiter.reset()
    await asyncio.to_thread(store.close)


async def _watch_capability() -> None:
    """中央模块重载会清空在线能力，这里按开关持续补注册。"""
    while True:
        try:
            capability.sync(config.load().get('capability_enabled', False))
        except Exception:  # noqa: BLE001 - 看护任务不能被单次失败带崩
            log.debug('画图能力注册重试失败', exc_info=True)
        await asyncio.sleep(5)


def _is_owner(event) -> bool:
    """读取机器人配置判断主人身份；配置不可用时按普通用户处理。"""
    try:
        bot_config = cfg.get_bot_config(str(getattr(event, 'appid', '') or ''))
    except Exception:  # noqa: BLE001 - 配置异常不应中断画图请求
        return False
    owner_ids = {
        str(item) for item in (bot_config.get('owner_ids') or [])
    } if isinstance(bot_config, dict) else set()
    return bool(owner_ids) and str(event.user_id) in owner_ids


def _scene_enabled(event, current: dict) -> bool:
    if getattr(event, 'is_group', False):
        return bool(current['group_enabled'])
    if getattr(event, 'is_direct', False):
        return bool(current['direct_enabled'])
    if getattr(event, 'is_channel', False):
        return bool(current['channel_enabled'])
    return False


def _mention(event, current: dict) -> str:
    """群聊与频道里生成 @发起人；私聊没有提及语义，返回空串。"""
    if not current.get('mention_user'):
        return ''
    if getattr(event, 'is_group', False) or getattr(event, 'is_channel', False):
        return f'<@{event.user_id}>'
    return ''


async def _reply_text(event, text: str, current: dict | None = None) -> None:
    mention = _mention(event, current if current is not None else config.load())
    await event.reply(f'{mention} {text}'.strip() if mention else text)


def _brief(text: str, limit: int = 60) -> str:
    """日志用的描述摘要：压成单行并截断，避免长提示词刷屏。"""
    value = ' '.join(str(text or '').split())
    return value if len(value) <= limit else value[:limit] + '…'


def _find_blocked(text: str, words: list[str]) -> str:
    value = str(text or '').casefold()
    return next((word for word in words if word and word.casefold() in value), '')


def _fill(template: str, **values) -> str:
    """按占位符渲染面板文案；模板写错时原样返回，不让回复流程失败。"""
    try:
        return str(template).format(**values)
    except (KeyError, IndexError, ValueError):
        return str(template)


def _caption(current: dict, event, prompt: str, result: dict) -> str:
    """渲染图片说明正文，不含 @提及。"""
    template = str(current.get('caption_template') or '').strip()
    if not template:
        return ''
    return _fill(
        template,
        prompt=prompt,
        model=result.get('model', ''),
        provider=result.get('provider', ''),
        size=current.get('image_size', ''),
        user=str(getattr(event, 'username', '') or event.user_id),
    )[:300]


def _media_content(event, current: dict, caption: str) -> str:
    """富媒体消息的附带文字；QQ 不会在 msg_type=7 中解析 <@openid>。"""
    mention = _mention(event, current)
    return f'{mention} {caption}'.strip() if mention else caption


def _markdown_text(event, current: dict, caption: str, url: str, size: tuple[int, int]) -> str:
    """@发起人开头的 Markdown 图片消息。"""
    width, height = size
    head = ' '.join(item for item in (_mention(event, current), caption) if item)
    image = f"![{current['markdown_alt']} #{width}px #{height}px]({url})"
    return f'{head}\n{image}' if head else image


def _markdown_many(event, current: dict, prompt: str, shots: list, note: str = '') -> str:
    """多张图拼成一条 Markdown：@发起人开头，图与图之间空一行，末尾可附进度说明。"""
    blocks = []
    for shot in shots:
        width, height = media.dimensions(shot.image, (1024, 1024))
        image = f"![{current['markdown_alt']} #{width}px #{height}px]({shot.hosted_url})"
        caption = _caption(current, event, prompt, shot.result or {})
        blocks.append(f'{caption}\n{image}' if caption else image)
    body = '\n\n'.join(blocks)
    mention = _mention(event, current)
    text = f'{mention}\n{body}' if mention else body
    return f'{text}\n\n{note}' if note else text


def _delivered(response) -> bool:
    """平台失败时也会返回响应体，只有带消息 ID 才算真正发出去。"""
    return isinstance(response, dict) and bool(response.get('id'))


class _Replies:
    """这条消息还能被动回复几次。「正在作画」提示也算一次；多图靠它决定剩下的图要不要攒成一条发。"""

    def __init__(self, event):
        direct = getattr(event, 'is_direct', False)
        self.left = _REPLY_QUOTA_DIRECT if direct else _REPLY_QUOTA_GROUP

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _base_record(event, prompt: str) -> dict:
    return {
        'created_at': time.time(),
        'appid': str(getattr(event, 'appid', '') or ''),
        'user_id': str(event.user_id),
        'username': str(getattr(event, 'username', '') or ''),
        'chat_type': str(getattr(event, 'chat_type', '') or ''),
        'chat_id': str(getattr(event, 'chat_id', '') or ''),
        'source': 'command',
        'status': 'failed',
        'prompt': prompt,
    }


async def _persist(record: dict, current: dict, image: bytes = b'') -> int:
    record_id = await asyncio.to_thread(store.add, record, current['history_limit'])
    if image and current.get('history_save_images'):
        extension = media.sniff(image)[0]
        await asyncio.to_thread(
            store.save_image, record_id, image, extension, current['history_image_limit'],
        )
    return record_id


async def _image_bytes(result: dict) -> bytes:
    """拿到图片字节：接口直接返回则用它，否则下载接口给的公网 URL。"""
    if result.get('data'):
        return result['data']
    return await media.download(result.get('url', '')) or b''


async def _deliver(event, current: dict, caption: str, result: dict, image: bytes) -> dict:
    """优先图床直链 + Markdown 图片消息，失败时退回富媒体图片消息。

    `retryable` 表示「这条线路给的图有问题，换一条可能就好了」：图片下不下来、
    图床收不下、平台拉不动这张图，都属于这类。唯独「图床上传成功、Markdown 却被
    平台拒收」不算 —— 那是图床域名没报备，换线路还是同一个域名，重试纯属浪费额度。
    """
    outcome = {
        'send_mode': '', 'hosted_url': '', 'delivered': False,
        'error': '', 'retryable': False,
    }
    if current['markdown_send'] and image:
        url = await hosting.upload(
            image, hosting.file_name(image), appid=str(getattr(event, 'appid', '') or ''),
        )
        outcome['hosted_url'] = url
        if url:
            text = _markdown_text(
                event, current, caption, url, media.dimensions(image, (1024, 1024)),
            )
            response = await event.reply(
                text,
                msg_type=2,
                skip_suffix=True,
                force_verify_image_resource=bool(current['force_verify_image']),
            )
            outcome['send_mode'] = 'markdown'
            outcome['delivered'] = _delivered(response)
            if outcome['delivered']:
                return outcome
            outcome['error'] = 'Markdown 图片消息发送失败，请确认图床域名已在 QQ 开放平台报备'
        else:
            outcome['error'] = hosting.state()['message']
            outcome['retryable'] = True
        if not current['media_fallback']:
            return outcome

    payload = image or result.get('url', '')
    if not payload:
        outcome['error'] = '生图接口返回的图片既下载不到也没有可用链接'
        outcome['retryable'] = True
        return outcome
    response = await event.reply_image(payload, _media_content(event, current, caption))
    outcome['send_mode'] = 'media'
    outcome['delivered'] = _delivered(response)
    if outcome['delivered']:
        outcome['error'] = ''
        outcome['retryable'] = False
    else:
        outcome['error'] = outcome['error'] or '图片发送失败，请检查图片格式或平台限制'
        outcome['retryable'] = True
    return outcome


async def _generate_gated(current: dict, final_prompt: str, tried: set) -> dict:
    async with limiter.semaphore(current['max_concurrency']):
        return await central.generate(current, final_prompt, exclude=tried)


async def _generate_and_deliver(event, current: dict, prompt: str, final_prompt: str,
                                record: dict, *, deadline: float = 0.0) -> bytes:
    """按线路依次生图并投递；生成成功却没送达时换下一条线路重试。

    `deadline`（monotonic）一到就不再发起新的出图，正在画的也会被掐断：
    过了那个点 QQ 被动回复已经发不出去了。
    """
    tried: set[tuple[str, str]] = set()
    routes = len(central.valid_routes(current))
    attempts = max(1, min(int(current['delivery_max_attempts']), routes or 1))
    notes: list[str] = []
    image = b''
    for index in range(attempts):
        started = time.perf_counter()
        remaining = deadline - time.monotonic() if deadline else 0.0
        if deadline and remaining <= 1:
            log.warning('已到 %s 秒上限，不再换线路重试', fanout.DEADLINE_SECONDS)
            notes.append(_TIME_UP)
            break
        log.info('开始生图 第 %s/%s 次 尺寸=%s 可用线路=%s',
                 index + 1, attempts, current['image_size'], routes)
        try:
            call = _generate_gated(current, final_prompt, tried)
            result = await (asyncio.wait_for(call, remaining) if deadline else call)
        except Exception as error:  # noqa: BLE001 - 线路耗尽时保留前几次的失败原因
            if isinstance(error, TimeoutError):
                # central.generate 自己会把各线路的超时包成 RuntimeError，漏出来的只能是总时限
                error = RuntimeError(_TIME_UP)
            log.warning('生图请求失败 第 %s 次: %s', index + 1, error)
            if not notes:
                raise error
            notes.append(str(error)[:300])
            break
        elapsed = round((time.perf_counter() - started) * 1000)
        tried.add((result['provider_id'], result['model']))
        log.info('生图完成 线路=%s/%s 用时=%sms 返回=%s',
                 result['provider'] or result['provider_id'], result['model'], elapsed,
                 '图片数据' if result['data'] else (result['url'] or '空'))
        record.update({
            'status': 'success',
            'provider': result['provider'],
            'provider_id': result['provider_id'],
            'model': result['model'],
            'size': current['image_size'],
            'image_url': result['url'],
        })
        image = await _image_bytes(result)
        try:
            outcome = await _deliver(
                event, current, _caption(current, event, prompt, result), result, image,
            )
        except Exception as error:  # noqa: BLE001 - 发送炸了也按未送达处理，换线路再试
            report_error(PLUGIN, 'AI画图', error)
            outcome = {
                'send_mode': '', 'hosted_url': '', 'delivered': False,
                'error': f'图片发送异常：{error}'[:300], 'retryable': True,
            }
        record.update({
            'delivered': outcome['delivered'],
            'send_mode': outcome['send_mode'],
            'hosted_url': outcome['hosted_url'],
            'error': outcome['error'],
        })
        if outcome['delivered']:
            log.info('图片已送达 方式=%s 线路=%s/%s%s',
                     outcome['send_mode'], result['provider'] or result['provider_id'],
                     result['model'], f'（第 {index + 1} 次尝试）' if notes else '')
            if notes:
                record['error'] = '已改用备用线路；先前失败：' + '；'.join(notes)[:800]
            return image
        notes.append(f"{result['provider'] or result['provider_id']}/{result['model']}: "
                     f"{outcome['error']}")
        if not outcome['retryable'] or index + 1 >= attempts:
            log.warning('画图未送达且不再重试（%s）: %s',
                        '已达重试上限' if outcome['retryable'] else '换线路也无济于事', notes[-1])
            break
        log.warning('生成成功但未送达，改用下一条线路重试: %s', notes[-1])
    record['error'] = '；'.join(notes)[:1000]
    return image


class _MultiDraw:
    """一次多图请求：勾了「多图」的线路同时开画。

    时间都从开始审核算起：全部画完或到「多图汇总等待」就把画好的拼成一条发出去，没画完的
    接着画；290 秒内画好的补发，过点的放弃。被动回复只剩一次时不再逐张补发，攒到最后一起发。
    多图线路全部失败时，再按顺序试一遍没勾「多图」的线路兜底。
    """

    def __init__(self, event, current: dict, prompt: str, record: dict, started: float,
                 replies: _Replies):
        self.event = event
        self.current = current
        self.prompt = prompt
        self.base = record
        self.started = started
        self.replies = replies
        self.deadline = started + fanout.DEADLINE_SECONDS
        self.gather_at = min(self.deadline, started + current['multi_wait_seconds'])
        upload = bool(current['markdown_send']) and hosting.available()
        self.batch = fanout.Batch(
            current, record['final_prompt'], upload=upload,
            accept_unhosted=not upload or bool(current['media_fallback']),
            appid=record['appid'],
        )
        self.backups = [
            route for route in central.valid_routes(current) if not route.get('multi')
        ]
        self.held: list[fanout.Shot] = []
        self.delivered = 0
        self.fallback = False

    async def run(self, routes: list[dict]) -> None:
        # 一次多图请求只占一个并发名额：按张占的话，名额一满几条线路又变回排队挨个画
        semaphore = limiter.semaphore(self.current['max_concurrency'])
        try:
            async with asyncio.timeout(max(0.1, self.deadline - time.monotonic())):
                await semaphore.acquire()
        except TimeoutError:
            log.warning('多图请求排队等并发名额超时 user=%s', self.base['user_id'])
            record = dict(self.base, error='排队等待画图名额超时',
                          duration_ms=round((time.monotonic() - self.started) * 1000))
            await _persist(record, self.current)
            await self._fail_notice()
            return
        try:
            await self._drive(routes)
        finally:
            if self.batch.pending:
                # 只有被取消（插件重载、处理器超时）才会走到这里，别留下没人管的任务
                await self.batch.abandon('请求被中断，未完成的出图已放弃')
            for shot in self.batch.shots:
                fanout.save(shot, self.base, self.current, self.started, self.batch.id)
            semaphore.release()

    async def _drive(self, routes: list[dict]) -> None:
        log.info('多图模式 同时请求 %s 条线路：%s 汇总等待=%ss',
                 len(routes), '、'.join(central.route_label(route) for route in routes),
                 self.current['multi_wait_seconds'])
        for route in routes:
            self.batch.add([route])
        gathered = False
        while True:
            shot = await self.batch.next(self.deadline if gathered else self.gather_at)
            if shot is not None:
                await self._arrived(shot)
                # 补发：发完还给没画完的留得出一次回复才发，否则先攒着
                if gathered and self.held and (
                    not self.batch.pending or self.replies.left - self._slots() >= 1
                ):
                    await self._flush()
                continue
            if gathered:
                break
            gathered = True
            pending = len(self.batch.pending)
            log.info('多图汇总 已画好 %s 张 还在画 %s 张 用时=%.1fs',
                     len(self.held), pending, time.monotonic() - self.started)
            if self.held:
                await self._flush(pending)
            elif pending and self.replies.left >= 2:
                # 一张都还没好也先回一句，不让用户干等；至少给补发留一次回复
                self.replies.take()
                await _reply_text(
                    self.event, _fill(self.current['multi_progress_text'], pending=pending),
                    self.current,
                )
            if not pending:
                break
        dropped = await self.batch.abandon()
        if dropped:
            log.warning('多图到 %s 秒上限仍有 %s 路没画完，已放弃：%s',
                        fanout.DEADLINE_SECONDS, len(dropped),
                        '、'.join(shot.label for shot in dropped))
            for shot in dropped:
                await self._save(shot)
        if self.held:
            await self._flush()
        if not self.delivered:
            log.warning('多图请求结束但一张都没送达 user=%s 总耗时=%.1fs',
                        self.base['user_id'], time.monotonic() - self.started)
            await self._fail_notice()

    async def _arrived(self, shot: fanout.Shot) -> None:
        if shot.ok:
            self.held.append(shot)
            return
        log.warning('多图 一路没出图：%s', '；'.join(shot.notes)[:300])
        await self._save(shot)
        if self.fallback or not self.backups or self.batch.pending:
            return
        if any(item.ok for item in self.batch.shots):
            return
        if self.deadline - time.monotonic() < fanout.FALLBACK_MIN_SECONDS:
            return
        self.fallback = True
        log.info('多图线路全部失败，改按顺序尝试备用线路：%s',
                 '、'.join(central.route_label(route) for route in self.backups))
        self.batch.add(self.backups)

    def _slots(self) -> int:
        """把攒着的图发出去要占几次回复：有直链的合成一条 Markdown，其余一张一条。"""
        hosted = any(shot.hosted_url for shot in self.held)
        return int(hosted) + sum(1 for shot in self.held if not shot.hosted_url)

    async def _flush(self, pending: int = 0) -> None:
        """把攒着的图发出去：有图床直链的拼成一条 Markdown，其余逐张富媒体。"""
        shots, self.held = self.held, []
        note = _fill(self.current['multi_progress_text'], pending=pending) if pending else ''
        hosted = [shot for shot in shots if shot.hosted_url]
        loose = [shot for shot in shots if not shot.hosted_url]
        if hosted and not self.replies.take():
            for shot in hosted:
                shot.error = _NO_REPLY_LEFT
        elif hosted:
            response = await self._send(self.event.reply(
                _markdown_many(self.event, self.current, self.prompt, hosted, note),
                msg_type=2,
                skip_suffix=True,
                force_verify_image_resource=bool(self.current['force_verify_image']),
            ))
            ok = _delivered(response)
            for shot in hosted:
                shot.send_mode = 'markdown'
                shot.delivered = ok
                if not ok:
                    shot.error = 'Markdown 图片消息发送失败，请确认图床域名已在 QQ 开放平台报备'
            if ok:
                note = ''
            elif self.current['media_fallback']:
                loose = hosted + loose
        for index, shot in enumerate(loose):
            if not self.replies.take():
                shot.error = _NO_REPLY_LEFT
                continue
            content = _media_content(
                self.event, self.current,
                _caption(self.current, self.event, self.prompt, shot.result or {}),
            )
            if note and index == len(loose) - 1:
                content = f'{content}\n{note}'.strip()
            response = await self._send(self.event.reply_image(
                shot.image or (shot.result or {}).get('url', ''), content,
            ))
            shot.send_mode = 'media'
            shot.delivered = _delivered(response)
            if shot.delivered:
                shot.error = ''
            elif not shot.error:
                shot.error = '图片发送失败，请检查图片格式或平台限制'
        sent = [shot for shot in shots if shot.delivered]
        self.delivered += len(sent)
        if sent:
            log.info('多图已送达 %s 张：%s', len(sent), '、'.join(shot.label for shot in sent))
        for shot in shots:
            await self._save(shot)

    @staticmethod
    async def _send(call):
        try:
            return await call
        except Exception as error:  # noqa: BLE001 - 一条发炸了按未送达处理，不拖垮其余的图
            report_error(PLUGIN, 'AI画图', error)
            return None

    async def _save(self, shot: fanout.Shot) -> None:
        await asyncio.to_thread(
            fanout.save, shot, self.base, self.current, self.started, self.batch.id,
        )

    async def _fail_notice(self) -> None:
        if self.replies.take():
            await _reply_text(self.event, self.current['failure_message'], self.current)
        else:
            log.warning('被动回复次数已用完，失败提示没能发出 user=%s', self.base['user_id'])


@handler(
    r'^/?(?:画图|绘图|AI画图|ai画图)\s*$',
    name='AI 画图帮助',
    desc='查看 AI 画图用法',
    priority=40,
    event_types=MESSAGE_EVENTS,
    ignore_at_check=True,
    block=True,
)
async def draw_help(event, _match) -> None:
    if not config.initialized():
        return
    current = config.load()
    if not current['enabled']:
        return
    routes = central.valid_routes(current)
    multi = central.multi_routes(current)
    await _reply_text(
        event,
        '\n【AI 画图】\n'
        f"发送「画图 <画面描述>」即可生成图片（最多 {current['input_max_length']} 字）\n"
        '例：画图 雪山下的湖泊，黄昏，写实摄影\n'
        f"当前尺寸：{current['image_size']}\n"
        f"可用线路：{len(routes)} 条"
        + (f"（多图：{len(multi)} 条同时出图）" if len(multi) >= 2 else '') + '\n'
        f"提示词优化：{'已开启' if current['prompt_optimize_enabled'] else '已关闭'}\n"
        f"发送方式：{'Markdown 图片' if current['markdown_send'] and hosting.available() else '富媒体图片'}\n"
        f"{central.status()['message']}",
        current,
    )


@handler(
    r'^/?(?:画图|绘图|AI画图|ai画图)\s*(\S[\s\S]*)$',
    name='AI 画图',
    desc='画图 <描述>：调用中央 AI LLM 生成图片',
    priority=30,
    event_types=MESSAGE_EVENTS,
    ignore_at_check=True,
    block=True,
)
async def draw_command(event, match) -> None:
    if not config.initialized():
        return
    current = config.load()
    if not current['enabled'] or not _scene_enabled(event, current):
        return
    if getattr(event, 'is_bot', False):
        return
    prompt = str(match.group(1) or '').strip()
    if not prompt:
        return
    limit = current['input_max_length']
    if len(prompt) > limit:
        await _reply_text(event, _fill(current['input_too_long_response'], limit=limit), current)
        return

    record = _base_record(event, prompt)
    log.info(
        '收到画图请求 user=%s 场景=%s 描述=%s',
        record['user_id'], record['chat_type'] or '未知', _brief(prompt),
    )
    blocked = _find_blocked(prompt, current['blocked_words'])
    if blocked:
        record.update({'status': 'blocked', 'error': f'命中违规词：{blocked}'})
        log.warning('画图描述命中违规词 user=%s 违规词=%s', record['user_id'], blocked)
        await _persist(record, current)
        await _reply_text(event, current['blocked_response'], current)
        return

    owner = _is_owner(event)
    reason = await asyncio.to_thread(
        limiter.check, current,
        appid=record['appid'], user_id=record['user_id'],
        chat_id=record['chat_id'], is_owner=owner,
    )
    if reason:
        # 限流只是拦下重复请求，不构成一次画图，不写入运行日志。
        log.info('画图请求被限流 user=%s 原因=%s', record['user_id'], reason)
        await _reply_text(event, _fill(current['limited_response'], detail=reason), current)
        return

    if not central.available():
        limiter.release(record['appid'], record['user_id'], record['chat_id'])
        record.update({'status': 'failed', 'error': central.status()['message']})
        log.warning('中央 AI LLM 不可用，画图请求已放弃: %s', central.status()['message'])
        await _persist(record, current)
        await _reply_text(event, central.status()['message'], current)
        return

    # 290 秒总时限也从这里算起：之后的审核、改写、出图、补发都得在 QQ 被动回复失效前做完
    started = time.monotonic()
    review = await central.moderate(current, prompt)
    if review.get('flagged') or (
        not review.get('available')
        and current['moderation_enabled']
        and current['moderation_fail_closed']
    ):
        record.update({
            'status': 'blocked',
            'error': review.get('error') or 'AI 内容审核判定为违规',
            'duration_ms': round((time.monotonic() - started) * 1000),
        })
        log.warning(
            '画图描述未通过内容审核 user=%s 原因=%s',
            record['user_id'], review.get('error') or '判定为违规',
        )
        await _persist(record, current)
        await _reply_text(event, current['blocked_response'], current)
        return

    replies = _Replies(event)
    if current['notice_enabled']:
        replies.take()
        await _reply_text(event, current['notice_text'], current)
    image = b''
    try:
        optimized = await central.optimize_prompt(current, prompt)
        final_prompt = central.build_prompt(current, optimized)
        record['final_prompt'] = final_prompt
        if current['prompt_optimize_enabled']:
            log.debug('提示词已改写 user=%s -> %s', record['user_id'], _brief(final_prompt, 120))
        lanes = central.multi_routes(current)
        if len(lanes) >= 2:
            # 今天的额度只剩几张就只画几张；不足两张时退回单图
            left = await asyncio.to_thread(
                limiter.quota_left, current, user_id=record['user_id'], is_owner=owner,
            )
            lanes = lanes if left is None else lanes[:left]
        if len(lanes) >= 2:
            # 多图自己逐张留档、自己回复，不走下面的单条记录
            await _MultiDraw(event, current, prompt, record, started, replies).run(lanes)
            return
        image = await _generate_and_deliver(
            event, current, prompt, final_prompt, record,
            deadline=started + fanout.DEADLINE_SECONDS,
        )
    except Exception as error:  # noqa: BLE001 - 统一记录并回复友好文案
        record.update({
            'status': record.get('status', 'failed'),
            'error': str(error)[:1000],
        })
        if record['status'] != 'success':
            log.warning('AI 画图失败: %s', error)
        else:
            report_error(PLUGIN, 'AI画图', error)
    record['duration_ms'] = round((time.monotonic() - started) * 1000)
    await _persist(record, current, image if current['history_save_images'] else b'')
    if not record.get('delivered'):
        # 具体原因上面已经逐条打过，这里只补一句结论。
        log.warning('画图请求结束但未出图 user=%s 总耗时=%sms',
                    record['user_id'], record['duration_ms'])
        await _reply_text(event, current['failure_message'], current)
