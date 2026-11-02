# -*- coding: utf-8 -*-
"""内容查重 (1.18.2) —— 只有对内容下过结论的记录才拦同内容重传 (flow._settled)。

审核不通过、编译失败、包不合规、已部署成功 → 拦 (再来一遍结论不会变);
任务被取消、处理异常、下载失败、部署写盘出错、审核服务异常 → 不拦 (插件 / 服务自己的状况,
内容没得出结论, 重传正是出路); 重复上传被拒的记录跳过, 找到真正的结论。
跑的是真的 flow._run, 下载 / 审核 / 编译 API 用假函数顶替。
"""
import asyncio
import io
import os
import time
import zipfile

import _env

store, config, flow, compilemod, review = _env.boot('store', 'config', 'flow', 'compile', 'review')
ck = _env.Checker()
store.init()

GAMES = os.path.join(_env.SANDBOX, 'games')
os.makedirs(GAMES, exist_ok=True)
config.update({'upload_dir': GAMES, 'enabled': True, 'allowed_groups': ['g1'],
               'required_files': [], 'keep_archive': False, 'report_base_url': '',
               'review_enabled': True, 'compile_enabled': True, 'notify_users': []})
TARGET = config.upload_target()

print('\n[1] _settled: 哪些记录算对内容下过结论')
C = flow._CANCELLED
for rec, want, why in (
        ({'stage': 'review', 'verdict': 'reject'}, True, '审核不通过'),
        ({'stage': 'deployed', 'compile': {'status': 'failed', 'returncode': 2}}, True, '编译器报错'),
        ({'stage': 'deployed', 'compile': {'status': 'failed', 'http_status': 409}}, True,
         '临时性编译失败 (拦下, 引导 /compile)'),
        ({'stage': 'deployed', 'compile': {'status': 'success', 'ok': True}}, True, '已部署成功'),
        ({'stage': 'recompile', 'compile': {'status': 'success', 'ok': True}}, True, '重编成功'),
        ({'stage': 'hidden'}, True, '含隐藏文件'), ({'stage': 'script'}, True, '含 .py'),
        ({'stage': 'integrity'}, True, '缺必需文件'), ({'stage': 'extract'}, True, '解压校验失败'),
        ({'stage': 'cancelled', 'error': C}, False, '任务被取消 (审核中)'),
        ({'stage': 'deployed', 'cancelled': True, 'compile': {'status': 'error'}}, False,
         '任务被取消 (已落地, 收拾成暂存)'),
        ({'stage': 'recompile', 'error': C}, False, '旧版本留下的被取消的重编'),
        ({'stage': 'error'}, False, '处理异常'), ({'stage': 'download'}, False, '下载失败'),
        ({'stage': 'deploy'}, False, '部署写盘出错'),
        ({'stage': 'review', 'manual': True}, False, '审核服务异常'),
        ({'stage': 'duplicate'}, False, '重复上传被拒'),
        ({'stage': 'name'}, False, '名称不一致 (拦的是压缩包名, 改名重传字节不变)')):
    ck(f'{why} → {"拦" if want else "不拦"}', flow._settled(rec) is want, rec)

print('\n[2] find_by_sha 跳过没结论的, 找到真正的结论')
for rid, stage, extra in (('20990101-000001-aaaa', 'review', {'verdict': 'reject'}),
                          ('20990101-000002-bbbb', 'duplicate', {}),
                          ('20990101-000003-cccc', 'cancelled', {'error': C})):
    store.add_record({'id': rid, 'sha256': 'S' * 64, 'stage': stage, **extra})
got = store.find_by_sha('S' * 64, accept=flow._settled)
ck('越过「被取消」「重复上传」两条, 找到审核不通过那条', (got or {}).get('id') == '20990101-000001-aaaa', got)
ck('不给 accept 还是原来的行为 (最近一条)', store.find_by_sha('S' * 64)['id'] == '20990101-000003-cccc')
store.add_record({'id': '20990101-000004-dddd', 'sha256': 'T' * 64, 'stage': 'cancelled', 'error': C})
ck('只有被取消的记录 → 当没传过', store.find_by_sha('T' * 64, accept=flow._settled) is None)


# ---- 端到端: 真的 _run, 下载 / 审核 / 编译用假函数 ----
class Ev:
    group_id, user_id, username = 'g1', 'u1', '上传者'

    def __init__(self):
        self.sent = []

    async def reply(self, text):
        self.sent.append(text)
        return True

    async def send_to_group(self, gid, text):
        self.sent.append(text)


