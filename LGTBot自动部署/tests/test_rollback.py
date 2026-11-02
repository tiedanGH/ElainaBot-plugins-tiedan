# -*- coding: utf-8 -*-
"""编译失败回滚 (1.18.0) —— deploy.rollback / flow._settle / /compile 换回暂存。

要守住的:
1. 编译没成, games/ 下不留新代码: 老游戏放回上一版本, 新游戏整个目录移除;
2. 还能 /compile 的失败 (临时问题 / 接口配错) 新代码挪进 data/pending, /compile 换回去
   再编; 不能的 (编译器报错 / 目标名非法) 直接撤掉 —— 判据与 /compile 放不放行是同一个;
3. 回滚只动上传目录下一层 / 两层的那一个路径; 备份找不到就一个文件都不动;
4. 新游戏撤掉后下次上传仍判为新游戏 (目录不在), 自动加的绑定收回;
   暂存期间名字仍只许绑定用户来传;
5. 面板没开「保留旧版本」时旧版本也先留着, 编译成功才删。
"""
import asyncio
import os
import shutil
import time

import _env

store, config, deploy, flow, compilemod, report = _env.boot(
    'store', 'config', 'deploy', 'flow', 'compile', 'report')
ck = _env.Checker()
store.init()

GAMES = os.path.join(_env.SANDBOX, 'games')
STG = os.path.join(_env.SANDBOX, 'stg')
os.makedirs(GAMES, exist_ok=True)
config.update({'upload_dir': GAMES, 'enabled': True, 'allowed_groups': ['g1'],
               'keep_replaced_backup': True, 'compile_enabled': True, 'report_base_url': ''})
TARGET = config.upload_target()
_n = [0]


def cfg(**kw):
    return {**config.all_config(), **kw}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return None


def game(name, ver):
    """games/<name>/ 放一份版本为 ver 的游戏 (mygame.cc + rule.md)。"""
    d = os.path.join(GAMES, name)
    shutil.rmtree(d, ignore_errors=True)
    write(os.path.join(d, 'mygame.cc'), ver)
    write(os.path.join(d, 'rule.md'), 'rule ' + ver)
    return d


def staged(ver):
    """一份待落地的解压结果 (root 为空, 整个目录挪过去)。"""
    _n[0] += 1
    d = os.path.join(STG, str(_n[0]))
    write(os.path.join(d, 'mygame.cc'), ver)
    write(os.path.join(d, 'rule.md'), 'rule ' + ver)
    return d


def live(name):
    return read(os.path.join(GAMES, name, 'mygame.cc'))


R_OK = {'status': 'success', 'ok': True, 'http_status': 200, 'elapsed_sec': 3.0}
R_CODE = {'status': 'failed', 'ok': False, 'http_status': 500, 'returncode': 2,
          'error': '编译失败 (退出码 2)', 'log_tail': 'mygame.cc:1: error'}
R_BUSY = {'status': 'failed', 'ok': False, 'http_status': 409, 'returncode': None,
          'error': '已有编译在进行中'}
R_TIMEOUT = {'status': 'timeout', 'ok': False, 'terminate': {'ok': True}}
R_INVALID = {'status': 'invalid', 'ok': False, 'error': '目标名不符合'}
R_MISCONF = {'status': 'misconfig', 'ok': False, 'error': '地址不是完整 URL'}


print('\n[1] 判据: 只剩重传 ⇔ /compile 不放行')
for name, res, want in (
        ('编译器报错 (500 + 退出码)', R_CODE, True),
        ('目标名非法', R_INVALID, True),
        ('401 token 错 (非临时)', {'status': 'failed', 'ok': False, 'http_status': 401}, True),
        ('500 无退出码 (进程被终止)', {'status': 'failed', 'ok': False, 'http_status': 500}, True),
        ('409 编译进程被占用', R_BUSY, False),
        ('503 服务未就绪', {'status': 'failed', 'ok': False, 'http_status': 503}, False),
        ('本地等待超时', R_TIMEOUT, False),
        ('网络异常', {'status': 'error', 'ok': False}, False),
        ('接口地址配错', R_MISCONF, False),
        ('成功', R_OK, False)):
    ck(f'{name} → {"只能重传" if want else "可 /compile"}', compilemod.needs_reupload(res) is want)

