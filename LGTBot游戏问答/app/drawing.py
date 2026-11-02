"""画图能力适配层。

「AI 画图」插件（≥1.8.0）把出图做成了它自己的对外工具，挂在框架的全局钩子上
（见 AI画图/app/capability.py）：``ai_draw.tools`` 检索、``ai_draw.call`` 调用，
不经过中央 AI LLM —— 中央 1.3 起去掉了跨插件共享工具。本模块把它包装成本插件
模型可见的第七个工具 ``draw_image``。

**工具名不凭空调用**：每次先从 ``ai_draw.tools`` 的检索结果里确认对方确实提供
``draw_image``，再按这个名字去调。对方改了名、版本没跟上时能直接说清楚，而不是
静默拿到一个空结果。

分工：出图、内容审核、留档、每日额度全部发生在 AI 画图那边，本插件只负责
「要不要给模型这个工具」「一次问答最多画几张」，以及把图片发给用户。
"""
from __future__ import annotations

import asyncio
import hashlib
import re

from core.base.logger import PLUGIN, get_logger

from . import store

log = get_logger(PLUGIN, 'LGTBot游戏问答')

# 正在跑的画图任务：缓存键 → Task。
#
# 这两层（内存任务 + 落盘缓存）是给「请求被打断」准备的。中央模块的单次调用超时
# 覆盖**整个** complete()，工具执行时间也算在内；画图动辄几十秒，一旦撞线，
# asyncio.wait_for 会取消整条调用链，中央那边记成 error='interrupted'，
# 而我们这边会退避重试。
#
# 问题在于：图那时候往往**已经在画甚至画完了**。重试再画一张，等于同一个请求
# 烧两次额度，用户还是什么都没收到。所以画图跑在独立任务里、用 shield 挡住取消：
# 调用方被取消，任务照样跑完并把结果写进缓存，下次（重试或用户重问）直接复用。
_pending: dict = {}


def _normalize(prompt: str) -> str:
    return ' '.join(str(prompt or '').split())


def _cache_key(scope: str, prompt: str) -> str:
    """按「谁问的 + 画什么」做键。

    带上 scope 是刻意的：不同用户提同样的描述，各自出各自的图，不会拿到别人那张。
    只在同一个人重试 / 重问时才复用 —— 那正是被打断后要救的场景。
    """
    raw = f'{scope}\x00{_normalize(prompt)}'.encode()
    return hashlib.sha1(raw).hexdigest()


def _cache_ttl(current: dict) -> int:
    return int(current.get('draw_cache_seconds') or 0)


_SIZE_RE = re.compile(r'^\s*(\d{2,5})\s*[x×*]\s*(\d{2,5})\s*$', re.IGNORECASE)


def _dimensions(result: dict) -> str:
    """从画图结果里取**实测**像素宽高，归一成 `宽x高`。

    AI 画图 1.5.1 起返回实测的 width / height（它自己解析图片头拿到的），
    并把「请求的尺寸」挪到了 requested_size。实测值才是对的 —— 有些线路
    并不严格按请求尺寸出图，照请求值写 Markdown 会让客户端按错误比例排版。

    下不到图片字节时它**不返回** width / height（免得 0 被当成真实值），
    那就退回 requested_size；再不行交给调用方按默认值处理。
    `size` 是 1.5.0 的旧键名，一并认，省得对方版本没跟上就取不到数。
    """
    width = int(result.get('width') or 0)
    height = int(result.get('height') or 0)
    if width > 0 and height > 0:
        return f'{width}x{height}'
    match = _SIZE_RE.match(str(result.get('requested_size') or result.get('size') or ''))
    return f'{int(match.group(1))}x{int(match.group(2))}' if match else ''

TOOLS_HOOK = 'ai_draw.tools'  # AI画图 的检索钩子：返回它对外的工具定义
CALL_HOOK = 'ai_draw.call'    # AI画图 的调用钩子：按工具名执行，结果在 result 里
REMOTE_TOOL = 'draw_image'    # AI画图 对外工具的名字（每次以检索结果为准）
TOOL_NAME = 'draw_image'      # 暴露给本插件模型的工具名
CALLER = 'LGTBot游戏问答'     # 传给对方留档用的调用方标注

