"""超时与重试：画图时间要算进模型调用超时，瞬时/永久故障分类要准。"""
from _harness import central, check, config, finish, fresh_env, section

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
