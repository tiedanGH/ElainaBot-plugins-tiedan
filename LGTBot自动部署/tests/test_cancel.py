# -*- coding: utf-8 -*-
"""任务被取消 (插件热重载 / 机器人重启) 时的收尾 (1.18.1) —— flow._cancelled。

跑的是真的 flow._run / flow._run_recompile, 只把下载、审核、编译 API 换成可以「卡住」的
假函数, 在各个环节把任务取消, 看收尾对不对:
  · 还在审核 → stage='cancelled', 什么都没落地, 群里说一句;
  · 已落地、编译中 → 按临时性失败收拾: games/ 恢复原状、新代码进暂存, /compile 能接着编;
  · 落地进行到一半 → 落地照样做完 (线程停不下来), 再按上一条收拾;
  · 编译结论已到手 → 收尾做完 (落盘 + 回报), 不补写同号记录、不发「中断」;
  · /compile 换回暂存后编译中 → 重新暂存到这条记录名下, 下次 /compile 还够得着。
"""
import asyncio
import io
import os
import threading
import time
import zipfile

import _env

store, config, deploy, flow, compilemod, review = _env.boot(
    'store', 'config', 'deploy', 'flow', 'compile', 'review')
ck = _env.Checker()
store.init()

GAMES = os.path.join(_env.SANDBOX, 'games')
os.makedirs(GAMES, exist_ok=True)
config.update({'upload_dir': GAMES, 'enabled': True, 'allowed_groups': ['g1'],
               'required_files': [], 'keep_archive': False, 'report_base_url': '',
               'review_enabled': True, 'compile_enabled': True, 'notify_users': []})
TARGET = config.upload_target()


def mkzip(root, ver):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr(f'{root}/mygame.cc', ver)
        zf.writestr(f'{root}/rule.md', 'rule ' + ver)
    return buf.getvalue()


def live(name):
    try:
        with open(os.path.join(GAMES, name, 'mygame.cc'), encoding='utf-8') as f:
            return f.read()
    except OSError:
        return None


def game(name, ver):
    d = os.path.join(GAMES, name)
    os.makedirs(d, exist_ok=True)
    for fn, text in (('mygame.cc', ver), ('rule.md', 'rule ' + ver)):
        with open(os.path.join(d, fn), 'w', encoding='utf-8') as f:
            f.write(text)


def lines_of(rid):
    return [r for r in store.list_records(1000) if r.get('id') == rid]


class Ev:
    group_id, username = 'g1', '上传者'

    def __init__(self, user='u1'):
        self.user_id = user
        self.sent = []

    async def reply(self, text):
        self.sent.append(text)
        return True

    async def send_to_group(self, gid, text):
        self.sent.append(text)


# ---- 可以卡住的假函数: HOOKS[名字] = (进来了, 放行), 都是 asyncio.Event ----
HOOKS = {}
PACKAGE = {}
NEXT = []
RESTARTS = []
PASS = {'verdict': 'pass', 'categories': [], 'manual': False, 'model': 'fake', 'error': '',
        'http_status': 200, 'raw': '{}', 'elapsed': 0.1, 'criteria': [], 'findings': [],
        'game_name': '', 'game_desc': '', 'retryable': True}


async def _hook(name):
    h = HOOKS.get(name)
    if h:
        h[0].set()
        await h[1].wait()


async def fake_download(url, max_bytes, timeout):
    return PACKAGE['data'], ''


async def fake_review(pkg, meta, cfg):
    await _hook('review')
    return dict(PASS)


async def fake_compile(game_name, cfg, is_new=False):
    await _hook('compile')
    res = dict(NEXT.pop(0)) if NEXT else {'status': 'success', 'ok': True, 'http_status': 200}
    res['new'] = bool(is_new)
    return res


async def fake_restart(cfg, reason, auto=True):
    await _hook('restart')
    RESTARTS.append(reason)
    return {'status': 'success', 'ok': True, 'enabled': True, 'auto': True,
            'reason': reason, 'active_matches': 0, 'http_status': 200}


flow.quoted.download = fake_download
review.review = fake_review
compilemod.request_compile = fake_compile
compilemod.request_planned_restart = fake_restart


async def cancel_at(stage, coro, release_after=False):
    """跑 coro, 等它卡在 stage 就取消; release_after=True 时取消后再放行 (给被 shield 的收尾用)。"""
    HOOKS.clear()
    HOOKS[stage] = (asyncio.Event(), asyncio.Event())
    task = asyncio.ensure_future(coro)
    await asyncio.wait_for(HOOKS[stage][0].wait(), 10)
    task.cancel()
    if release_after:
        await asyncio.sleep(0.05)
        HOOKS[stage][1].set()
    try:
        await task
    except asyncio.CancelledError:
        pass
    HOOKS.clear()
    return task


def upload(name, ver, is_new, stage=None, release_after=False):
    """走一遍真的 _run; stage 给定就卡在那儿取消。返回 (事件, 该次记录的全部行)。"""
    PACKAGE['data'] = mkzip(name, ver)
    ev = Ev()
    before = {r['id'] for r in store.list_records(1000)}
    coro = flow._run(ev, config.all_config(), {'filename': name + '.zip', 'url': 'u'},
                     TARGET, '', False, is_new)
    if stage:
        asyncio.run(cancel_at(stage, coro, release_after))
    else:
        asyncio.run(coro)
    rid = next(r['id'] for r in store.list_records(1000) if r['id'] not in before)
    return ev, lines_of(rid)


print('\n[1] 还在审核时被取消')
flow._busy = True                     # handle() 受理时置的上传锁, 收尾必须放开
ev, rows = upload('c_review', 'n1', True, 'review')
ck('只落一条记录, stage=cancelled', len(rows) == 1 and rows[0]['stage'] == 'cancelled'
   and rows[0]['error'] == flow._CANCELLED, rows)
