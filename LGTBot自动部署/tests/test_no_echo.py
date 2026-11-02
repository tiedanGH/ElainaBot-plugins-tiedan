# -*- coding: utf-8 -*-
"""群消息里绝不出现未经内容审核的用户输入 (1.18.3 / 1.18.4) —— 探针测试。

两种探针:
  · U「没审过的」: 昵称、模型自由发挥的文字 (说明、对不上原文的游戏名)、报错原文、拒收时的
    包内路径, 以及审核前 / 审核不通过 / force / 关闭审核时的文件名 —— 任何消息里都不许有;
  · R「过审的」: 审核通过的包的文件名、目录名、mygame.cc 里的游戏名 —— 只许出现在审核通过
    之后的消息里 (收到确认那条还没审, 也不许)。
跑的是真的 handle / _run / handle_recompile / _run_recompile, 收集 bot 发出的每一条群消息,
连同发给 LGTBot、会被它转发给玩家的计划重启原因。

新增任何群消息路径, 都要在这里补一个用例 (规则正本: 插件目录 CLAUDE.md)。
"""
import asyncio
import io
import os
import shutil
import zipfile

import _env

store, config, deploy, flow, compilemod, review, report = _env.boot(
    'store', 'config', 'deploy', 'flow', 'compile', 'review', 'report')
ck = _env.Checker()
store.init()

U = '违规探针'      # 没审过的: 任何时候都不许进群
R = 'rvok'          # 过审的 (文件名 / 目录名前缀): 只许出现在审核通过之后

GAMES = os.path.join(_env.SANDBOX, 'games')
os.makedirs(GAMES, exist_ok=True)
config.update({'upload_dir': GAMES, 'enabled': True, 'allowed_groups': ['g1'],
               'required_files': [], 'keep_archive': False, 'report_base_url': '',
               'review_enabled': True, 'compile_enabled': True, 'notify_users': ['DEV']})
TARGET = config.upload_target()

SENT = []                     # 所有群消息
REASONS = []                  # 请求计划重启时带的维护原因 (LGTBot 会发给玩家)


class Ev:
    group_id = 'g1'

    def __init__(self, user='u1', username=U + '昵称'):
        self.user_id = user
        self.username = username

    async def reply(self, text):
        SENT.append(text)
        return True

    async def send_to_group(self, gid, text):
        SENT.append(text)


def new(start):
    return SENT[start:]


def none_of(name, start, *words):
    """这一段新发出的消息里, words 一个都不出现 (且确实发了消息, 免得空跑也算过)。"""
    words = words or (U,)
    bad = [m for m in new(start) if any(w in m for w in words)]
    ck(name, len(SENT) > start and not bad, bad or '(没发任何消息)')


PACKAGE, REVIEW, NEXT, HOOKS = {}, [], [], {}
PASS = {'verdict': 'pass', 'categories': [], 'manual': False, 'model': 'fake', 'error': '',
        'http_status': 200, 'raw': '{}', 'elapsed': 0.1, 'criteria': [], 'findings': [],
        'game_name': U + '模型名', 'game_desc': U + '描述', 'retryable': True}


async def fake_download(url, max_bytes, timeout):
    data = PACKAGE['data']
    if isinstance(data, Exception):
        return None, f'下载异常: {type(data).__name__}: {data}'
    return data, ''


async def fake_review(pkg, meta, cfg):
    h = HOOKS.get('review')
    if h:
        h[0].set()
        await h[1].wait()
    res = REVIEW.pop(0) if REVIEW else PASS
    if isinstance(res, Exception):
        raise res
    return dict(res)


real_compile = compilemod.request_compile


async def fake_compile(game_name, cfg, is_new=False):
    h = HOOKS.get('compile')
    if h:
        h[0].set()
        await h[1].wait()
    if NEXT and NEXT[0] == 'real':
        NEXT.pop(0)
        return await real_compile(game_name, cfg, is_new)
    res = dict(NEXT.pop(0)) if NEXT else {'status': 'success', 'ok': True, 'http_status': 200}
    res['new'] = bool(is_new)
    return res


async def fake_restart(cfg, reason, auto=True):
    REASONS.append(reason)
    return {'status': 'success', 'ok': True, 'enabled': True, 'auto': True, 'reason': reason,
            'active_matches': 0, 'http_status': 200}


flow.quoted.download = fake_download
review.review = fake_review
compilemod.request_compile = fake_compile
compilemod.request_planned_restart = fake_restart


