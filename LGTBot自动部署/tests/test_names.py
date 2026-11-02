# -*- coding: utf-8 -*-
"""名称检查 (1.20.0) —— 新游戏目录名必须全小写; 压缩包名与包内文件夹名必须一致。

要守住的:
1. deploy.bad_game_name: 只认小写字母、数字、下划线, 不以数字开头, 最长 63
   (目录名就是 LGTBot 编译时的 C++ 命名空间 GAME_MODULE_NAME);
2. flow.handle 只查**新建**的游戏目录: 拦在下载之前、不建记录, force 也拦; 已有目录、
   暂存待重编的、单文件都不查; 提示不复述名字;
3. 包里唯一的顶层文件夹与压缩包名 (去扩展名) 不同就拒收: stage='name', 不送审、不落地、
   不 @ 开发者, 群里不报两个名字 (明细进记录); 平铺在包根的不受影响; force 也拦;
4. 名称不一致不算对内容下过结论: 改个压缩包名 (字节不变) 重传照常处理。
跑的是真的 handle / _run, 下载 / 审核 / 编译 API 用假函数顶替。
"""
import asyncio
import io
import os
import tarfile
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
               'review_enabled': True, 'compile_enabled': True, 'notify_users': ['DEV']})
TARGET = config.upload_target()
P = '违规探针'

print('\n[1] deploy.bad_game_name')
for name in ('mygame', 'my_game', 'game2', '_x', 'a', 'a' * 63, 'mahjong_17_steps'):
    ck(f'合规: {name[:20]}', deploy.bad_game_name(name) == '', deploy.bad_game_name(name))
for name, why in (('MyGame', '有大写'), ('myGame', '混了一个大写'), ('my-game', '连字符'),
                  ('my.game', '点'), ('my game', '空格'), ('2048', '纯数字'), ('9lives', '数字开头'),
                  ('五子棋', '中文'), ('ｍｙｇａｍｅ', '全角字母'), ('game２', '全角数字'), ('', '空'),
                  ('a' * 64, '超过 63 个字符'), ('mygame\n', '带换行')):
    msg = deploy.bad_game_name(name)
    ck(f'不合规: {why}', '必须全部小写' in msg, msg)
ck('  提示不带名字本身', P not in deploy.bad_game_name(P))

SENT = []


class Ev:
    group_id, username = 'g1', '上传者'

    def __init__(self, user='u1'):
        self.user_id = user

    async def reply(self, text):
        SENT.append(text)
        return True

    async def send_to_group(self, gid, text):
        SENT.append(text)


def handle(filename, argline='', user='u1', force=False):
    """跑 handle(); 返回 (这次新发的消息, 是否起了后台任务 = 是否去下载)。"""
    saved = (flow.quoted.extract_file_ref, flow._spawn)
    spawned = []
    flow.quoted.extract_file_ref = lambda e, allow_any=False: {'url': 'u', 'filename': filename}
    flow._spawn = lambda coro: (coro.close(), spawned.append(1))[1]
    s = len(SENT)
    try:
        asyncio.run(flow.handle(Ev(user), argline, force))
    finally:
        flow.quoted.extract_file_ref, flow._spawn = saved
        flow._busy = False
    return SENT[s:], bool(spawned)


print('\n[2] /upload 入口: 只查新建的游戏目录')
n_rec = len(store.list_records(1000))
for fname, why in (('MyGame.zip', '有大写'), ('my-game.tar.gz', '连字符 (复合扩展名)'),
                   ('2048.zip', '数字开头'), (f'{P}.zip', '中文')):
    said, spawned = handle(fname)
    ck(f'新游戏{why} → 拒收, 不下载', not spawned and any('必须全部小写' in m for m in said), said)
    ck('  说明目录名要和压缩包名一致', any('和压缩包名一致' in m for m in said), said)
said, _ = handle(f'{P}.zip')
ck('  不复述名字', said and not any(P in m for m in said), said)
ck('  不建记录', len(store.list_records(1000)) == n_rec)
said, spawned = handle('MyGame.zip', force=True)
ck('force 也拦', not spawned and any('必须全部小写' in m for m in said), said)
for fname in ('my_game.zip', 'game2.tar.gz', '_x.zip'):
    said, spawned = handle(fname)
    ck(f'合规的新游戏放行: {fname}', spawned, said)

os.makedirs(os.path.join(GAMES, 'Legacy-Game'), exist_ok=True)
store.perm_set('Legacy-Game', 'u1', '上传者')
said, spawned = handle('Legacy-Game.zip')
ck('已有目录 (大写 + 连字符) → 不查, 照常更新', spawned, said)
said, spawned = handle('rule.md', argline='Legacy-Game')
ck('单文件写进已有目录 → 不查', spawned, said)
said, spawned = handle('Legacy-Game.zip', user='u2')
ck('  已有目录照样走绑定校验', not spawned and any('权限不足' in m for m in said), said)

real_last, real_pending = store.last_game_record, store.pending_of
store.last_game_record = lambda g: {'id': 'held'} if g == 'Held_Game' else real_last(g)
store.pending_of = lambda rec: '/pending/x' if (rec or {}).get('id') == 'held' else real_pending(rec)
store.perm_set('Held_Game', 'u1', '上传者')
try:
    said, spawned = handle('Held_Game.zip')