print('\n[2] 落地: 被替换的旧版本一律先挪进 backups')
game('upd', 'v1')
res = deploy.deploy_archive(staged('v2'), '', 'upd', TARGET, cfg(keep_replaced_backup=False))
ck('落地成功、新代码在位', res['ok'] and live('upd') == 'v2', res)
ck('  没开备份也留了旧版本 (回滚要用)', read(os.path.join(res['backup'], 'mygame.cc')) == 'v1', res)
ck('  标记为临时备份', res['replaced'] is True and res['backup_temp'] is True)
res2 = deploy.deploy_archive(staged('v3'), '', 'upd', TARGET, cfg(keep_replaced_backup=True))
ck('开着备份 → 不是临时的', res2['ok'] and res2['backup_temp'] is False)
res3 = deploy.deploy_archive(staged('n1'), '', 'brand_new', TARGET, cfg())
ck('新游戏 → replaced=False、没有备份', res3['ok'] and res3['replaced'] is False
   and res3['backup'] == '')

print('\n[3] rollback 三种收尾')
game('rb', 'v1')
os.utime(os.path.join(GAMES, 'rb', 'mygame.cc'), (1e9, 1e9))     # 旧版本带着很早的时间戳
r = deploy.deploy_archive(staged('v2'), '', 'rb', TARGET, cfg())
t0 = time.time() - 2
out = deploy.rollback(TARGET, r['dest'], r['backup'], r['replaced'])
ck('restored: 上一版本放回原位', out['ok'] and out['action'] == 'restored' and live('rb') == 'v1', out)
ck('  备份被用掉 (挪回去了)', not os.path.exists(r['backup']))
ck('  放回的文件时间戳刷成现在 (逼 make 按旧源码重编, 不拿新版本留下的 .o 去链接)',
   os.path.getmtime(os.path.join(GAMES, 'rb', 'mygame.cc')) >= t0)
ck('  没在 games/ 留下别的东西', sorted(os.listdir(os.path.join(GAMES, 'rb'))) == ['mygame.cc', 'rule.md'])
r = deploy.deploy_archive(staged('n1'), '', 'rb_new', TARGET, cfg())
out = deploy.rollback(TARGET, r['dest'], r['backup'], r['replaced'])
ck('removed: 新游戏目录整个移除', out['ok'] and out['action'] == 'removed'
   and not os.path.exists(os.path.join(GAMES, 'rb_new')), out)
r = deploy.deploy_archive(staged('v2'), '', 'rb', TARGET, cfg())
park = store.pending_path('20261006-000000-park', 'rb')
out = deploy.rollback(TARGET, r['dest'], r['backup'], r['replaced'], park)
ck('park: games/ 恢复上一版本', out['ok'] and live('rb') == 'v1', out)
ck('  新代码进了 pending/<记录号>/<名称>', read(os.path.join(park, 'mygame.cc')) == 'v2'
   and out['parked'] == park)

print('\n[4] rollback 的安全边界')
game('safe', 'v1')
for label, dest in (('上传目录本身', GAMES), ('上传目录的上一级', os.path.dirname(GAMES)),
                    ('三层深', os.path.join(GAMES, 'safe', 'a', 'b')),
                    ('上传目录以外', os.path.join(_env.SANDBOX, 'elsewhere'))):
    out = deploy.rollback(TARGET, dest, '', False)
    ck(f'dest 是{label} → 拒绝', not out['ok'] and '拒绝回滚' in out['error'], out)
ck('  拒绝之后什么都没删', live('safe') == 'v1' and os.path.isdir(GAMES))
out = deploy.rollback(TARGET, os.path.join(GAMES, 'safe'),
                      os.path.join(store.BACKUPS_DIR, 'safe.20990101-000000'), True)
ck('原来有东西但备份找不到 → 一个文件都不动', not out['ok'] and not out['taken']
   and live('safe') == 'v1', out)
out = deploy.rollback(TARGET, os.path.join(GAMES, 'safe'), os.path.join(GAMES, 'rb'), True)
ck('备份路径不在 backups/ 里 → 不认', not out['ok'] and live('safe') == 'v1'
   and live('rb') == 'v1', out)