def mygame(name):
    """name 为 None 时 k_properties 不写字面量 (本地解析不出名字)。描述里放 U: 描述从不进群。"""
    if name is None:
        return ('const char* kName = "' + R + '模型名";\n'
                'const GameProperties k_properties { .name_ = kName, };\n')
    return ('const GameProperties k_properties { .name_ = "' + name + '", '
            '.developer_ = "x", .description_ = "' + U + '描述", };\n')


def mkzip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, text in files.items():
            zf.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), text)
    return buf.getvalue()


def game_zip(root, name='', extra=None):
    files = {f'{root}/mygame.cc': mygame(name), f'{root}/rule.md': '规则'}
    files.update(extra or {})
    return mkzip(files)


def run(fname, data, folder='', is_new=True, force=False, ev=None):
    PACKAGE['data'] = data
    asyncio.run(flow._run(ev or Ev(), config.all_config(), {'filename': fname, 'url': 'u'},
                          TARGET, folder, force, is_new))


async def _cancel_at(stage, coro):
    HOOKS[stage] = (asyncio.Event(), asyncio.Event())
    task = asyncio.ensure_future(coro)
    await asyncio.wait_for(HOOKS[stage][0].wait(), 10)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    HOOKS.pop(stage, None)


def run_cancel(stage, fname, data, is_new=True):
    PACKAGE['data'] = data
    asyncio.run(_cancel_at(stage, flow._run(Ev(), config.all_config(),
                                            {'filename': fname, 'url': 'u'},
                                            TARGET, '', False, is_new)))


def handle(filename, argline='', user='u1', force=False):
    saved = (flow.quoted.extract_file_ref, flow._spawn)
    spawned = []
    flow.quoted.extract_file_ref = lambda e, allow_any=False: {'url': 'u', 'filename': filename}
    flow._spawn = lambda coro: (coro.close(), spawned.append(1))[1]
    try:
        asyncio.run(flow.handle(Ev(user), argline, force))
    finally:
        flow.quoted.extract_file_ref, flow._spawn = saved
        flow._busy = False
    return spawned


def recompile(arg, user='u1'):
    saved = flow._spawn
    spawned = []
    flow._spawn = lambda coro: (spawned.append(coro), None)[1]
    try:
        asyncio.run(flow.handle_recompile(Ev(user), arg))
    finally:
        flow._spawn = saved
        flow._busy = False
    return spawned


print('\n[1] /upload 入口的拒收 (都在审核之前)')
for fname, argline, why in ((f'.{U}', 'demo', '隐藏文件'), (f'{U}.py', 'demo', '.py 文件'),
                            (f'{U}.txt', '', '不是压缩包'), (f'{U}..x.zip', '', '压缩包名不合法'),
                            ('mygame.cc', f'{U}..x', '文件夹名不合法'),
                            (f'{U}.zip', '', '新游戏目录名不合规范 (1.20.0)')):
    s = len(SENT)
    handle(fname, argline)
    none_of(f'{why} → 不复述名字', s)
os.makedirs(os.path.join(GAMES, U), exist_ok=True)
store.perm_set(U, 'someone', U + '绑定人昵称')
s = len(SENT)
handle(f'{U}.zip', user='u2')
none_of('目录已绑定给别人 → 不报目录名, 也不报绑定人昵称', s)
store.perm_delete(U)
s = len(SENT)
handle(f'{U}.zip', user='u2')
none_of('目录未绑定 → 不报目录名', s)
shutil.rmtree(os.path.join(GAMES, U))

print('\n[2] 审核通过之后: 过审的名字可以报, 没审过的照样不行')
s = len(SENT)
REASONS.clear()
run(f'{R}1.zip', game_zip(f'{R}1', '过审游戏名'))
ck('收到确认 (还没审) 不报文件名', R not in SENT[s] and U not in SENT[s], SENT[s])
none_of('全程没有没审过的 (昵称、模型回显的游戏名、描述)', s)
ck('  审核通过的结论报文件名、目录名与游戏名', any(f'{R}1.zip' in m and f'/ {R}1/' in m
                                           and '🎮 游戏名称: 过审游戏名' in m for m in new(s)), new(s))
ck('  编译成功消息带「游戏名(目录名)」', any(f'「过审游戏名({R}1)」' in m for m in new(s)), new(s))
ck('  计划重启的维护原因是「新游戏《游戏名》」', REASONS == ['新游戏《过审游戏名》'], REASONS)

s = len(SENT)
REVIEW[:] = [{**PASS, 'game_name': R + '模型名'}]
run(f'{R}2.zip', game_zip(f'{R}2', None))
ck('本地解析不出名字, 模型报的名字在 mygame.cc 原文里找得到 → 照报',
   any('🎮 游戏名称: ' + R + '模型名' in m for m in new(s)), new(s))