# 画面描述的长度上限。AI 画图那边自己也有 input_max_length，这里先截一道，
# 避免模型把整段源码当描述丢过去。
_PROMPT_MAX = 600

TOOL_SCHEMA = {
    'type': 'function',
    'function': {
        'name': TOOL_NAME,
        'description': (
            '生成一张图片。**只有用户明确要求画图、生成图片、做张图时才调用**；'
            '规则、机制、计分、结算类问题一律用检索工具回答，不要配图。'
            '**要画的东西属于某个游戏时，必须先用 read_game_source / read_game_rule '
            '读到它在该游戏里的真实描述，再据此写画面描述** —— 凭游戏名想象会被拒绝。'
            '描述要写清主体、动作、场景、风格，越具体越好，不要传入任何链接。'
            '图片会由机器人自动发送给用户，你拿不到也不需要图片链接 —— '
            '回答里不要写链接、不要写 Markdown 图片语法。每次回答最多调用一次。'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'prompt': {
                    'type': 'string',
                    'description': (
                        '画面描述，中文英文均可。用户只给了一句话时可以补充细节，'
                        '但不要改变原意，也不要加入用户没提过的元素。'
                    ),
                },
            },
            'required': ['prompt'],
            'additionalProperties': False,
        },
    },
}


def enabled(current: dict) -> bool:
    """管理员是否在本插件面板打开了画图。不查对方是否在线 —— 那是运行时的事。"""
    return bool(current.get('draw_enabled'))


def _hooks():
    """框架的全局 HookManager；取不到时返回 None（等同于 AI 画图不可用）。"""
    try:
        from core.module.hook import get_hook_manager

        return get_hook_manager()
    except Exception:  # noqa: BLE001 — 取不到就按画图不可用处理
        return None


_MISSING = (
    '未检测到「AI 画图」的对外画图工具：请确认 AI画图（≥1.8.0）已加载，'
    '且它面板里的「公开画图工具」开着'
)


def state() -> dict:
    """面板诊断：AI 画图有没有把对外画图工具挂出来。

    挂没挂一眼就能看出来；没挂的几种原因（没装、没加载、对方开关关着）对管理员来说
    都是去 AI 画图那边看一眼，所以合成一句说清楚就够了。
    """
    hooks = _hooks()
    if hooks is None:
        return {'installed': False, 'usable': False, 'message': '框架钩子不可用，检索不到 AI 画图'}
    if not hooks.has(CALL_HOOK):
        return {'installed': False, 'usable': False, 'message': _MISSING}
    return {
        'installed': True, 'usable': True,
        'message': 'AI 画图的对外画图工具已就绪',
    }


async def _discover(hooks) -> str:
    """检索 AI 画图的对外工具，确认 draw_image 在里面。返回空串表示可用，否则是原因。"""
    found = await hooks.pipeline(TOOLS_HOOK, {})
    tools = found.get('tools') if isinstance(found, dict) else None
    names = {
        str((item.get('function') or {}).get('name') or '')
        for item in tools or [] if isinstance(item, dict)
    }
    if REMOTE_TOOL in names:
        return ''
    if not tools:
        return _MISSING
    return f'AI 画图没有提供 {REMOTE_TOOL} 工具（只有 {"、".join(sorted(names))}），可能是版本不匹配'


def detach_image(result: dict) -> dict:
    """把图片链接与尺寸从**回灌给模型**的结果里摘出来。

    刻意不让模型看到链接：它拿到就会往答案里贴，而 QQ 那边裸链接会被风控、
    Markdown 图片语法在普通文本消息里也渲染不出来，最后用户只看到一串 URL。
    图片由插件自己发一条消息，模型只需要知道「画好了」。

    返回 {} 表示这次没出图。
    """
    if not isinstance(result, dict):
        return {}
    url = str(result.pop('url', '') or '')
    size = str(result.pop('size', '') or '')
    reused = bool(result.pop('reused', False))
    result.pop('record_id', None)
    if not (result.get('ok') and url):
        return {}
    result['note'] = (
        '这张图之前已经画好了，直接复用，没有重新生成。' if reused
        else '图片已生成，'
    ) + '机器人会自动发给用户。直接用一句话回应即可，不要写链接。'
    return {'url': url, 'size': size, 'reused': reused}