out = deploy.rollback(TARGET, os.path.join(GAMES, 'safe'), '', False,
                      park=os.path.join(_env.SANDBOX, 'not_pending', 'safe'))
ck('暂存路径不在 pending/ 里 → 拒绝', not out['ok'] and live('safe') == 'v1', out)

print('\n[5] 单文件')
game('sf', 'v1')
r = deploy.deploy_single(b'h1', 'board.h', 'sf', TARGET, cfg())
ck('新加的文件 → replaced=False', r['ok'] and r['replaced'] is False)
out = deploy.rollback(TARGET, r['dest'], r['backup'], r['replaced'])
ck('  回滚 = 只撤这一个文件, 目录还在', out['ok'] and out['action'] == 'removed'
   and not os.path.exists(os.path.join(GAMES, 'sf', 'board.h')) and live('sf') == 'v1', out)
r = deploy.deploy_single(b'v2', 'mygame.cc', 'sf', TARGET, cfg(keep_replaced_backup=False))
ck('替换已有文件 → 有备份', r['ok'] and r['replaced'] and os.path.isfile(r['backup']))
out = deploy.rollback(TARGET, r['dest'], r['backup'], r['replaced'])
ck('  回滚 = 放回被替换的那一个', out['ok'] and out['action'] == 'restored' and live('sf') == 'v1', out)
r = deploy.deploy_single(b'v2', 'mygame.cc', 'sf', TARGET, cfg(keep_replaced_backup=False))
parent = os.path.dirname(r['backup'])
ck('discard_backup 删掉临时备份', deploy.discard_backup(r['backup']) and not os.path.exists(r['backup']))
ck('  backups/<文件夹>/ 删空了也收掉', not os.path.exists(parent))

print('\n[6] 落地中途失败 → 当场复原, 不留半截目录')
game('mid', 'v1')
src = staged('v2')
real_move, real_copytree = shutil.move, shutil.copytree


def flaky_move(a, b, *x, **k):
    if os.path.realpath(a) == os.path.realpath(src):
        raise OSError('磁盘满了 (模拟)')
    return real_move(a, b, *x, **k)


def flaky_copytree(a, b, *x, **k):
    os.makedirs(os.path.join(b, 'half'), exist_ok=True)       # 拷了一半
    raise OSError('还是满的 (模拟)')


shutil.move, shutil.copytree = flaky_move, flaky_copytree
try:
    r = deploy.deploy_archive(src, '', 'mid', TARGET, cfg())
finally:
    shutil.move, shutil.copytree = real_move, real_copytree
ck('报部署失败', not r['ok'] and '部署失败' in r['error'], r)
ck('  旧版本已放回', live('mid') == 'v1')
ck('  没有半截目录', sorted(os.listdir(os.path.join(GAMES, 'mid'))) == ['mygame.cc', 'rule.md'])
game('sw', 'v1')


def bad_open(*a, **k):
    raise OSError('只读 (模拟)')


deploy.open = bad_open                      # 模块级同名遮住内置 open, 只影响 deploy
try:
    r = deploy.deploy_single(b'v2', 'mygame.cc', 'sw', TARGET, cfg())
finally:
    del deploy.open
ck('单文件写入失败 → 被替换的旧文件放回', not r['ok'] and live('sw') == 'v1', r)

print('\n[7] flow._settle: 按结果收拾')


def rec(name, ver_old=None, ver_new='v2', is_new=False, perm=False, keep=True, user='u1'):
    """落地一份新代码, 返回带回滚信息的记录 (与 _finish_deploy 登记的字段一致)。"""
    if ver_old is None:
        shutil.rmtree(os.path.join(GAMES, name), ignore_errors=True)
    else:
        game(name, ver_old)
    r = deploy.deploy_archive(staged(ver_new), '', name, TARGET, cfg(keep_replaced_backup=keep))
    assert r['ok'], r
    store.perm_delete(name)
    if perm:
        store.perm_set(name, user, '上传者')
    rid = store.new_record_id() + f'-{_n[0]}'
    return {'id': rid, 'mode': 'archive', 'dest': r['dest'], 'backup': r['backup'],
            'replaced': r['replaced'], 'backup_temp': r['backup_temp'], 'is_new': is_new,
            'perm_bound': perm, 'user_id': user, 'folder': '', 'deploy_name': name}