s = len(SENT)
REVIEW[:] = [{**PASS, 'game_name': U + '编的名字'}]
run(f'{R}3.zip', game_zip(f'{R}3', None))
none_of('  模型报的名字原文里没有 → 不报', s)

s = len(SENT)
NEXT[:] = ['real']                                     # 真的 request_compile: 中文目录名 → invalid
run('过审中文.zip', game_zip('过审中文', '过审中文名'))
ck('中文目录名过审了 → 部署结论里照报', any('过审中文' in m for m in new(s)[1:]), new(s))
ck('  收到确认那条照样不报', '过审中文' not in SENT[s], SENT[s])
none_of('  没审过的照样一个没有', s)

s = len(SENT)
NEXT[:] = [{'status': 'failed', 'ok': False, 'http_status': 409, 'error': '已有编译在进行中'}]
data4 = game_zip(f'{R}4', '过审四')
run(f'{R}4.zip', data4)
ck('临时性失败 → /compile 直接填好过审的目录名', any(f'/compile {R}4' in m for m in new(s)), new(s))
s = len(SENT)
run(f'{U}改了名.zip', data4)
said = '\n'.join(new(s))
ck('同内容换个文件名重传: 这次的文件名没送审, 不报; 引导用的是上次过审的目录名',
   U not in said and f'/compile {R}4' in said, new(s))

s = len(SENT)
coro = recompile(f'{R}4')
asyncio.run(coro[0])
ck('/compile 过审的目录 → 重编消息带「游戏名(目录名)」', any(f'「过审四({R}4)」' in m for m in new(s)), new(s))
none_of('  没审过的照样一个没有', s)

s = len(SENT)
run_cancel('compile', f'{R}5.zip', game_zip(f'{R}5', '过审五'))
ck('编译中被取消 (已过审) → 中断提示可以报文件名与目录名',
   any('处理被中断' in m and f'{R}5.zip' in m and f'/compile {R}5' in m for m in new(s)), new(s))

os.makedirs(os.path.join(GAMES, R + 'dir'), exist_ok=True)
s = len(SENT)
run(f'{R}.h', b'// header', folder=R + 'dir', is_new=False)
ck('单文件过审: 收到确认不报', R not in SENT[s], SENT[s])
ck('  之后报文件名与文件夹名', any(f'{R}.h' in m and f'lgtbot/{R}dir' in m for m in new(s)[1:]), new(s))

print('\n[3] 没审过的上传: 文件名、目录名、游戏名一个都不报')
s = len(SENT)
REASONS.clear()
run(f'{R}force.zip', game_zip(f'{R}force', '力'), force=True)
none_of('force 强制上传 (没审) → 不报', s, f'{R}force', '🎮', U)
ck('  维护原因只写「新游戏上线」', REASONS == ['新游戏上线'], REASONS)
config.update({'review_enabled': False})
try:
    s = len(SENT)
    run(f'{R}skip.zip', game_zip(f'{R}skip', '跳'))
finally:
    config.update({'review_enabled': True})
none_of('面板关闭审核 → 同样不报', s, f'{R}skip', '🎮', U)

s = len(SENT)
NEXT[:] = [{'status': 'failed', 'ok': False, 'http_status': 409, 'error': '已有编译在进行中'}]
data_ft = game_zip(f'{R}ft', '临')
run(f'{R}ft.zip', data_ft, force=True)
none_of('force 上传遇到临时性编译失败 → /compile 提示只给格式', s, f'{R}ft', U)
ck('  给的是 /compile <文件夹名>', any('/compile <文件夹名>' in m for m in new(s)), new(s))
s = len(SENT)
run(f'{U}原样.zip', data_ft)
none_of('  别人原样重传 → 拒收提示也不报那个没审过的目录名', s, f'{R}ft', U)

s = len(SENT)
REVIEW[:] = [{**PASS, 'verdict': 'reject', 'categories': ['content'], 'findings': [
    {'category': 'content', 'target': f'{U}目录/{U}.md', 'line': 3, 'reason': U + '说明'},
    {'category': 'content', 'target': f'{R}rej/mygame.cc', 'line': 9, 'reason': U}]}]
run(f'{R}rej.zip', game_zip(f'{R}rej', '拒'))
none_of('审核不通过 → 不报文件名, 违规位置只点名固定文件名', s, f'{R}rej', U)
ck('  固定文件名照样点名 (mygame.cc:9), 其余是「其他文件」',
   any('mygame.cc:9' in m and '其他文件:3' in m for m in new(s)), new(s))
