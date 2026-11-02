"""画图适配层（app/drawing.py）：能力发现、AI画图 1.7 接口、缓存与被打断后的复用。"""
import asyncio

from _harness import (
    FakeService, central, check, draw_entry, draw_payload, drawing, finish, fresh_env, run,
    section, use_service,
)

base = fresh_env()
ON = dict(base, draw_enabled=True, draw_cache_seconds=0, draw_timeout_seconds=180)

section('detach_image：链接不回灌给模型')
res = {'ok': True, 'url': 'https://bed/x.png', 'size': '1024x768', 'record_id': 7, 'model': 'm'}
image = drawing.detach_image(res)
check('摘出链接与尺寸', image == {'url': 'https://bed/x.png', 'size': '1024x768', 'reused': False},
      image)
check('回灌的结果里没有链接、尺寸、留档号',
      not ({'url', 'size', 'record_id'} & set(res)), res)
check('换成一句给模型的说明', '不要写链接' in res.get('note', ''), res)
res = {'ok': True, 'url': 'https://bed/x.png', 'reused': True}
check('复用标记透出来', drawing.detach_image(res)['reused'] is True)
check('复用时告诉模型没重画', '之前已经画好' in res.get('note', ''), res)
fail = {'ok': False, 'error': 'x'}
check('失败不算出图、也不加说明', drawing.detach_image(fail) == {} and 'note' not in fail)
check('ok 但没链接也不算出图', drawing.detach_image({'ok': True, 'url': ''}) == {})

section('能力发现与调用')
svc = FakeService()
use_service(svc)
out = run(drawing.run({'prompt': '一只猫'}, ON, 'u1'))
check('调用成功拿到链接', out['ok'] and out['url'] == 'https://bed/1.png', out)
sent = svc.calls[-1]
check('用发现来的能力键，不自己拼', sent['key'] == 'ai_draw:tool:draw', sent)
check('以本插件身份调用', sent['consumer'] == central.CONSUMER == 'lgtbot_qa', sent)
check('自报调用方供对方留档', sent['arguments']['caller'] == drawing.CALLER, sent)
check('描述原样传过去', sent['arguments']['prompt'] == '一只猫', sent)

run(drawing.run({'prompt': '猫' * 5000}, ON, 'u1'))
check('超长描述被截断', len(svc.calls[-1]['arguments']['prompt']) == drawing._PROMPT_MAX)
out = run(drawing.run({'prompt': '   '}, ON, 'u1'))
check('空描述直接拒，不打扰对方', out['ok'] is False and 'prompt' in out['error'], out)
out = run(drawing.run({'prompt': 'x'}, dict(ON, draw_enabled=False), 'u1'))
check('开关关着时拒绝', out['ok'] is False and '未开启' in out['error'], out)

section('AI画图 1.7 接口')
check('钉死只要一张', sent['arguments'].get('max_images') == 1, sent)
wait = sent['arguments'].get('wait_seconds')
check('传了 wait_seconds', wait is not None, sent)
check('wait_seconds 比自己的超时短（否则自己先走人，对方一张也交不出）',
      wait is not None and wait < ON['draw_timeout_seconds'], wait)
check('wait_seconds 落在对方允许的 10~280', wait is not None and 10 <= wait <= 280, wait)

use_service(FakeService(result={
    'ok': True, 'mode': 'single', 'count': 1, 'pending': 0,
    'images': [draw_entry('https://bed/array.png', 512, 512)], 'requested_size': '1024x1024'}))
out = run(drawing.run({'prompt': '只有数组'}, ON, 'u1'))
check('只有 images 数组（对方去掉镜像后）也能解析',
      out['ok'] and out['url'] == 'https://bed/array.png' and out['size'] == '512x512', out)

use_service(FakeService(result={'ok': True, 'url': 'https://bed/old.png', 'model': 'sd',
                                'width': 640, 'height': 480, 'requested_size': '1024x1024'}))
out = run(drawing.run({'prompt': '老返回体'}, ON, 'u1'))
check('只有顶层字段（1.5.x 老版）也能解析',
      out['ok'] and out['url'] == 'https://bed/old.png' and out['size'] == '640x480', out)

use_service(FakeService(result=draw_payload(
    [draw_entry('https://bed/m1.png'), draw_entry('https://bed/m2.png')], mode='multi')))
out = run(drawing.run({'prompt': '意外多图'}, ON, 'u1'))
check('对方意外给了多张时只取第一张', out['ok'] and out['url'] == 'https://bed/m1.png', out)

use_service(FakeService(result={'ok': False, 'error': '调用方已离开'}))
out = run(drawing.run({'prompt': '走人'}, ON, 'u1'))
check('「调用方已离开」翻译成人话再给模型',
      '调用方已离开' not in out['error'] and '超时' in out['error'], out)