r = rec('s_ok', 'v1', keep=False)
bk = r['backup']
ck('成功 → 不回滚', flow._settle(r, R_OK, 's_ok') == {} and live('s_ok') == 'v2')
ck('  临时备份删掉、记录里也清空', not os.path.exists(bk) and r['backup'] == '')
r = rec('s_ok2', 'v1', keep=True)
flow._settle(r, R_OK, 's_ok2')
ck('成功 + 开着备份 → 备份留着', os.path.exists(r['backup']))

r = rec('s_code', 'v1')
out = flow._settle(r, R_CODE, 's_code')
ck('编译器报错 (老游戏) → 放回上一版本', out['ok'] and out['action'] == 'restored'
   and live('s_code') == 'v1' and not out['pending'], out)
ck('  记录里的 backup 清空 (已放回原位)', r['backup'] == '')
ck('  说法', deploy.describe_rollback({**r, 'rollback': out}).startswith('已回滚: 编不过的新代码已撤下'))

r = rec('s_newbad', None, is_new=True, perm=True)
out = flow._settle(r, R_CODE, 's_newbad')
ck('编译器报错 (新游戏) → 目录整个移除', out['ok'] and out['action'] == 'removed'
   and not os.path.exists(os.path.join(GAMES, 's_newbad')), out)
ck('  自动加的绑定收回', out['unbound'] and store.perm_get('s_newbad') is None
   and r['perm_bound'] is False)
ck('  下次上传仍判为新游戏 (目录不在)', not os.path.isdir(os.path.join(GAMES, 's_newbad')))
ck('  说法', '修改后重新上传仍按新游戏处理' in deploy.describe_rollback({**r, 'rollback': out}))

r = rec('s_rebound', None, is_new=True, perm=True)
store.perm_set('s_rebound', 'admin_pick', '管理员改绑的')
out = flow._settle(r, R_CODE, 's_rebound')
ck('中途被管理员改绑过 → 目录照撤, 绑定不动', out['ok'] and not out['unbound']
   and store.perm_get('s_rebound')['user_id'] == 'admin_pick', out)

r = rec('中文游戏', None, is_new=True, perm=True)
out = flow._settle(r, R_INVALID, '中文游戏')
ck('目标名非法 (中文目录名) → 移除, 不留给 CMake', out['ok'] and out['action'] == 'removed'
   and not os.path.exists(os.path.join(GAMES, '中文游戏')), out)

r = rec('s_busy', 'v1')
out = flow._settle(r, R_BUSY, 's_busy')
ck('编译进程被占用 (老游戏) → games/ 恢复上一版本', out['ok'] and live('s_busy') == 'v1', out)
ck('  新代码暂存, 记录登记的是相对 data/ 的路径', out['pending'].startswith('pending/')
   and read(os.path.join(store.pending_of({'rollback': out}), 'mygame.cc')) == 'v2', out)

r = rec('s_newbusy', None, is_new=True, perm=True)
out = flow._settle(r, R_TIMEOUT, 's_newbusy')
ck('超时 (新游戏) → 目录暂时撤下、新代码暂存', out['ok'] and out['pending']
   and not os.path.exists(os.path.join(GAMES, 's_newbusy')), out)
ck('  绑定保留 (名字留给 /compile)', not out['unbound'] and store.perm_get('s_newbusy') is not None)
ck('  说法', '新游戏目录暂时从服务器上撤下' in deploy.describe_rollback({**r, 'rollback': out}))

r = rec('s_misconf', 'v1')
out = flow._settle(r, R_MISCONF, 's_misconf')
ck('接口地址配错 → 同样暂存 (改好地址后 /compile)', out['ok'] and out['pending']
   and live('s_misconf') == 'v1', out)

game('s_legacy', 'v2')
legacy = {'id': 'legacy', 'dest': os.path.join(GAMES, 's_legacy'), 'backup': '', 'mode': 'archive'}
out = flow._settle(legacy, R_CODE, 's_legacy')
ck('旧版本插件的记录 (没有 replaced) → 不动文件', out.get('skipped') and not out['ok']
   and live('s_legacy') == 'v2', out)

