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
import importlib.util
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


# ==================== 中央模块与画图能力的假实现 ====================

class FakeService:
    """假的中央 AI LLM 服务，只实现画图能力相关的三个接口。

    ``result`` 是 call_capability 的返回值；默认按 AI画图 1.7 的返回体造。
    """

    def __init__(self, *, result=None, shared=True, online=True, enabled=True, present=True,
                 delay=0.0, boom=None):
        self.result = result if result is not None else draw_payload([draw_entry('https://bed/1.png')])
        self.shared, self.online, self.enabled, self.present = shared, online, enabled, present
        self.delay, self.boom = delay, boom
        self.calls: list = []

    def _item(self) -> dict:
        return {'key': 'ai_draw:tool:draw', 'id': 'draw', 'kind': 'tool',
                'source_plugin': 'ai_draw', 'name': 'AI 画图', 'description': 'd',
                'enabled': self.enabled, 'shared': self.shared, 'online': self.online}

    def plugin_capabilities(self, *, consumer_plugin='', kind='', public=False):
        return [self._item()] if self.present else []

    def list_capabilities(self, consumer_plugin, kind=''):
        if not (self.present and self.online and self.enabled and self.shared):
            return []
        return [self._item()]

    async def call_capability(self, consumer_plugin, capability_key, arguments=None):
        self.calls.append({'consumer': consumer_plugin, 'key': capability_key,
                           'arguments': dict(arguments or {})})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.boom:
            raise self.boom
        return self.result


def use_service(service) -> None:
    """把假服务挂到框架桩上；传 None 等于中央模块不存在。"""
    if service is None:
        APP.module_manager = None
        return
    APP.module_manager = types.SimpleNamespace(
        get=lambda name: service if name == 'ai_llm' else None, list_modules=lambda: [])


def draw_entry(url: str, width: int = 768, height: int = 1344) -> dict:
    """AI画图 1.7 返回体里的一张图（capability._entry）。"""
    return {'url': url, 'model': 'sd', 'provider': 'p', 'record_id': 1,
            'width': width, 'height': height,
            'markdown': f'![AI画图 #{width}px #{height}px]({url})'}


def draw_payload(entries: list, mode: str = 'single', **extra) -> dict:
    """AI画图 1.7 的统一返回体：images 数组 + 顶层镜像第一张（capability._payload）。"""
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