finally:
    store.last_game_record, store.pending_of = real_last, real_pending
ck('暂存待重编的新游戏 (目录暂时不在) → 不查', spawned, said)

# ---- 名称不一致: 真的 _run, 下载 / 审核 / 编译用假函数 ----
PACKAGE, REVIEWED = {}, []
PASS = {'verdict': 'pass', 'categories': [], 'manual': False, 'model': 'fake', 'error': '',
        'http_status': 200, 'raw': '{}', 'elapsed': 0.1, 'criteria': [], 'findings': [],
        'game_name': '', 'game_desc': '', 'retryable': True}


async def fake_download(url, max_bytes, timeout):
    return PACKAGE['data'], ''


async def fake_review(pkg, meta, cfg):
    REVIEWED.append(meta)
    return dict(PASS)


async def fake_compile(game_name, cfg, is_new=False):
    return {'status': 'success', 'ok': True, 'http_status': 200, 'new': bool(is_new)}


async def fake_restart(cfg, reason, auto=True):
    return {'status': 'success', 'ok': True, 'enabled': True, 'auto': True, 'reason': reason,
            'active_matches': 0, 'http_status': 200}


flow.quoted.download = fake_download
review.review = fake_review
compilemod.request_compile = fake_compile
compilemod.request_planned_restart = fake_restart


def mkzip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, text in files.items():
            zf.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), text)
    return buf.getvalue()


def mktgz(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for name, text in files.items():
            raw = text.encode('utf-8')
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def files(root, ver='v1'):
    pre = f'{root}/' if root else ''
    return {f'{pre}mygame.cc': ver, f'{pre}rule.md': 'rule'}


def run(fname, data, force=False):
    PACKAGE['data'] = data
    s = len(SENT)
    asyncio.run(flow._run(Ev(), config.all_config(), {'filename': fname, 'url': 'u'},
                          TARGET, '', force, True))
    return SENT[s:], store.list_records(1)[0]


def landed(name):
    return os.path.isfile(os.path.join(GAMES, name, 'mygame.cc'))


print('\n[3] 压缩包名与包内文件夹名不一致 → 拒收')
n = len(REVIEWED)
said, r = run('nm_a.zip', mkzip(files('nm_b')))
ck('不一致 → stage=name, 没送审', r['stage'] == 'name' and len(REVIEWED) == n, r.get('stage'))
ck('  群里说「名称不一致」', any('名称不一致' in m for m in said), said)
ck('  不复述两个名字', said and not any('nm_a' in m or 'nm_b' in m for m in said), said)
ck('  不 @ 开发者', not any('<@DEV>' in m for m in said), said)
ck('  两个名字进记录明细 (后台可看)', 'nm_a' in r['error'] and 'nm_b' in r['error'], r['error'])
ck('  没落地', not os.path.exists(os.path.join(GAMES, 'nm_a'))
   and not os.path.exists(os.path.join(GAMES, 'nm_b')))
said, r = run('nm_c.zip', mkzip(files('NM_C')))
ck('只差大小写也算不一致', r['stage'] == 'name', r.get('stage'))
said, r = run('nm_t.tar.gz', mktgz(files('nm_u')))
ck('tar.gz 一样查', r['stage'] == 'name', r.get('stage'))
said, r = run('nm_f.zip', mkzip(files('nm_g')), force=True)
ck('force 也拦', r['stage'] == 'name' and not landed('nm_f'), r.get('stage'))
said, r = run(f'{P}甲.zip', mkzip(files(f'{P}乙')))
ck('探针名字 → 一个字都不进群', r['stage'] == 'name' and said and not any(P in m for m in said), said)

print('\n[4] 对得上 / 没套文件夹 → 照常')
said, r = run('nm_d.zip', mkzip(files('nm_d')))
ck('同名 → 部署, 那一层被提上来', r['stage'] == 'deployed' and landed('nm_d')
   and not os.path.exists(os.path.join(GAMES, 'nm_d', 'nm_d')), (r.get('stage'), said))
said, r = run('nm_k.tar.gz', mktgz(files('nm_k')))
ck('tar.gz 同名 (去的是整个复合扩展名) → 部署', r['stage'] == 'deployed' and landed('nm_k'),
   (r.get('stage'), said))
said, r = run('nm_e.zip', mkzip(files('')))
ck('文件平铺在包根 → 部署进同名目录', r['stage'] == 'deployed' and landed('nm_e'), (r.get('stage'), said))

print('\n[5] 名称不一致不算对内容下过结论')
ck('_settled(stage=name) → 不拦', flow._settled({'stage': 'name'}) is False)
data = mkzip(files('nm_j', 'j1'))
said, r = run('nm_i.zip', data)
ck('先因名称不一致被拒', r['stage'] == 'name', r.get('stage'))
said, r = run('nm_j.zip', data)
ck('改压缩包名 (字节不变) 重传 → 不算重复, 照常部署',
   r['stage'] == 'deployed' and landed('nm_j') and not any('重复上传' in m for m in said), said)

ck.done()