r = rec('s_nobk', 'v1')
shutil.rmtree(r['backup'])
out = flow._settle(r, R_CODE, 's_nobk')
ck('备份被人删了 → 回滚失败、新代码原样留着', not out['ok'] and not out['taken']
   and live('s_nobk') == 'v2', out)
ck('  说法', deploy.describe_rollback({**r, 'rollback': out}) == '自动回滚失败, 新代码仍在服务器上')


print('\n[8] 端到端: 上传 → 编译 → 回滚 → /compile')


class Ev:
    group_id = 'g1'
    username = '上传者'

    def __init__(self, user='u1'):
        self.user_id = user
        self.sent = []

    async def reply(self, text):
        self.sent.append(text)
        return True

    async def send_to_group(self, gid, text):
        self.sent.append(text)


NEXT = []
RESTARTS = []


async def fake_compile(game_name, c, is_new=False):
    res = dict(NEXT.pop(0))
    res['new'] = bool(is_new)
    return res


async def fake_restart(c, reason, auto=True):
    RESTARTS.append(reason)
    return {'status': 'success', 'ok': True, 'enabled': True, 'auto': True,
            'reason': reason, 'active_matches': 0, 'http_status': 200}


compilemod.request_compile = fake_compile
compilemod.request_planned_restart = fake_restart


def upload(name, ver, is_new, user='u1'):
    record = {'forced': False, 'is_new': is_new, 'perm_bound': False, 'compile': {},
              'restart': {}, 'id': store.new_record_id() + f'-{name}', 'time': 't',
              'group_id': 'g1', 'user_id': user, 'username': '上传者',
              'filename': name + '.zip', 'target': 'lgtbot', 'target_path': GAMES,
              'folder': '', 'mode': 'archive', 'source': 'quote', 'url': '', 'size': 10,
              'sha256': 'x' + name + ver, 'stage': 'review', 'verdict': 'pass',
              'categories': [], 'manual': False, 'error': '', 'file_count': 2,
              'game_name': '', '_started': 0}
    ev = Ev(user)
    asyncio.run(flow._finish_deploy(ev, cfg(), record, b'', staged(ver),
                                    {'root': '', 'total_size': 0}, TARGET, ''))
    return ev, store.list_records(1)[0]


game('e_upd', 'v1')
NEXT[:] = [R_CODE]
ev, last = upload('e_upd', 'v2', False)
ck('老游戏编不过 → 群消息说已回滚', any('已回滚: 编不过的新代码已撤下' in m for m in ev.sent), ev.sent)
ck('  出路仍是改完重传', any('重新 /upload' in m for m in ev.sent))
ck('  服务器上是上一版本, 记录登记了回滚', live('e_upd') == 'v1'
   and last['rollback']['action'] == 'restored')
ck('  留档写明服务器上的代码', '## 服务器上的代码' in store.get_review_text(last['id']))
ok_ = asyncio.run(flow.handle_recompile(Ev(), 'e_upd'))
ck('  /compile 照旧拒绝 (编译器报错)', ok_ is False)

NEXT[:] = [R_CODE]
ev, last = upload('e_new', 'n1', True)
ck('新游戏编不过 → 目录移除、绑定收回', not os.path.isdir(os.path.join(GAMES, 'e_new'))
   and store.perm_get('e_new') is None and last['rollback']['unbound'], last.get('rollback'))
ck('  群消息说下次仍按新游戏处理', any('仍按新游戏处理' in m for m in ev.sent), ev.sent)
NEXT[:] = [R_OK]
RESTARTS.clear()
ev, last = upload('e_new', 'n2', True)
ck('  改好再传: 照旧按新游戏编 (new=true) 并请求计划重启',
   last['compile'].get('new') is True and RESTARTS and live('e_new') == 'n2', (last['compile'], RESTARTS))

NEXT[:] = [R_BUSY]
ev, last = upload('e_held', 'n1', True)
ck('新游戏遇到编译进程被占用 → 目录暂时撤下、新代码暂存',
   not os.path.isdir(os.path.join(GAMES, 'e_held')) and store.pending_of(last), last.get('rollback'))
ck('  群消息: 新代码已暂存 + /compile', any('新代码已暂存' in m and '/compile e_held' in m
                                         for m in ev.sent), ev.sent)