PACKAGE, REVIEW, NEXT, HOOKS = {}, [], [], {}
PASS = {'verdict': 'pass', 'categories': [], 'manual': False, 'model': 'fake', 'error': '',
        'http_status': 200, 'raw': '{}', 'elapsed': 0.1, 'criteria': [], 'findings': [],
        'game_name': '', 'game_desc': '', 'retryable': True}
REJECT = {**PASS, 'verdict': 'reject', 'categories': ['content'],
          'findings': [{'category': 'content', 'target': 'mygame.cc', 'line': 3}]}
SERVICE_DOWN = {**PASS, 'verdict': '', 'manual': True, 'retryable': False,
                'error': 'HTTP 502', 'http_status': 502}


async def _hook(name):
    h = HOOKS.get(name)
    if h:
        h[0].set()
        await h[1].wait()


async def fake_download(url, max_bytes, timeout):
    return PACKAGE['data'], ''


async def fake_review(pkg, meta, cfg):
    await _hook('review')
    return dict(REVIEW.pop(0) if REVIEW else PASS)


async def fake_compile(game_name, cfg, is_new=False):
    await _hook('compile')
    res = dict(NEXT.pop(0)) if NEXT else {'status': 'success', 'ok': True, 'http_status': 200}
    res['new'] = bool(is_new)
    return res


async def fake_restart(cfg, reason, auto=True):
    return {'status': 'success', 'ok': True, 'enabled': True, 'auto': True, 'reason': reason,
            'active_matches': 0, 'http_status': 200}


flow.quoted.download = fake_download
review.review = fake_review
compilemod.request_compile = fake_compile
compilemod.request_planned_restart = fake_restart


