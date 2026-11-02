"""超时与重试：画图时间要算进模型调用超时，瞬时/永久故障分类要准。"""
from _harness import central, check, config, finish, fresh_env, patched, run, section

base = fresh_env()

section('画图时间计入单次模型调用超时')
# 生产日志里连着三条 error=interrupted、duration_ms 都卡在 90013 —— 超时只管模型调用、
# 没给画图留时间，三次重试全被自己掐断。
off = dict(base, draw_enabled=False, draw_timeout_seconds=180)
check('画图关着不加额度', central.tool_time_budget(off) == 0.0)
on = dict(base, draw_enabled=True, draw_timeout_seconds=180, draw_max_per_question=2)
check('开着按最坏情况加：超时 × 张数', central.tool_time_budget(on) == 360.0,
      central.tool_time_budget(on))

section('画图超时宁可设长')
# AI画图 1.7 起能检测调用方走人：我们先超时，它就只留档不回链接，图白画。
default = config.DEFAULT_CONFIG['draw_timeout_seconds']
check('默认 180 秒，对齐 AI画图 的线路上限', default == 180, default)
total = config.DEFAULT_CONFIG['request_timeout_seconds'] + central.tool_time_budget(
    dict(base, draw_enabled=True))
check('默认配置下整轮仍在 QQ 被动消息 5 分钟窗口内', total <= 300, total)

section('中央 1.3 的单工具超时：画图开着要放宽')
# 中央 1.3 起每个工具单独限时（默认 120 秒），到点终止整次 complete()，报的「执行超时」
# 又会被 is_transient 当成瞬时故障，整轮问答连同所有检索重跑一遍。
check('画图关着沿用中央默认 120 秒', central.tool_timeout(off) == 120.0, central.tool_timeout(off))
wait180 = dict(base, draw_enabled=True, draw_timeout_seconds=180)
check('开着比画图等待多留 30 秒（让自己的画图超时先到）',
      central.tool_timeout(wait180) == 210.0, central.tool_timeout(wait180))
check('画图等待不限时（0）给到 600', central.tool_timeout(dict(wait180, draw_timeout_seconds=0)) == 600.0)
check('等待很短时也不低于中央默认',
      central.tool_timeout(dict(wait180, draw_timeout_seconds=30)) == 120.0)
check('始终比画图等待长', all(
    central.tool_timeout(dict(wait180, draw_timeout_seconds=s)) > s for s in (10, 90, 180, 300, 600)))
check('中央的「执行超时」确实会被当成瞬时故障（所以必须放宽）',
      central.is_transient('工具 draw_image 执行超时') is True)


class NewCentral:
    """1.3 起的 complete()：有 tool_timeout。"""

    def __init__(self):
        self.kwargs = {}

    async def complete(self, messages, *, tool_timeout: float = 120, **kwargs):
        self.kwargs = dict(kwargs, tool_timeout=tool_timeout)
        return {'text': 'ok'}


class OldCentral:
    """1.3 以前的 complete()：没有 tool_timeout，也不收 **kwargs。"""

    def __init__(self):
        self.called = False

    async def complete(self, messages, *, system_prompt='', provider_id='', model='',
                       temperature=None, max_tokens=None, tools=None, tool_handler=None,
                       max_tool_rounds=None, session_id='', consumer_plugin='',
                       prepare_context=True):
        self.called = True
        return {'text': 'ok'}


async def _noop_tool(name, arguments):
    return {}


def _ask_with(service):
    with patched(central, get_service=lambda: service,
                 resolve_selection=lambda provider_id='', model='': ('', '')):
        return run(central.ask(wait180, [{'role': 'user', 'content': 'q'}], 'sys', _noop_tool, 's'))


new = NewCentral()
_ask_with(new)
check('新版中央收到放宽后的 tool_timeout', new.kwargs.get('tool_timeout') == 210.0, new.kwargs)
old = OldCentral()
try:
    run(old.complete([], tool_timeout=1))
    strict = False
except TypeError:
    strict = True
check('（前提）老版签名传 tool_timeout 会 TypeError', strict)
old.called = False
answer = _ask_with(old)
check('老版中央不传 tool_timeout，照常能答', old.called and answer == {'text': 'ok'}, answer)

section('瞬时 / 永久故障分类')
for text, expect in [
    ('HTTP 524: A timeout occurred', True),
    ('502 Bad Gateway', True),
    ('接口返回了空消息', True),
    ('Connection reset by peer', True),
    ('HTTP 413: 文本输入内容已超最大字符限制', False),   # 重试也是同样的输入
    ('401 Unauthorized', False),
    ('没有可用的 AI 接口', False),
    ('KeyError: foo', False),                           # 程序错，不是网络抖
]:
    check(f'{"重试" if expect else "不重试"}: {text}', central.is_transient(text) is expect)
check('413 里的数字不会被当成瞬时（永久判定在前）',
      central.is_transient('413 ... 500 ... timeout') is False)

finish()