section('像素尺寸')
for label, extra, want in [
    ('实测宽高优先于请求尺寸', {'width': 768, 'height': 1344, 'requested_size': '1024x1024'}, '768x1344'),
    ('没实测值时退回请求尺寸', {'requested_size': '1152x896'}, '1152x896'),
    ('宽高为 0 不当真', {'width': 0, 'height': 0, 'requested_size': '512x512'}, '512x512'),
    ('认 1.5.0 的旧键名 size', {'size': '640x480'}, '640x480'),
    ('全都没有就交给默认值', {}, ''),
    ('尺寸是乱写的也不炸', {'requested_size': 'large'}, ''),
]:
    check(label, drawing._dimensions(extra) == want, drawing._dimensions(extra))

section('失败原因透给模型')
use_service(FakeService(result={'ok': False, 'error': '描述命中违规词：xx'}))
out = run(drawing.run({'prompt': 'a'}, ON, 'u1'))
check('对方的拒绝原因原样透出', out['ok'] is False and '违规词' in out['error'], out)
use_service(FakeService(boom=RuntimeError('接口炸了')))
out = run(drawing.run({'prompt': 'b'}, ON, 'u1'))
check('异常收敛成 ok=false', out['ok'] is False and '接口炸了' in out['error'], out)
use_service(FakeService(result={'ok': True, 'url': ''}))
out = run(drawing.run({'prompt': 'c'}, ON, 'u1'))
check('出图了却没链接算失败', out['ok'] is False, out)
use_service(FakeService(delay=0.3))
out = run(drawing.run({'prompt': 'd'}, dict(ON, draw_timeout_seconds=0.05), 'u1'))
check('超时被兜住', out['ok'] is False and '超时' in out['error'], out)
use_service(FakeService(shared=False))
out = run(drawing.run({'prompt': 'e'}, ON, 'u1'))
check('未授权时说清怎么改', out['ok'] is False and 'allowed_consumers' in out['error'], out)

section('面板诊断：四种不可用分开说')
use_service(None)
check('没有中央模块', drawing.state()['usable'] is False and 'AI LLM' in drawing.state()['message'])
for label, kwargs, word in [
    ('AI画图 没注册', {'present': False}, '未检测到'),
    ('能力不在线', {'online': False}, '不在线'),
    ('被停用', {'enabled': False}, '停用'),
    ('没共享给本插件', {'shared': False}, '未授权'),
]:
    use_service(FakeService(**kwargs))
    state = drawing.state()
    check(label, state['usable'] is False and word in state['message'], state)
use_service(FakeService())
check('一切正常时就绪', drawing.state()['usable'] is True, drawing.state())

section('被打断也不重画（shield + 落盘缓存）')
CACHED = dict(ON, draw_cache_seconds=3600)
slow = FakeService(delay=0.35, result=draw_payload([draw_entry('https://bed/slow.png')]))
use_service(slow)


async def interrupted_then_retry():
    first = asyncio.ensure_future(drawing.run({'prompt': '一只橘猫'}, CACHED, 'u9'))
    await asyncio.sleep(0.05)
    first.cancel()                     # 就是生产日志里 error=interrupted 的那一刻
    try:
        await first
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.8)           # 后台任务不受影响，继续画完并落盘
    again = await drawing.run({'prompt': '一只橘猫'}, CACHED, 'u9')
    other = await drawing.run({'prompt': '一只橘猫'}, CACHED, 'u8')
    return again, other


again, other = run(interrupted_then_retry())
check('被取消后图仍画完、重试直接复用', again['ok'] and again.get('reused') is True, again)
check('复用拿到的是同一张', again['url'] == 'https://bed/slow.png', again)
check('换个人问同样的描述，不会拿到别人那张', other.get('reused') is not True, other)
check('u9 只真画了一次（另一次是换人那次）', len(slow.calls) == 2, len(slow.calls))
check('reusable 认得缓存', run(drawing.reusable({'prompt': '一只橘猫'}, CACHED, 'u9')) is True)
check('没画过的不算可复用', run(drawing.reusable({'prompt': '没画过'}, CACHED, 'u9')) is False)
check('缓存关掉就不复用',
      run(drawing.reusable({'prompt': '一只橘猫'}, dict(CACHED, draw_cache_seconds=0), 'u9'))
      is False)


async def concurrent_same_prompt():
    svc = FakeService(delay=0.3)
    use_service(svc)
    nocache = dict(ON, draw_cache_seconds=0)
    pair = await asyncio.gather(drawing.run({'prompt': '同时画'}, nocache, 'u7'),
                                drawing.run({'prompt': '同时画'}, nocache, 'u7'))
    return svc, pair


svc7, pair = run(concurrent_same_prompt())
check('同一个人并发同描述只画一张', len(svc7.calls) == 1, len(svc7.calls))
check('两边都拿到图', all(item['ok'] for item in pair), pair)

finish()