def mkzip(root, ver, extra=()):
    """同样的参数必须打出逐字节相同的包 —— writestr(名字, …) 会把当前时间写进去,
    两次打包跨过一秒 sha 就变了, 「原样重传」就测成了「改过再传」。时间戳钉死。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, text in ([('mygame.cc', ver), ('rule.md', 'rule ' + ver)]
                           + [(n, 'x') for n in extra]):
            zf.writestr(zipfile.ZipInfo(f'{root}/{name}', date_time=(2026, 1, 1, 0, 0, 0)), text)
    return buf.getvalue()


async def _cancel_at(stage, coro):
    HOOKS.clear()
    HOOKS[stage] = (asyncio.Event(), asyncio.Event())
    task = asyncio.ensure_future(coro)
    await asyncio.wait_for(HOOKS[stage][0].wait(), 10)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    HOOKS.clear()


def upload(name, ver, is_new=True, cancel_at=None, extra=()):
    PACKAGE['data'] = mkzip(name, ver, extra)
    ev = Ev()
    coro = flow._run(ev, config.all_config(), {'filename': name + '.zip', 'url': 'u'},
                     TARGET, '', False, is_new)
    asyncio.run(_cancel_at(cancel_at, coro) if cancel_at else coro)
    return ev, store.list_records(1)[0]


def dup_said(ev):
    return next((m for m in ev.sent if '重复上传, 已拒收' in m), '')


_a = mkzip('z', 'v')
time.sleep(1.1)
ck('打包可复现 (隔一秒再打, 字节一样)', _a == mkzip('z', 'v'))

print('\n[3] 上次被插件意外打断 → 同一个包照样能传')
ev, r = upload('d_cancel', 'n1', cancel_at='review')
ck('先在审核时被取消', r['stage'] == 'cancelled')
ev, r = upload('d_cancel', 'n1')
ck('原样重传: 不算重复, 走完了全流程', not dup_said(ev) and r['stage'] == 'deployed'
   and r['compile'].get('ok'), (ev.sent, r.get('stage')))

ev, r = upload('d_landed', 'n1', cancel_at='compile')
ck('编译中被取消 (已落地, 收拾成暂存)', r.get('cancelled') and store.pending_of(r))
first = r
ev, r = upload('d_landed', 'n1')
ck('原样重传也放行, 编译成功', not dup_said(ev) and r['compile'].get('ok'), ev.sent)
ck('  上一份暂存作废删掉了', not store.pending_of(first))

REVIEW[:] = [SERVICE_DOWN]
ev, r = upload('d_svc', 'n1')
ck('审核服务异常 (重试耗尽)', r.get('manual'), r)
ev, r = upload('d_svc', 'n1')
ck('原样重传放行, 这回过审', not dup_said(ev) and r['stage'] == 'deployed', ev.sent)

print('\n[4] 上次对内容下过结论 → 拦, 并说出真正的结论')
REVIEW[:] = [REJECT]
ev, r = upload('d_rej', 'n1')
ck('先审核不通过', r.get('verdict') == 'reject')
ev, r = upload('d_rej', 'n1')
said = dup_said(ev)
ck('原样重传: 拒收, 旧结论是审核未通过', '审核未通过' in said, ev.sent)
ev, r = upload('d_rej', 'n1')
said = dup_said(ev)
ck('第三次: 还是指着审核未通过那条, 不是「重复上传被拒」',
   '审核未通过' in said and '重复上传被拒' not in said, said)

game_dir = os.path.join(GAMES, 'd_code')
os.makedirs(game_dir, exist_ok=True)
with open(os.path.join(game_dir, 'mygame.cc'), 'w', encoding='utf-8') as f:
    f.write('v1')
NEXT[:] = [{'status': 'failed', 'ok': False, 'http_status': 500, 'returncode': 2,
            'error': '编译失败 (退出码 2)'}]
ev, r = upload('d_code', 'v2', is_new=False)
ev, r = upload('d_code', 'v2', is_new=False)
ck('编译器报错过 → 拒收, 让他改了再传', '修改后再上传' in dup_said(ev), ev.sent)

NEXT[:] = [{'status': 'failed', 'ok': False, 'http_status': 409, 'error': '已有编译在进行中'}]
ev, r = upload('d_busy', 'n1')
ev, r = upload('d_busy', 'n1')
ck('临时性编译失败 → 拒收, 引导 /compile (上次过审, 目录名直接填好)',
   '/compile d_busy' in dup_said(ev), ev.sent)

ev, r = upload('d_ok', 'n1')
ev, r = upload('d_ok', 'n1')
ck('已经部署成功 → 拒收', bool(dup_said(ev)), ev.sent)

ev, r = upload('d_py', 'n1', extra=('tools/gen.py',))
ck('含 .py 被拒', r['stage'] == 'script')
ev, r = upload('d_py', 'n1', extra=('tools/gen.py',))
ck('原样重传 → 拒收 (包本身不合规, 结论不会变)', '含 .py 文件被拒' in dup_said(ev), ev.sent)

print('\n[5] 上传方式 (压缩包 / 单文件) 不同不算重复 (1.20.0)')


def upload_file(folder, fname, data):
    """单文件方式: 引用的就是 data 这个文件, 写进已有的 folder。"""
    PACKAGE['data'] = data
    ev = Ev()
    asyncio.run(flow._run(ev, config.all_config(), {'filename': fname, 'url': 'u'},
                          TARGET, folder, False, False))
    return ev, store.list_records(1)[0]


os.makedirs(os.path.join(GAMES, 'd_mode'), exist_ok=True)
whole = mkzip('d_mode', 'm1')
ev, r = upload_file('d_mode', 'd_mode.zip', whole)
ck('先把整个游戏的压缩包当单文件传了 (成了目录里的一个文件, 已部署)',
   r['mode'] == 'file' and r['stage'] == 'deployed', (r.get('mode'), r.get('stage')))
ev, r = upload('d_mode', 'm1', is_new=False)
ck('换成压缩包方式重传同一个包 → 不算重复, 走完全流程',
   not dup_said(ev) and r['mode'] == 'archive' and r['stage'] == 'deployed', ev.sent)
ck('  这回解压进了游戏目录', os.path.isfile(os.path.join(GAMES, 'd_mode', 'mygame.cc')))
ev, r = upload('d_mode', 'm1', is_new=False)
ck('压缩包方式再传一次 → 拦', bool(dup_said(ev)), ev.sent)
ev, r = upload_file('d_mode', 'd_mode.zip', whole)
ck('单文件方式再传一次 → 也拦 (对上的是上次单文件那条)', bool(dup_said(ev)), ev.sent)

REVIEW[:] = [REJECT]
ev, r = upload_file('d_mode', 'notes.md', b'# notes v1')
ck('单文件审核不通过', r.get('verdict') == 'reject', r.get('verdict'))
ev, r = upload_file('d_mode', 'notes.md', b'# notes v1')
ck('  同样方式原样重传 → 照样拦 (老规矩不变)', '审核未通过' in dup_said(ev), ev.sent)

ck.done()
