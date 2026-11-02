"""本插件自己的对外画图工具：挂在框架的全局钩子上，其他插件直接检索、直接调用，不经过中央 AI LLM。

中央 AI LLM 1.3 起去掉了跨插件共享工具：它的「插件私有工具」只注入注册者自己的请求，
「公用工具」是在子进程里跑的代码（单次最长 120 秒），都碰不到本插件的配置、历史库和图床。
框架的 HookManager 是进程内单例、任意字符串都能当钩子名，于是：

    from core.module.hook import get_hook_manager
    hooks = get_hook_manager()
    found = await hooks.pipeline('ai_draw.tools', {})        # 检索：found['tools'] 是 OpenAI function 定义
    reply = await hooks.pipeline('ai_draw.call', {
        'name': 'draw_image', 'arguments': {'prompt': '一只橘猫'}, 'caller': '我的插件',
    })
    result = reply['result']

没人挂钩子时 pipeline 原样返回传入的 dict（没有 tools / result 键），调用方据此判断本插件不在。

审核、限额与留档仍然全部发生在本插件里，调用方只拿到图床链接。
返回值刻意只给链接和少量元信息：工具结果会回灌进调用方模型的上下文，
把 base64 塞回去会瞬间撑爆 token。

每次调用都跑在独立的会话任务里：调用方超时离开（它那边 wait_for 一到点就会连带
取消我们）也不会把画到一半的图扔掉，而是照常画完并留档 —— 只是不再有人接收结果。
"""
from __future__ import annotations

import asyncio
import copy
import time

from core.base.logger import PLUGIN, get_logger

from . import central, config as draw_config, fanout, hosting, limiter, media, store

log = get_logger(PLUGIN, 'AI画图')
TOOLS_HOOK = 'ai_draw.tools'
CALL_HOOK = 'ai_draw.call'
HOOK_OWNER = 'AI画图'
TOOL_NAME = 'draw_image'
# 1 = 1.5~1.7 经中央 AI LLM 共享的 ai_draw:tool:draw；2 = 1.8 起的框架钩子
INTERFACE = 2
MAX_IMAGES = 10
_registered = None
_sessions: set[asyncio.Task] = set()

# 给调用方模型看的定义（OpenAI function 格式，可直接当 complete() 的调用级工具）。
# 只放模型该决定的参数；留档标注、多图等待、张数上限由调用方放在钩子外层。
TOOL = {
    'type': 'function',
    'function': {
        'name': TOOL_NAME,
        'description': (
            '根据文字描述生成图片，返回本机图床的图片直链、实测像素宽高（width / height），'
            '并附一段可直接发送的 QQ Markdown 图片语法（markdown 字段）。'
            '返回的链接已转存到机器人自己的图床，可以直接发出去；不会给 AI 接口的原始链接。'
            '默认画 1 张；max_images≥2 且 AI 画图开了多图模式时，几条线路同时各画一张，'
            '画好的都放进 images 数组一起返回，没画完的只留档、不会再补给你。'
            '描述要写清主体、动作、场景、风格；不要传入已有图片链接。'
            '图片由 AI 画图插件统一做内容审核与留档，失败时返回 ok=false 与原因。'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'prompt': {
                    'type': 'string',
                    'description': '画面描述，中文英文均可',
                },
                'max_images': {
                    'type': 'integer',
                    'minimum': 1,
                    'maximum': MAX_IMAGES,
                    'description': (
                        '最多要几张，默认 1。AI 画图没开多图模式、或多图线路不足两条时按 1 张处理'
                    ),
                },
            },
            'required': ['prompt'],
            'additionalProperties': False,
        },
    },
}


class _Session:
    """一次对外调用。`answer` 是交给调用方的结果；调用方离开后会话照常跑完。"""

    def __init__(self):
        self.answer: asyncio.Future = asyncio.get_running_loop().create_future()
        self.caller_gone = False
        self.batch: fanout.Batch | None = None

    def reply(self, payload: dict) -> None:
        if not self.answer.done():
            self.answer.set_result(payload)

    def leave(self) -> None:
        """调用方走了：剩下的图照常画完留档，但不再占图床，也不再要求有直链。"""
        self.caller_gone = True
        if self.batch is not None:
            self.batch.upload = False
            self.batch.accept_unhosted = True