# 暂存期间别人传同名新游戏 → 照绑定拦下 (handle 走到权限校验为止, 不真的起任务)
spawned = []
flow._spawn = lambda coro: (spawned.append(1), coro.close())
flow.quoted.extract_file_ref = lambda event, allow_any=True: {
    'filename': 'e_held.zip', 'url': 'http://x', 'source': 'quote'}
other = Ev('u2')
asyncio.run(flow.handle(other, ''))
ck('  暂存期间别人传同名 → 权限不足', any('权限不足' in m for m in other.sent) and not spawned,
   other.sent)
owner = Ev('u1')
asyncio.run(flow.handle(owner, ''))
ck('  上传者本人照常受理', spawned == [1], owner.sent)
flow._busy = False

NEXT[:] = [R_OK]
RESTARTS.clear()
ev = Ev('u1')
for f in os.listdir(store.pending_of(last)):
    os.utime(os.path.join(store.pending_of(last), f), (1e9, 1e9))
t0 = time.time() - 2
asyncio.run(flow._run_recompile(ev, cfg(), 'e_held', store.last_game_record('e_held')))
re_ = store.list_records(1)[0]
ck('/compile 换回的文件时间戳也刷新 (否则比期间重编出的 .o 旧, make 会当成不用编)',
   os.path.getmtime(os.path.join(GAMES, 'e_held', 'mygame.cc')) >= t0)
ck('/compile: 暂存换回 games/ 再编, 成功', live('e_held') == 'n1' and re_['compile'].get('ok')
   and re_['stage'] == 'recompile', re_)
ck('  暂存用掉了', not store.pending_of(last))
ck('  新游戏仍按完整编译 + 请求计划重启', re_['compile'].get('new') is True and RESTARTS)
ck('  绑定还在', (store.perm_get('e_held') or {}).get('user_id') == 'u1')

NEXT[:] = [R_TIMEOUT]
ev, last = upload('e_held2', 'n1', True)
NEXT[:] = [R_CODE]
asyncio.run(flow._run_recompile(Ev('u1'), cfg(), 'e_held2', store.last_game_record('e_held2')))
re_ = store.list_records(1)[0]
ck('/compile 换回后编译器报错 → 目录移除、绑定收回、暂存不留',
   not os.path.isdir(os.path.join(GAMES, 'e_held2')) and store.perm_get('e_held2') is None
   and not store.pending_of(re_) and re_['rollback']['action'] == 'removed', re_.get('rollback'))

game('e_upd2', 'v1')
NEXT[:] = [R_BUSY]
ev, first = upload('e_upd2', 'v2', False)
ck('老游戏临时失败 → 上一版本在位、新代码暂存', live('e_upd2') == 'v1' and store.pending_of(first))
NEXT[:] = [R_OK]
ev, second = upload('e_upd2', 'v3', False)
ck('  没 /compile 又传了新版 → 上一份暂存作废删除', not store.pending_of(first)
   and live('e_upd2') == 'v3')

NEXT[:] = [R_BUSY]
ev, last = upload('e_refail', 'n1', True)
real_dp = deploy.deploy_pending
deploy.deploy_pending = lambda *a, **k: deploy._out(False, 'e_refail', '放不回去 (模拟)')
try:
    asyncio.run(flow._run_recompile(Ev('u1'), cfg(), 'e_refail', store.last_game_record('e_refail')))
finally:
    deploy.deploy_pending = real_dp
ck('/compile 换回失败 → 新记录带上暂存登记, 下次还够得着',
   store.pending_of(store.last_game_record('e_refail')) == store.pending_of(last) != '')
NEXT[:] = [R_OK]
asyncio.run(flow._run_recompile(Ev('u1'), cfg(), 'e_refail', store.last_game_record('e_refail')))
ck('  再 /compile 一次就换回去了', live('e_refail') == 'n1')


def upload_file(folder, filename, data):
    record = {'forced': False, 'is_new': False, 'perm_bound': False, 'compile': {},
              'restart': {}, 'id': store.new_record_id() + f'-{folder}-f', 'time': 't',
              'group_id': 'g1', 'user_id': 'u1', 'username': '上传者', 'filename': filename,
              'target': 'lgtbot', 'target_path': GAMES, 'folder': folder, 'mode': 'file',
              'source': 'quote', 'url': '', 'size': len(data), 'sha256': 'f' + folder,
              'stage': 'review', 'verdict': 'pass', 'categories': [], 'manual': False,
              'error': '', 'file_count': 1, 'game_name': '', '_started': 0}
    ev = Ev()
    asyncio.run(flow._finish_deploy(ev, cfg(), record, data, '', {'root': '', 'total_size': 0},
                                    TARGET, folder))
    return ev, store.list_records(1)[0]


