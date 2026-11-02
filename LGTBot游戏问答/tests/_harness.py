"""测试共用的桩与工具。

插件跑在 ElainaBot_v2 框架里，直接 import 会因为缺 ``core.*`` 失败。这里把框架接口
打成最小的桩，再把**仓库里这份**插件当成 ``plugins.LGTBot游戏问答`` 包载入 ——
不依赖测试环境、不依赖真实的中央 AI LLM 模块、不碰任何真实配置或数据库。

每个 test_*.py 由 run_all.py 用独立子进程跑：插件里有不少模块级状态（配置缓存、
SQLite 连接、画图任务表），同进程连跑会互相串味。

文件名以 _ 开头是刻意的：框架的热重载监视器忽略 _ 开头的 .py。tests/ 本来就不会
被同步到测试环境（见仓库根 sync_to_test.sh），这只是多一层保险。
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import importlib.util
import inspect
import os
import sys
import tempfile
import types

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(TESTS_DIR)
PKG = 'plugins.LGTBot游戏问答'

# Windows 控制台默认 GBK，Git Bash 按 UTF-8 读 —— 不改的话中文输出全是乱码。
# 显式设了 PYTHONIOENCODING 的以用户为准。
if not os.environ.get('PYTHONIOENCODING'):
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass

# ==================== 框架打桩 ====================

_NOOP = lambda *a, **k: None  # noqa: E731


def _stub(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


_stub('core')
_stub('core.base')
_stub('core.base.logger', PLUGIN='plugin', get_logger=lambda *a, **k: types.SimpleNamespace(
    info=_NOOP, warning=_NOOP, error=_NOOP, debug=_NOOP, exception=_NOOP))
_stub('core.base.config', cfg=types.SimpleNamespace(get_bot_config=lambda appid: {}, get=_NOOP))
_stub('core.plugin')
# 装饰器原样返回函数：参数（正则、优先级）拿不到，需要时去读源码比对
_stub('core.plugin.decorators', handler=lambda *a, **k: (lambda fn: fn),
      on_load=lambda fn: fn, on_unload=lambda fn: fn)
_stub('core.plugin.web_pages', register_page=_NOOP, unregister_page=_NOOP,
      register_route=lambda *a, **k: (lambda fn: fn))
APP = types.SimpleNamespace(module_manager=None)
_stub('core.application', get_app=lambda: APP)


class FakeHooks:
    """框架 HookManager（core/module/hook.py）的最小替身，语义照真实实现：

    任意字符串当钩子名；pipeline 让数据按 priority 依次流过回调，回调返回 None 即拦截；
    回调抛异常只记日志、**数据原样往下传** —— 所以对方炸了时调用方拿回的是自己传进去的 dict。
    """

    def __init__(self):
        self._hooks: dict = {}

    def register(self, name, callback, *, owner='unknown', priority=100):
        self._hooks.setdefault(name, []).append((priority, owner, callback))

    def unregister_owner(self, owner):
        for name in list(self._hooks):
            self._hooks[name] = [entry for entry in self._hooks[name] if entry[1] != owner]
            if not self._hooks[name]:
                del self._hooks[name]

    def has(self, name):
        return bool(self._hooks.get(name))

    async def pipeline(self, name, data):
        if data is None:
            return None
        for _priority, _owner, callback in sorted(self._hooks.get(name, []), key=lambda e: e[0]):
            try:
                value = callback(data)
                data = await value if inspect.isawaitable(value) else value
            except Exception:  # noqa: BLE001 — 与真实实现一致：吞掉异常，数据原样往下传
                continue
            if data is None:
                return None
        return data


HOOKS = FakeHooks()
_stub('core.module')
_stub('core.module.hook', get_hook_manager=lambda: HOOKS)

# ==================== 载入插件 ====================

sys.modules.setdefault('plugins', types.ModuleType('plugins'))
sys.modules['plugins'].__path__ = [os.path.dirname(PLUGIN_DIR)]
_spec = importlib.util.spec_from_file_location(
    PKG, os.path.join(PLUGIN_DIR, 'main.py'), submodule_search_locations=[PLUGIN_DIR])
main = importlib.util.module_from_spec(_spec)
sys.modules[PKG] = main
_spec.loader.exec_module(main)

central = main.central
config = main.config
drawing = main.drawing
games = main.games
safety = main.safety
sandbox = main.sandbox
store = main.store
tools = main.tools
webpanel = main.webpanel

# ==================== 断言与汇总 ====================

_ok: list = []
_bad: list = []


def section(title: str) -> None:
    print(f'\n=== {title} ===')


def check(name: str, condition, detail='') -> bool:
    passed = bool(condition)
    (_ok if passed else _bad).append(name)
    suffix = f'  << {detail!r}'[:400] if detail != '' and not passed else ''
    print(('  ok   ' if passed else '  FAIL ') + name + suffix)
    return passed


def finish() -> None:
    """打印汇总并以失败数作为退出码。run_all.py 靠最后一行解析结果，格式别改。"""
    try:
        store.close()
    except Exception:  # noqa: BLE001 — 收尾失败不该掩盖测试结果
        pass
    print(f'\nRESULT {len(_ok)} / {len(_ok) + len(_bad)}')
    if _bad:
        print('失败项: ' + ' | '.join(_bad))
    sys.exit(1 if _bad else 0)


_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


def run(coro):
    return _loop.run_until_complete(coro)


# ==================== 隔离的配置与存储 ====================

def fresh_env(**overrides) -> dict:
    """临时目录里起一份全新的配置与数据库，返回当前配置。

    测试绝不碰插件真实的 data/：那里是线上配置与聊天记录。
    """
    tmp = tempfile.mkdtemp(prefix='lgtqa-test-')
    config.init(tmp)
    store.close()
    store.connect(tmp)
    if overrides:
        config.save(overrides)
    return config.load()


@contextlib.contextmanager
def patched(target, **attrs):
    """临时替换模块属性，退出时原样还原（哪怕断言中途抛错）。"""
    saved = {name: getattr(target, name) for name in attrs}
    for name, value in attrs.items():
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(target, name, value)


# ==================== AI 画图（对外画图工具）的假实现 ====================

DRAW_TOOL = {'type': 'function', 'function': {
    'name': 'draw_image', 'description': 'd',
    'parameters': {'type': 'object', 'properties': {'prompt': {'type': 'string'}},
                   'required': ['prompt'], 'additionalProperties': False}}}


class FakeDrawPlugin:
    """假的「AI 画图」：像 1.8.0 那样把 draw_image 挂到框架钩子上（见 use_draw）。

    ``result`` 是 ai_draw.call 回给调用方的 result，默认按 AI画图 1.7+ 的返回体造；
    ``boom`` 让回调抛异常（真实钩子会吞掉、原样返回传入的 dict），``swallow`` 让回调
    不给 result；``calls`` 记着每次收到的外层 dict。
    """

    OWNER = 'AI画图'

    def __init__(self, *, result=None, tools=None, delay=0.0, boom=None, swallow=False):
        self.result = result if result is not None else draw_payload([draw_entry('https://bed/1.png')])
        self.tools = tools if tools is not None else [DRAW_TOOL]
        self.delay, self.boom, self.swallow = delay, boom, swallow
        self.calls: list = []
        self.cancelled = False

    async def on_tools(self, data):
        return {**data, 'plugin': 'AI画图', 'interface': 2, 'call_hook': 'ai_draw.call',
                'tools': copy.deepcopy(self.tools)}

    async def on_call(self, data):
        self.calls.append(copy.deepcopy(data))
        if self.delay:
            try:
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                self.cancelled = True   # 调用方的超时顺着钩子传过来了
                raise
        if self.boom:
            raise self.boom
        if self.swallow:
            return data
        return {**data, 'result': copy.deepcopy(self.result)}


def use_draw(plugin) -> None:
    """把假的 AI 画图挂到钩子上；传 None 等于 AI 画图没装、没加载或没开对外工具。"""
    HOOKS.unregister_owner(FakeDrawPlugin.OWNER)
    if plugin is not None:
        HOOKS.register('ai_draw.tools', plugin.on_tools, owner=FakeDrawPlugin.OWNER)
        HOOKS.register('ai_draw.call', plugin.on_call, owner=FakeDrawPlugin.OWNER)


def draw_entry(url: str, width: int = 768, height: int = 1344) -> dict:
    """AI画图 1.7+ 返回体里的一张图（capability._entry）。"""
    return {'url': url, 'model': 'sd', 'provider': 'p', 'record_id': 1,
            'width': width, 'height': height,
            'markdown': f'![AI画图 #{width}px #{height}px]({url})'}


def draw_payload(entries: list, mode: str = 'single', **extra) -> dict:
    """AI画图 1.7+ 的统一返回体：images 数组 + 顶层镜像第一张（capability._payload）。"""
    return {'ok': True, 'mode': mode, 'count': len(entries), 'pending': 0,
            'images': entries, **entries[0], 'requested_size': '1024x1024', **extra}


# ==================== 假消息事件 ====================

class FakeEvent:
    """记录所有发出去的消息。``markdown_ok=False`` 模拟 Markdown 被平台拒收。"""

    def __init__(self, user_id='u1', *, is_group=True, markdown_ok=True, media_ok=True):
        self.user_id, self.appid = user_id, 'app1'
        self.is_group, self.is_channel, self.is_bot = is_group, False, False
        self.event_type = 'GROUP_AT_MESSAGE_CREATE'
        self.markdown_ok, self.media_ok = markdown_ok, media_ok
        self.texts: list = []    # (正文, 关键字参数)
        self.media: list = []    # 富媒体图片的载荷

    async def reply(self, text, **kw):
        self.texts.append((text, kw))
        if kw.get('msg_type') == 2 and not self.markdown_ok:
            return None          # 平台没给消息 ID = 没送达
        return {'id': 'm'}

    async def reply_image(self, payload, content='', **kw):
        self.media.append(payload)
        return {'id': 'i'} if self.media_ok else None

    def image_messages(self) -> list:
        return [text for text, kw in self.texts if kw.get('msg_type') == 2]

    def text_messages(self) -> list:
        return [text for text, kw in self.texts if kw.get('msg_type') != 2]


def answer_with(question: str, fake_ask, event=None, *, index=None, **central_overrides):
    """跑一轮完整的 _answer，返回事件对象（里面记着发出去的所有消息）。

    把 central.ask 换成 ``fake_ask``，沙箱根给一个假的，审核默认放行；
    ``index`` 给假的游戏索引（接地闸要用它判断有没有点名游戏）。
    """
    event = event or FakeEvent()

    async def passed(current, text):
        return {'available': True, 'flagged': False}

    central_attrs = dict(ask=fake_ask, available=lambda: True,
                         moderate_input=passed, moderate_output=passed)
    central_attrs.update(central_overrides)
    roots = [{'id': 'games', 'path': 'lgtbot/games', 'label': 'g'}]
    with patched(central, **central_attrs), \
            patched(sandbox, roots=lambda current: roots), \
            patched(games, index=(lambda current, refresh=False: index) if index is not None
                    else games.index):
        run(main._answer(event, question))
    return event


def read_source(relative: str) -> str:
    with open(os.path.join(PLUGIN_DIR, relative), encoding='utf-8') as file:
        return file.read()
