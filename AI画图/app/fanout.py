"""多图模式的并发调度：勾了「多图」的线路同时开画，谁先画好先交谁。

这里只管每一路的「出图 → 取到字节 → 转存图床」，什么时候汇总、要不要补发、到点怎么处置
都留给调用方 —— 聊天指令要算被动回复次数，对外工具要按调用方给的等待时长交卷。
"""
from __future__ import annotations

import asyncio
import time
import uuid

from core.base.logger import PLUGIN, get_logger

from . import central, hosting, media, store

log = get_logger(PLUGIN, 'AI画图')
# QQ 被动回复在用户发消息 5 分钟后失效，框架也会在 300 秒时掐断处理器；
# 从开始审核算起最多留 290 秒，最后一批图还来得及发出去。
DEADLINE_SECONDS = 290
# 多图线路全部失败后改走备用线路，至少得剩这么多秒才值得一试。
FALLBACK_MIN_SECONDS = 20


class Shot:
    """一路出图。多图线路一路一条；备用兜底是一路里按顺序故障转移的几条线路。"""

    def __init__(self, routes: list[dict]):
        self.routes = list(routes)
        self.route = self.routes[0]
        self.result: dict | None = None
        self.image = b''
        self.hosted_url = ''
        self.notes: list[str] = []
        self.ok = False
        self.done = False
        self.timed_out = False
        self.finished_at = 0.0
        # 以下由调用方维护：送没送到、怎么送的、为什么没送到、有没有落库
        self.delivered = False
        self.send_mode = ''
        self.error = ''
        self.recorded = False
        self.record_id = 0

    @property
    def reason(self) -> str:
        """这一路为什么没成，不带线路前缀（单条线路时前缀就是它自己，调用方另有字段）。"""
        if len(self.routes) == 1:
            prefix = f'{central.route_label(self.route)}: '
            return '；'.join(
                note[len(prefix):] if note.startswith(prefix) else note for note in self.notes
            )
        return '；'.join(self.notes)

    @property
    def label(self) -> str:
        result = self.result or {}
        if result:
            return f"{result.get('provider') or result.get('provider_id', '')}/{result.get('model', '')}"
        return central.route_label(self.route)


class Batch:
    """同一次请求的所有出图任务。`upload` / `accept_unhosted` 可在运行中改：
    对外工具交卷以后，晚到的图只留档，不必再占图床额度。
    """

    def __init__(self, config: dict, prompt: str, *, upload: bool, accept_unhosted: bool,
                 appid: str = ''):
        self.id = uuid.uuid4().hex[:12]
        self.config = config
        self.prompt = prompt
        self.upload = upload
        self.accept_unhosted = accept_unhosted
        self.appid = appid
        self.shots: list[Shot] = []
        self._tasks: list[asyncio.Task] = []
        self._finished: asyncio.Queue = asyncio.Queue()
        self._abandon_reason = ''

    def add(self, routes: list[dict]) -> Shot:
        shot = Shot(routes)
        self.shots.append(shot)
        self._tasks.append(asyncio.create_task(self._run(shot)))
        return shot

    @property
    def pending(self) -> list[Shot]:
        return [shot for shot in self.shots if not shot.done]

    async def next(self, until: float) -> Shot | None:
        """等下一路结束（成败都算）；到 `until`（monotonic）还没有，或已经没有在画的，返回 None。"""
        if self._finished.empty():
            remaining = until - time.monotonic()
            if not self.pending or remaining <= 0:
                return None
            try:
                return await asyncio.wait_for(self._finished.get(), remaining)
            except asyncio.TimeoutError:
                return None
        return self._finished.get_nowait()

    async def abandon(self, reason: str = '') -> list[Shot]:
        """到点还没画完的一律放弃，返回被放弃的那几路。"""
        dropped = self.pending
        self._abandon_reason = reason or f'超过 {DEADLINE_SECONDS} 秒仍未完成，已放弃'
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        return dropped

    async def _run(self, shot: Shot) -> None:
        try:
            for route in shot.routes:
                shot.route = route
                try:
                    if await self._attempt(shot, route):
                        shot.ok = True
                        break
                except Exception as error:  # noqa: BLE001 - 取图、转存的意外错误也只算这条线路失败
                    shot.notes.append(f'{central.route_label(route)}: {central.describe(error)}')
                    log.exception('多图 %s 处理出图结果时出错', central.route_label(route))
        except asyncio.CancelledError:
            shot.timed_out = True
            shot.notes.append(self._abandon_reason or '出图已取消')
            raise
        finally:
            shot.done = True
            shot.finished_at = time.monotonic()
            if not shot.timed_out:
                self._finished.put_nowait(shot)

    async def _attempt(self, shot: Shot, route: dict) -> bool:
        """在一条线路上走完「出图 → 取字节 → 转存图床」，返回这一路是否可以交付。"""
        label = central.route_label(route)
        started = time.monotonic()
        try:
            result = await central.generate_on(self.config, self.prompt, route)
        except Exception as error:  # noqa: BLE001 - 记下原因，交给下一条线路或调用方
            reason = central.describe(error)
            shot.notes.append(f'{label}: {reason}')
            log.warning('多图 %s 出图失败 用时=%.1fs: %s', label, time.monotonic() - started, reason)
            return False
        shot.result = result
        image = result['data'] or (await media.download(result['url']) or b'')
        shot.image = image
        log.info('多图 %s 出图完成 用时=%.1fs 返回=%s', shot.label, time.monotonic() - started,
                 '图片数据' if result['data'] else (result['url'] or '空'))
        if not image:
            shot.notes.append(f'{shot.label}: 接口只给了服务器下载不到的链接，拿不到图片内容')
        elif self.upload:
            shot.hosted_url = await hosting.upload(
                image, hosting.file_name(image), appid=self.appid,
            )
            if not shot.hosted_url:
                shot.notes.append(f'{shot.label}: 图床上传失败')
        if shot.hosted_url:
            return True
        return self.accept_unhosted and bool(image or result.get('url'))


def save(shot: Shot, base: dict, current: dict, started: float, batch_id: str) -> int:
    """把一路的结果写进历史；同一次请求的几路共用 batch_id。留档失败只记日志。

    出了图就记 success（钱已经花了，每日配额如实计数），送没送到看 delivered。
    """
    if shot.recorded:
        return shot.record_id
    shot.recorded = True
    result = shot.result or {}
    notes = '；'.join(shot.notes)
    if shot.delivered:
        error = f'送达前先失败过：{notes}' if notes else ''
    else:
        error = '；'.join(item for item in (shot.error, notes) if item)
    record = dict(base)
    record.update({
        'batch_id': batch_id,
        'status': 'success' if result else 'failed',
        'provider_id': result.get('provider_id') or shot.route['provider_id'],
        'provider': result.get('provider') or central.provider_name(shot.route['provider_id']),
        'model': result.get('model') or shot.route['model'],
        'size': current['image_size'],
        'image_url': result.get('url', ''),
        'hosted_url': shot.hosted_url,
        'send_mode': shot.send_mode or base.get('send_mode', ''),
        'delivered': shot.delivered,
        'error': error[:1000],
        'duration_ms': round(((shot.finished_at or time.monotonic()) - started) * 1000),
    })
    try:
        shot.record_id = store.add(record, current['history_limit'])
        if shot.image and current.get('history_save_images'):
            store.save_image(
                shot.record_id, shot.image, media.sniff(shot.image)[0],
                current['history_image_limit'],
            )
    except Exception:  # noqa: BLE001 - 留档失败不能影响把图发出去
        log.exception('多图留档失败')
    return shot.record_id