game('e_sf', 'v1')
NEXT[:] = [R_BUSY]
ev, last = upload_file('e_sf', 'mygame.cc', b'v2')
ck('单文件临时失败 → 旧文件放回、新文件暂存', live('e_sf') == 'v1'
   and read(store.pending_of(last)) == 'v2', last.get('rollback'))
NEXT[:] = [R_OK]
asyncio.run(flow._run_recompile(Ev('u1'), cfg(), 'e_sf', store.last_game_record('e_sf')))
ck('  /compile 把那一个文件换回去再编', live('e_sf') == 'v2' and not store.pending_of(last))

game('e_nocomp', 'v1')
config.update({'compile_enabled': False, 'keep_replaced_backup': False})
try:
    ev, last = upload('e_nocomp', 'v2', False)
finally:
    config.update({'compile_enabled': True, 'keep_replaced_backup': True})
ck('没开自动编译 + 没开备份 → 新代码在位、临时备份当场删 (与 1.17 行为一致)',
   live('e_nocomp') == 'v2' and last['backup'] == '' and not last.get('rollback')
   and not [n for n in os.listdir(store.BACKUPS_DIR) if n.startswith('e_nocomp.')],
   (last.get('backup'), os.listdir(store.BACKUPS_DIR)))
ck('  也不报「旧内容已备份」', not any('旧内容已备份' in m for m in ev.sent), ev.sent)

NEXT[:] = [R_BUSY]
ev, last = upload('e_gone', 'n1', True)
store.drop_pending(last)
said = Ev('u1')
ok_ = asyncio.run(flow.handle_recompile(said, 'e_gone'))
ck('暂存被后台删了 → /compile 拒绝并说清楚', ok_ is False
   and any('暂存的新代码已不在' in m for m in said.sent), said.sent)

print('\n[9] 数据文件页 / 删记录')
NEXT[:] = [R_BUSY]
ev, last = upload('e_files', 'n1', True)
ents = store.list_entries()['entries']
pend = [e for e in ents if e['kind'] == 'pending']
ck('pending 按 pending/<记录号> 聚合成一项', any(e['path'] == 'pending/' + last['id']
                                              and e['display'].endswith('/e_files') for e in pend),
   pend)
ck('不允许删 pending/ 根目录', store.delete_entry('pending') != '')
write(os.path.join(store.PENDING_DIR, 'x', 'f'), 'x')
ck('登记路径只有一层 (pending/x) → 不删, 免得把整个 pending/ 一锅端',
   store.drop_pending({'rollback': {'pending': 'pending/x'}}) is False
   and store.pending_of(last) != '')
res = store.delete_record(last['id'])
ck('删记录连同暂存一起删', res['ok'] and not store.pending_of(last)
   and ('pending/' + last['id']) in res['files'], res)

print('\n[10] 报告页: 写明服务器上的代码, 不带报错原文')
r = {'id': 'rp', 'mode': 'archive', 'rollback': {'ok': False, 'taken': True, 'pending': '',
                                                 'error': 'OSError: /home/lighthouse/x'},
     'filename': 'a.zip', 'target': 'lgtbot', 'folder': ''}
page = report.build_compile(r, R_CODE, 'a')
ck('有「服务器上的代码」一行', '服务器上的代码' in page and '新代码已撤下, 但上一版本没能放回服务器' in page)
ck('  不含回滚报错里的服务器路径', '/home/lighthouse' not in page)
r = {'id': 'rp2', 'mode': 'archive', 'rollback': {'ok': True, 'action': 'restored',
                                                  'pending': 'pending/rp2/a'},
     'filename': 'a.zip', 'target': 'lgtbot', 'folder': ''}
page = report.build_compile(r, R_BUSY, 'a')
ck('临时失败的提示改成「新代码已暂存」', '暂存' in page and '/compile a' in page)

ck.done()