async def _generate(prompt: str, current: dict, key: str) -> dict:
    """真正调一次画图，成功就落盘缓存。

    **这个协程跑在独立任务里**（见 run 的 shield）：调用方被超时取消也不影响它跑完。
    图已经在画了，丢掉纯属浪费额度 —— 跑完把结果写进缓存，下次直接拿。
    """
    hooks = _hooks()
    if hooks is None or not hooks.has(CALL_HOOK):
        return {'ok': False, 'error': state()['message']}
    problem = await _discover(hooks)
    if problem:
        return {'ok': False, 'error': problem}

    # 出图比一次模型调用慢得多（实测几十秒，慢线路能到 180 秒），必须单独设超时：
    # 任务挂住不放会一直留在 _pending 里，后面同样的描述会一直等它。
    #
    # ⚠️ 超时**宁可设长**：AI 画图 1.7 起会检测调用方是否还在
    #   （capability.draw 的 shield + session.leave）。我们这边 wait_for 一旦触发，
    #   取消会顺着钩子传到对方的 draw()，它把 caller_gone 置位 —— 图照画完，但**只留档、
    #   不再把链接给我们**。那等于图已经出了、额度也扣了，我们却拿不到，
    #   缓存也写不进去，下次重问还得重画。见 _cache_ttl 那套机制的初衷。
    timeout = float(current.get('draw_timeout_seconds') or 0)
    envelope = {
        'name': REMOTE_TOOL,
        # 模型能决定的只有描述；其余放外层，是我们自己定的、模型改不了
        'arguments': {'prompt': prompt},
        'caller': CALLER,
        # 明确只要一张（外层的 max_images 是上限）。对方 1.7.0 起支持多图并发出图，
        # 默认虽是 1，但这边写死更稳：本插件一次问答只发一张图（上限在 main.tool_handler
        # 里管），真要多图得先想清楚群里连发 N 张图的观感和对方的日额度。
        'max_images': 1,
    }
    # 多图模式才用得上，但一并传：它必须比我们自己的超时短，
    # 否则我们先超时走人，对方那边一张也交不出来。
    if timeout > 0:
        envelope['wait_seconds'] = int(max(10, min(280, timeout - 10)))
    call = hooks.pipeline(CALL_HOOK, envelope)
    try:
        reply = await (asyncio.wait_for(call, timeout) if timeout > 0 else call)
    except asyncio.TimeoutError:
        # 这条日志要能直接指出怎么改：对方已经在画了，是我们没等够
        log.warning(
            f'画图等待 {timeout:.0f} 秒未返回，已放弃；对方会把图画完但只留档不回链接。'
            f'慢线路可能要 180 秒，建议把面板的「单次画图超时」调大'
        )
        return {'ok': False, 'error': f'画图超时（{timeout:.0f} 秒未返回）'}
    except Exception as error:  # noqa: BLE001 — 失败原因原样给模型，让它换个说法或放弃
        log.warning(f'画图能力调用失败: {type(error).__name__}: {error}')
        return {'ok': False, 'error': f'画图失败: {error}'[:300]}

    # 钩子在等待期间被摘掉（对方重载）、或对方回调出错时，pipeline 会原样返回我们传进去的
    # dict —— 里面没有 result。这时不能当成功处理。
    result = reply.get('result') if isinstance(reply, dict) else None
    if result is None:
        return {'ok': False, 'error': 'AI 画图没有返回结果（可能正在重载），请稍后再试'}
    if not isinstance(result, dict):
        return {'ok': False, 'error': '画图能力返回了无法识别的结果'}
    if not result.get('ok'):
        # 对方的失败原因（命中违规词、额度用完、线路全挂）原样透给模型，
        # 它才知道该不该换个描述再试一次。
        reason = str(result.get('error') or '画图失败')[:300]
        if '调用方已离开' in reason:
            # 只可能是我们自己超时走人导致的（见上面 wait_for 的说明）。
            # 对方那句话是写给它自己日志看的，直接透给模型只会让它莫名其妙。
            log.warning('对方报告「调用方已离开」—— 本次画图是被我们自己的超时掐断的')
            reason = '画图没能在超时时间内完成，图已生成但没能取回，请管理员调大画图超时。'
        return {'ok': False, 'error': reason}
    # 1.7.0 起统一返回体：images 永远是数组，顶层镜像第一张（兼容老调用方）。
    # 优先读数组，不靠镜像 —— 镜像是对方为兼容留的，不该当成长期契约。
    images = result.get('images')
    first = images[0] if isinstance(images, list) and images and isinstance(images[0], dict) else result
    url = str(first.get('url') or result.get('url') or '')
    if not url:
        return {'ok': False, 'error': '画图成功但没有拿到图片链接'}
    extra = int(result.get('count') or 1) - 1
    if extra > 0:
        # 我们只要一张，多的不发也不缓存。真出现了说明对方忽略了 max_images，
        # 要么是版本没跟上，要么是它那边的行为变了，得看得见。
        log.warning(f'只要 1 张却收到 {extra + 1} 张，多余的已忽略（检查 max_images 是否生效）')
    # 实测像素宽高，留着给 Markdown 图片消息用 —— QQ 的 `![alt #宽px #高px](url)`
    # 要这两个数，写错了客户端就按错误的比例给图占位。
    # width / height 在 entry 里，requested_size 只在顶层，所以两边都要看。
    size = _dimensions(first) or _dimensions(result)
    if _cache_ttl(current) > 0:
        try:
            await asyncio.to_thread(store.draw_cache_put, key, url, size)
        except Exception as error:  # noqa: BLE001 — 缓存写不进去顶多下次重画
            log.warning(f'画图结果缓存失败: {error}')
    model = str(first.get('model') or result.get('model') or '')
    log.info(f'画图完成（{model or "未知模型"}）')
    return {'ok': True, 'url': url, 'model': model, 'size': size}