def _int_in(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(high, max(low, number))


def _save(record: dict, current: dict, image: bytes = b'') -> int:
    """写一条记录，有图就一并留下；留档失败不该影响返回。"""
    try:
        record_id = store.add(record, current['history_limit'])
        if image and current.get('history_save_images'):
            store.save_image(
                record_id, image, media.sniff(image)[0], current['history_image_limit'],
            )
        return record_id
    except Exception:  # noqa: BLE001 - 留档失败只记日志
        log.exception('画图能力留档失败')
        return 0


def _fail(reason: str, record: dict | None = None, current: dict | None = None,
          image: bytes = b'') -> dict:
    """统一失败返回；需要留档时顺带写一条记录，有图就一并留下便于排查。"""
    if record is not None and current is not None:
        record['error'] = reason[:1000]
        _save(record, current, image)
    return {'ok': False, 'error': reason}


def _entry(current: dict, url: str, image: bytes, result: dict, record_id: int) -> dict:
    """一张图的返回项。量不出像素尺寸时不给 width / height / markdown，免得 0 被当成真实值。"""
    entry = {
        'url': url,
        'model': str((result or {}).get('model') or ''),
        'provider': str((result or {}).get('provider') or ''),
        'record_id': record_id,
    }
    # 实测像素尺寸：QQ 的 Markdown 图片必须写 #宽px #高px，调用方直接拿去用。
    width, height = media.dimensions(image) if image else (0, 0)
    if width > 0 and height > 0:
        entry.update({
            'width': width,
            'height': height,
            # 直接可用的 QQ Markdown 图片语法
            'markdown': f"![{current['markdown_alt']} #{width}px #{height}px]({url})",
        })
    return entry


def _payload(current: dict, entries: list[dict], *, mode: str, pending: int = 0,
             failed: list[dict] | None = None) -> dict:
    """统一返回体：`images` 永远是数组；顶层的 url / width / height / markdown 等
    镜像第一张，只认单图的老调用方不用改一行代码。
    """
    payload = {
        'ok': True,
        'mode': mode,
        'count': len(entries),
        'pending': pending,
        'images': entries,
        **entries[0],
        'requested_size': current['image_size'],
    }
    markdowns = [item['markdown'] for item in entries if item.get('markdown')]
    if markdowns:
        payload['markdown_all'] = '\n\n'.join(markdowns)
    if failed:
        payload['failed'] = failed
    return payload


async def _run(session: _Session, arguments: dict) -> dict:
    current = draw_config.load()
    if not current['capability_enabled']:
        return {'ok': False, 'error': 'AI 画图未对外开放画图能力'}
    prompt = str(arguments.get('prompt') or '').strip()
    caller = str(arguments.get('caller') or '')[:60]
    if not prompt:
        return {'ok': False, 'error': '缺少画面描述 prompt'}
    if len(prompt) > current['input_max_length']:
        return {'ok': False, 'error': f"画面描述不能超过 {current['input_max_length']} 字"}
    if not hosting.available():
        # 对外调用必须交图床直链：没有图床时画了也交不出去，不白花这一次出图。
        return {
            'ok': False,
            'error': f"没有可用图床（{hosting.state()['message']}），画图工具暂时交不出图片链接",
        }
    wanted = _int_in(arguments.get('max_images'), 1, 1, MAX_IMAGES)
    # 调用方没给就用面板值（保存时已校验过范围），给了再按 10~280 秒收拢
    wait = current['multi_wait_seconds']
    if arguments.get('wait_seconds') not in (None, ''):
        wait = _int_in(arguments.get('wait_seconds'), wait, 10, 280)

    record = {
        'created_at': time.time(), 'source': 'capability', 'status': 'failed',
        'user_id': 'capability', 'username': caller or '其他插件', 'chat_type': 'capability',
        'prompt': prompt, 'send_mode': 'capability',
    }
    blocked = next((
        word for word in current['blocked_words']
        if word and word.casefold() in prompt.casefold()
    ), '')
    if blocked:
        record['status'] = 'blocked'
        log.warning('画图能力调用命中违规词 caller=%s 违规词=%s', caller or '未知', blocked)
        return _fail(f'描述命中违规词：{blocked}', record, current)

    # 额度按出图张数算：多图一次出好几张，剩余额度不够就少画几张。
    day = limiter.day_start()
    left: int | None = None
    quota = int(current['capability_daily_limit'])
    if quota > 0:
        used = store.count_since(day, 'capability')
        if used >= quota:
            return {'ok': False, 'error': f'画图能力今日出图已达上限（{quota} 张）'}
        left = quota - used
    total = int(current['global_daily_limit'])
    if total > 0:
        used = store.count_since(day)
        if used >= total:
            return {'ok': False, 'error': '今日全局画图额度已用完'}
        left = total - used if left is None else min(left, total - used)

    started = time.monotonic()
    review = await central.moderate(current, prompt)
    if review.get('flagged') or (
        not review.get('available')
        and current['moderation_enabled'] and current['moderation_fail_closed']
    ):
        record.update({
            'status': 'blocked',
            'duration_ms': round((time.monotonic() - started) * 1000),
        })
        log.warning('画图能力调用未通过内容审核 caller=%s', caller or '未知')
        return _fail(review.get('error') or '描述未通过内容审核', record, current)

    log.info('画图能力被调用 caller=%s 张数=%s 描述=%s', caller or '未知', wanted, prompt[:60])
    optimized = await central.optimize_prompt(current, prompt)
    record['final_prompt'] = central.build_prompt(current, optimized)
    if session.caller_gone:
        # 审核、改写期间调用方就走了：还没开画，画了也没人收
        record['duration_ms'] = round((time.monotonic() - started) * 1000)
        return _fail('调用方在出图前就已离开，没有出图', record, current)

    multi = central.multi_routes(current)
    routes = multi[:wanted if left is None else min(wanted, left)]
    if wanted >= 2 and len(routes) >= 2:
        return await _multi(session, current, record, routes, started, wait, caller)
    payload = await _single(session, current, record, started, caller)
    if wanted >= 2 and payload.get('ok'):
        if not current['multi_enabled']:
            reason = 'AI 画图没有开启多图模式'
        elif len(multi) < 2:
            reason = 'AI 画图没有配好两条以上可用的「多图」线路'
        else:
            reason = '今日额度只够再画 1 张'
        payload['note'] = reason + '，按单张出图'
    return payload


async def _single(session: _Session, current: dict, record: dict, started: float,
                  caller: str) -> dict:
    """单张：按线路顺序出图。

    工具返回的链接**必须**是自己图床的直链：AI 接口的原始域名没在 QQ 开放平台报备，
    调用方拿去发 Markdown 图片会被拦下来不显示。所以每条线路都要走完
    「出图 → 拿到字节 → 传图床」，中间任何一步掉链子就换下一条线路重来，
    与聊天指令的故障转移同一套逻辑，不能只试一条就放弃。
    """
    routes = central.valid_routes(current)
    attempts = max(1, min(int(current['delivery_max_attempts']), len(routes) or 1))
    tried: set[tuple[str, str]] = set()
    notes: list[str] = []
    result = None
    image = b''
    hosted_url = ''
    for index in range(attempts):
        if session.caller_gone:
            notes.append('调用方已离开，不再换线路重试')
            break
        try:
            async with limiter.semaphore(current['max_concurrency']):
                attempt = await central.generate(current, record['final_prompt'], exclude=tried)
        except Exception as error:  # noqa: BLE001 - 线路耗尽或全部报错
            notes.append(str(error)[:400])
            log.warning('画图能力生图失败 caller=%s: %s', caller or '未知', error)
            break
        tried.add((attempt['provider_id'], attempt['model']))
        result = attempt
        label = f"{attempt['provider'] or attempt['provider_id']}/{attempt['model']}"
        record.update({
            'status': 'success',
            'provider': attempt['provider'], 'provider_id': attempt['provider_id'],
            'model': attempt['model'], 'size': current['image_size'],
            'image_url': attempt['url'],
        })
        candidate = attempt['data'] or (await media.download(attempt['url']) or b'')
        if not candidate:
            notes.append(f'{label}: 接口只给了服务器下载不到的链接，拿不到图片内容')
            log.warning('画图能力换下一条线路重试：%s', notes[-1])
            continue
        image = candidate
        if session.caller_gone:
            break  # 没人收了，不必再占图床
        if not hosting.available():
            # 压根没有可用图床，换线路拿到的还是同一个图床，重试纯属浪费额度。
            notes.append(f"没有可用图床（{hosting.state()['message']}），换线路也无济于事")
            break
        hosted_url = await hosting.upload(image, hosting.file_name(image))
        if hosted_url:
            break
        # 图床活着却收不下这张图（格式/体积），换条线路换张图还有机会。
        notes.append(f'{label}: 图床上传失败')
        log.warning('画图能力换下一条线路重试：%s', notes[-1])

    record['duration_ms'] = round((time.monotonic() - started) * 1000)
    if result is None:
        return _fail('；'.join(notes)[:500] or '没有可用的生图线路', record, current)
    record['hosted_url'] = hosted_url
    if session.caller_gone:
        record['error'] = '；'.join(['调用方已超时离开，图片只留档', *notes])[:1000]
        _save(record, current, image)
        log.info('画图能力调用方已离开，出的图只留档 caller=%s', caller or '未知')
        return {'ok': False, 'error': '调用方已离开'}
    if not hosted_url:
        return _fail(
            '；'.join(notes)[:400]
            + '。不返回接口原始链接：那个域名未在 QQ 开放平台报备，发出去图片不会显示',
            record, current, image,
        )
    record['delivered'] = 1
    if notes:
        record['error'] = '已改用备用线路；先前失败：' + '；'.join(notes)[:800]
    record_id = _save(record, current, image)
    entry = _entry(current, hosted_url, image, result, record_id)
    log.info('画图能力出图成功 caller=%s 线路=%s/%s 尺寸=%s',
             caller or '未知', result['provider'], result['model'],
             f"{entry['width']}x{entry['height']}" if entry.get('width') else '未知')
    return _payload(current, [entry], mode='single')


async def _multi(session: _Session, current: dict, base: dict, routes: list[dict],
                 started: float, wait: int, caller: str) -> dict:
    """多张：几条「多图」线路同时开画，到 wait 秒把画好的交给调用方。

    交卷以后还没画完的接着画到 290 秒，画好只留档、不再补给调用方 —— 它早就拿着结果
    走了。多图线路全部失败时，按顺序试一遍没勾「多图」的线路兜底。
    """
    deadline = started + fanout.DEADLINE_SECONDS
    gather_at = min(deadline, started + wait)
    batch = fanout.Batch(current, base['final_prompt'], upload=True, accept_unhosted=False)
    session.batch = batch  # 调用方中途离开时由 session.leave() 改成只留档
    backups = [route for route in central.valid_routes(current) if not route.get('multi')]

    def save(shot: fanout.Shot) -> int:
        return fanout.save(shot, base, current, started, batch.id)

    # 一次多图调用只占一个并发名额，理由同聊天指令
    semaphore = limiter.semaphore(current['max_concurrency'])
    try:
        async with asyncio.timeout(max(0.1, gather_at - time.monotonic())):
            await semaphore.acquire()
    except TimeoutError:
        base['duration_ms'] = round((time.monotonic() - started) * 1000)
        return _fail('排队等待画图名额超时', base, current)
    try:
        log.info('画图能力 多图 caller=%s 同时请求 %s 条线路：%s 等待=%ss', caller or '未知',
                 len(routes), '、'.join(central.route_label(route) for route in routes), wait)
        for route in routes:
            batch.add([route], route_timeouts=False)
        ready: list[fanout.Shot] = []
        failed: list[fanout.Shot] = []
        fallback = False
        while True:
            shot = await batch.next(gather_at)
            if shot is None:
                break
            if shot.ok:
                ready.append(shot)
                continue
            failed.append(shot)
            await asyncio.to_thread(save, shot)
            if (
                not fallback and backups and not ready and not batch.pending
                and gather_at - time.monotonic() >= fanout.FALLBACK_MIN_SECONDS
            ):
                fallback = True
                log.info('画图能力 多图线路全部失败，改按顺序尝试备用线路：%s',
                         '、'.join(central.route_label(route) for route in backups))
                batch.add(backups)

        pending = len(batch.pending)
        gone = session.caller_gone
        for shot in ready:
            shot.delivered = not gone
            if gone:
                shot.error = '调用方已超时离开，图片只留档'
            await asyncio.to_thread(save, shot)
        answer = _multi_answer(current, ready, failed, pending, wait)
        session.reply(answer)
        log.info('画图能力 多图交卷 caller=%s 交出 %s 张 失败 %s 路 还在画 %s 张',
                 caller or '未知', len(ready), len(failed), pending)

        # 交卷以后晚到的图只留档：不再占图床，也不再要求有直链
        batch.upload = False
        batch.accept_unhosted = True
        while True:
            shot = await batch.next(deadline)
            if shot is None:
                break
            if shot.ok:
                shot.error = '画完时已过调用方的等待时长，只留档、没有返回'
            await asyncio.to_thread(save, shot)
        dropped = await batch.abandon()
        for shot in dropped:
            await asyncio.to_thread(save, shot)
        if dropped:
            log.warning('画图能力 多图到 %s 秒上限仍有 %s 路没画完，已放弃',
                        fanout.DEADLINE_SECONDS, len(dropped))
        return answer
    finally:
        if batch.pending:
            # 只有被取消（插件卸载）才会走到这里
            await batch.abandon('插件重载，未完成的出图已放弃')
        for shot in batch.shots:
            save(shot)
        semaphore.release()


def _multi_answer(current: dict, ready: list, failed: list, pending: int, wait: int) -> dict:
    reasons = [
        {
            'model': shot.route['model'],
            'provider': central.provider_name(shot.route['provider_id']),
            'error': shot.reason[:200],
        }
        for shot in failed
    ]
    if ready:
        entries = [
            _entry(current, shot.hosted_url, shot.image, shot.result, shot.record_id)
            for shot in ready
        ]
        return _payload(current, entries, mode='multi', pending=pending, failed=reasons)
    if pending:
        error = f'等了 {wait} 秒还没有画好的图（还有 {pending} 张在画，画好只留档、不会再返回）'
    else:
        error = '；'.join(
            f"{item['provider']}/{item['model']}: {item['error']}" for item in reasons
        )[:500] or '多图线路全部失败'
    return {'ok': False, 'mode': 'multi', 'count': 0, 'pending': pending, 'error': error}


async def _drive(session: _Session, arguments: dict) -> None:
    try:
        session.reply(await _run(session, arguments))
    except asyncio.CancelledError:
        session.reply({'ok': False, 'error': 'AI 画图插件正在重载，本次调用已中止'})
        raise
    except Exception as error:  # noqa: BLE001 - 工具结果要始终可序列化
        log.exception('画图能力执行异常')
        session.reply({'ok': False, 'error': f'画图能力执行异常：{error}'[:300]})


async def draw(arguments: dict) -> dict:
    """对外画图入口；任何异常都收敛成 ok=false，不往调用方的模型里抛栈。"""
    session = _Session()
    task = asyncio.create_task(_drive(session, arguments if isinstance(arguments, dict) else {}))
    _sessions.add(task)
    task.add_done_callback(_sessions.discard)
    try:
        return await asyncio.shield(session.answer)
    except asyncio.CancelledError:
        # 调用方超时或被取消：会话照常跑完并留档，只是结果没人收了
        session.leave()
        log.info('画图能力调用方已离开，本次结果只留档')
        raise


async def _on_tools(data):
    """钩子「检索」：给出本插件对外的工具定义，可直接当 complete() 的调用级工具。"""
    current = draw_config.load()
    multi = central.multi_routes(current)
    return {
        **(data if isinstance(data, dict) else {}),
        'plugin': 'AI画图',
        'interface': INTERFACE,
        'call_hook': CALL_HOOK,
        'tools': [copy.deepcopy(TOOL)],
        'limits': {
            'max_images': len(multi) if len(multi) >= 2 else 1,
            'default_wait_seconds': current['multi_wait_seconds'],
            'hosting_ready': hosting.available(),
        },
    }


async def _on_call(data):
    """钩子「调用」：模型给的参数放 arguments；留档标注、多图等待、张数上限在外层，模型改不了。"""
    if not isinstance(data, dict):
        return data
    name = str(data.get('name') or '')
    if name != TOOL_NAME:
        return {**data, 'result': {'ok': False, 'error': f'AI 画图没有名为 {name or "（空）"} 的工具'}}
    arguments = dict(data['arguments']) if isinstance(data.get('arguments'), dict) else {}
    arguments['caller'] = str(data.get('caller') or arguments.get('caller') or '')
    if data.get('wait_seconds') not in (None, ''):
        arguments['wait_seconds'] = data['wait_seconds']
    if data.get('max_images') not in (None, ''):
        # 外层的 max_images 是调用方定的上限：模型要得再多也不超过它
        arguments['max_images'] = min(
            _int_in(arguments.get('max_images'), 1, 1, MAX_IMAGES),
            _int_in(data['max_images'], 1, 1, MAX_IMAGES),
        )
    return {**data, 'result': await draw(arguments)}


async def shutdown() -> None:
    """插件卸载时收掉还在跑的会话：不留没人管的出图任务，已出的图照常留档。"""
    tasks = [task for task in _sessions if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _hooks():
    """框架的全局 HookManager；取不到时返回 None。"""
    try:
        from core.module.hook import get_hook_manager

        return get_hook_manager()
    except Exception:  # noqa: BLE001 - 取不到就当没有对外能力
        return None


def _online(manager) -> bool:
    try:
        entries = manager.list_hooks().get(CALL_HOOK, [])
    except Exception:  # noqa: BLE001 - 状态查询失败按未挂上处理
        return False
    return any(item.get('owner') == HOOK_OWNER for item in entries)


def register() -> bool:
    """把画图工具挂到框架钩子上；重复调用无副作用。"""
    global _registered
    manager = _hooks()
    if manager is None:
        return False
    if _registered is manager and _online(manager):
        return True
    # 热重载后旧模块的回调可能还挂着：按 owner 先摘干净再挂新的，免得同一个请求被画两遍
    manager.unregister_owner(HOOK_OWNER)
    manager.register(TOOLS_HOOK, _on_tools, owner=HOOK_OWNER)
    manager.register(CALL_HOOK, _on_call, owner=HOOK_OWNER)
    _registered = manager
    log.info('画图工具已挂到框架钩子：%s / %s', TOOLS_HOOK, CALL_HOOK)
    return True


def unregister() -> None:
    global _registered
    manager = _registered or _hooks()
    if manager is not None:
        manager.unregister_owner(HOOK_OWNER)
    _registered = None


def sync(enabled: bool) -> bool:
    """按「公开画图工具」开关挂上或摘下。"""
    if enabled:
        return register()
    unregister()
    return False


def state() -> dict:
    manager = _hooks()
    return {
        'tools_hook': TOOLS_HOOK, 'call_hook': CALL_HOOK, 'tool': TOOL_NAME,
        'online': manager is not None and _online(manager),
        # 对外调用一定要过图床，图床不可用时这个工具必然失败，面板要能提前看到。
        'hosting_ready': hosting.available(),
    }