ck('  什么都没落地', not os.path.exists(os.path.join(GAMES, 'c_review')))
ck('  解压暂存清掉了', not os.path.exists(os.path.join(store.STAGING_DIR, rows[0]['id'])))
ck('  群里说了一句, 让他重新上传', any('处理被中断' in m and '请稍后重新上传' in m for m in ev.sent),
   ev.sent)
ck('  上传锁放开了', flow._busy is False)

print('\n[2] 已落地、编译中被取消 (新游戏)')
ev, rows = upload('c_comp', 'n1', True, 'compile')
r = rows[-1] if rows else {}
ck('只落一条记录, 仍是「已部署」+ 编译异常 (临时性)', len(rows) == 1 and r.get('stage') == 'deployed'
   and r['compile'].get('status') == 'error' and compilemod.is_transient(r['compile']), rows)
ck('  新游戏目录先撤下, 新代码进了暂存', not os.path.exists(os.path.join(GAMES, 'c_comp'))
   and store.pending_of(r), r.get('rollback'))
ck('  绑定留着 (名字留给 /compile)', (store.perm_get('c_comp') or {}).get('user_id') == 'u1')
ck('  群里说了 /compile 能接着编 (过审了, 目录名直接填好)',
   any('处理被中断' in m and '/compile c_comp' in m and '新代码已暂存待重编' in m for m in ev.sent),
   ev.sent)
NEXT[:] = [{'status': 'success', 'ok': True, 'http_status': 200}]
RESTARTS.clear()
asyncio.run(flow._run_recompile(Ev(), config.all_config(), 'c_comp', store.last_game_record('c_comp')))
ck('  /compile: 换回去编, 成功并请求计划重启', live('c_comp') == 'n1' and RESTARTS
   and store.list_records(1)[0]['compile'].get('ok'))

print('\n[3] 落地进行到一半被取消 (老游戏)')
game('c_land', 'v1')
real_da = deploy.deploy_archive
in_thread, go = threading.Event(), threading.Event()


def slow_deploy(*a, **k):
    in_thread.set()
    go.wait(10)
    return real_da(*a, **k)


async def cancel_in_land():
    PACKAGE['data'] = mkzip('c_land', 'v2')
    ev = Ev()
    task = asyncio.ensure_future(flow._run(ev, config.all_config(),
                                           {'filename': 'c_land.zip', 'url': 'u'},
                                           TARGET, '', False, False))
    while not in_thread.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.05)
    go.set()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return ev


deploy.deploy_archive = slow_deploy
try:
    ev = asyncio.run(cancel_in_land())
finally:
    deploy.deploy_archive = real_da
r = store.list_records(1)[0]
ck('落地照样做完了、记录知道 (stage=deployed)', r['stage'] == 'deployed' and r.get('dest'), r)
ck('  按临时性失败收拾: 上一版本放回, 新代码暂存', live('c_land') == 'v1'
   and store.pending_of(r), r.get('rollback'))
ck('  群里说了 /compile', any('/compile c_land' in m for m in ev.sent), ev.sent)

print('\n[4] 编译结论到手之后被取消 (新游戏, 卡在请求计划重启)')
RESTARTS.clear()
ev, rows = upload('c_tail', 'n1', True, 'restart', release_after=True)
r = rows[-1] if rows else {}
ck('收尾做完了: 只落一条记录, 编译成功 + 已请求重启', len(rows) == 1 and r['compile'].get('ok')
   and r['restart'].get('ok') and RESTARTS, rows)
ck('  新代码留在服务器上 (没被当成中断收拾)', live('c_tail') == 'n1' and not r.get('rollback'))
ck('  回报的是编译成功, 没发「中断」', any('编译成功' in m for m in ev.sent)
   and not any('处理被中断' in m for m in ev.sent), ev.sent)

print('\n[5] /compile 换回暂存后、编译中被取消')
NEXT[:] = [{'status': 'failed', 'ok': False, 'http_status': 409, 'error': '已有编译在进行中'}]
ev, rows = upload('c_re', 'n1', True)
first = rows[-1]
ck('先来一次临时性失败 → 有暂存', store.pending_of(first) != '')
last = store.last_game_record('c_re')
ev2 = Ev()
asyncio.run(cancel_at('compile', flow._run_recompile(ev2, config.all_config(), 'c_re', last)))
re_ = store.list_records(1)[0]
ck('重编记录登记了新的暂存 (在它自己名下)', re_['stage'] == 'recompile'
   and re_['id'] in (re_.get('rollback') or {}).get('pending', '') and store.pending_of(re_), re_)
ck('  games/ 又恢复成没有这个目录', not os.path.exists(os.path.join(GAMES, 'c_re')))
ck('  群里说了 /compile', any('/compile c_re' in m for m in ev2.sent), ev2.sent)
NEXT[:] = [{'status': 'success', 'ok': True, 'http_status': 200}]
asyncio.run(flow._run_recompile(Ev(), config.all_config(), 'c_re', store.last_game_record('c_re')))
ck('  再 /compile 一次就编成了', live('c_re') == 'n1' and store.list_records(1)[0]['compile'].get('ok'))

print('\n[6] 发消息卡住也不拖住收尾')
real_send = Ev.send_to_group


async def stuck(self, gid, text):
    await asyncio.sleep(3600)


Ev.send_to_group = stuck
t0 = time.time()
try:
    ev, rows = upload('c_mute', 'n1', True, 'review')
finally:
    Ev.send_to_group = real_send
ck('记录照落, 最多等几秒就放弃发送', rows and rows[0]['stage'] == 'cancelled' and time.time() - t0 < 30,
   time.time() - t0)

ck.done()