async def _cached(key: str, current: dict) -> dict:
    ttl = _cache_ttl(current)
    if ttl <= 0:
        return {}
    try:
        return await asyncio.to_thread(store.draw_cache_get, key, ttl)
    except Exception as error:  # noqa: BLE001 — 缓存读不到就当没有，照常画
        log.debug(f'画图缓存读取失败: {error}')
        return {}


async def reusable(arguments: dict, current: dict, scope: str = '') -> bool:
    """这次画图能不能**不花额度**拿到图：缓存里有，或已经有一模一样的在跑。

    给 main.py 的张数上限用 —— 复用不该占额度，否则被打断后重试会卡在
    「本次已经画过 1 张」上，明明有现成的图却发不出去。
    """
    if not enabled(current):
        return False
    prompt = _normalize(str((arguments or {}).get('prompt') or ''))[:_PROMPT_MAX]
    if not prompt:
        return False
    key = _cache_key(scope, prompt)
    task = _pending.get(key)
    if task is not None and not task.done():
        return True
    return bool(await _cached(key, current))


async def run(arguments: dict, current: dict, scope: str = '') -> dict:
    """执行一次画图。任何失败都收敛成 {'ok': False, 'error': ...} 交给模型。

    三条路，按代价从低到高：
      1. 缓存命中 —— 同一个人刚画过同样的东西，直接复用
      2. 已经有一模一样的任务在跑 —— 等它，不再起一个
      3. 都没有 —— 起一个独立任务去画

    `asyncio.shield` 是关键：我们这次 await 被取消（外层超时）时，任务本身**不会**
    被取消，会继续画完并写缓存。否则每次超时都白扔一张已经在画的图。
    """
    if not enabled(current):
        return {'ok': False, 'error': '画图能力未开启'}
    prompt = _normalize(str((arguments or {}).get('prompt') or ''))
    if not prompt:
        return {'ok': False, 'error': '缺少画面描述 prompt'}
    prompt = prompt[:_PROMPT_MAX]
    key = _cache_key(scope, prompt)

    cached = await _cached(key, current)
    if cached:
        log.info('画图命中缓存，直接复用已有图片')
        return {'ok': True, 'reused': True, 'model': '', **cached}

    task = _pending.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_generate(prompt, current, key))
        _pending[key] = task
        # 任务自己收尾：跑完就从 _pending 摘掉，别让字典无限长
        task.add_done_callback(
            lambda done, k=key: _pending.pop(k, None) if _pending.get(k) is done else None
        )
    else:
        log.info('同样的描述已经在画，等它出结果而不是再画一张')
    return await asyncio.shield(task)