ck('  网页报告可以照常写 (报告页不受限)', U in report.build_review(store.list_records(1)[0], {}))

s = len(SENT)
REVIEW[:] = [{**PASS, 'verdict': '', 'manual': True, 'retryable': False, 'error': 'HTTP 502',
              'http_status': 502}]
run(f'{R}svc.zip', game_zip(f'{R}svc', '服'))
none_of('审核服务异常 → 不报', s, f'{R}svc', U)

s = len(SENT)
run_cancel('review', f'{R}can.zip', game_zip(f'{R}can', '取'))
none_of('审核中被取消 → 中断提示不报文件名', s, f'{R}can', U)

for files, why in (({f'{U}3/mygame.cc': 'x', f'{U}3/.{U}': 'x'}, '隐藏文件成员'),
                   ({f'{U}3/mygame.cc': 'x', f'{U}3/tools/{U}.py': 'x'}, '.py 成员'),
                   ({f'../{U}': 'x'}, '路径穿越成员')):
    s = len(SENT)
    run(f'{U}3.zip', mkzip(files))
    none_of(f'压缩包含{why} → 拒收消息不带成员名与文件名', s)

s = len(SENT)
run(f'{U}9.zip', game_zip(f'{U}九', '九'))
none_of('压缩包名与包内文件夹名不一致 (1.20.0) → 只说不一致, 两个名字都不报', s, U, '九')

config.update({'text_budget': 10})
s = len(SENT)
run(f'{U}4.zip', game_zip(f'{U}4', '超', {f'{U}4/{U}大文件.txt': '字' * 200}))
none_of('送审文本超限 → 只报大小, 不报文件路径', s)
config.update({'text_budget': 150000})

s = len(SENT)
PACKAGE['data'] = RuntimeError(f'http://x/{U}.zip')
asyncio.run(flow._run(Ev(), config.all_config(), {'filename': f'{U}5.zip', 'url': 'u'},
                      TARGET, '', False, True))
none_of('下载异常 (原文里有带文件名的链接) → 只说下载异常', s)

s = len(SENT)
REVIEW[:] = [RuntimeError(f'{U} 在异常里')]
run(f'{U}6.zip', game_zip(f'{U}6', '异'))
none_of('流程里抛异常 → 不报异常原文', s)

real_move, real_copytree = shutil.move, shutil.copytree
os.makedirs(os.path.join(GAMES, R + '7'), exist_ok=True)


def bad_move(a, b, *x, **k):
    if os.path.basename(str(b)).startswith(R + '7') and 'backups' not in str(b):
        raise OSError(f'磁盘满了 {U}: {b}')
    return real_move(a, b, *x, **k)


def bad_copytree(a, b, *x, **k):
    raise OSError(f'还是满的 {U}: {b}')


shutil.move, shutil.copytree = bad_move, bad_copytree
try:
    s = len(SENT)
    run(f'{R}7.zip', game_zip(f'{R}7', '七'), is_new=False)
finally:
    shutil.move, shutil.copytree = real_move, real_copytree
none_of('部署写盘失败 → 不报 OSError 原文', s)

REVIEW[:] = [{**PASS, 'verdict': 'reject', 'categories': ['content']}]
data8 = game_zip(f'{U}8', '八')
run(f'{U}8.zip', data8)
s = len(SENT)
run(f'{U}8.zip', data8)
none_of('重复上传 (上次没过审) → 不复述文件名', s)

s = len(SENT)
NEXT[:] = [{'status': 'failed', 'ok': False, 'http_status': 400,
            'error': f"target 名称非法: '{R}400'(仅字母/数字/下划线/连字符)"}]
run(f'{R}400.zip', game_zip(f'{R}400', '四百'))
none_of('编译 API 的报错原文里带了目录名 → 替换掉再发 (报错原文不算过审内容)', s, f"'{R}400'", U)

print('\n[4] /compile 的拒绝回复 (指令参数是用户新打的字)')
for arg, user, why in ((U + '没传过', 'u1', '没有上传记录'), (f'{U}..x', 'u1', '目录名不合法'),
                       (f'{R}4', 'u9', '别人的目录')):
    s = len(SENT)
    recompile(arg, user)
    none_of(f'{why} → 不复述', s, U, arg)

print('\n[5] 兜底: 整轮下来')
ck(f'共 {len(SENT)} 条群消息, 没审过的探针一条都没有', not [m for m in SENT if U in m],
   [m for m in SENT if U in m][:3])
ck('维护原因里也没有', not [r for r in REASONS if U in r], REASONS)

ck.done()
