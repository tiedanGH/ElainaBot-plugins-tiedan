# -*- coding: utf-8 -*-
"""隐藏文件预检 —— 以 . 开头的文件/目录一律不进游戏目录 (1.15.0 压缩包 / 1.15.1 单文件)。

压缩包在**解压前**扫成员名, 一个字节都不落盘; 单文件光看文件名就能判, 拦在下载之前。
"""
import asyncio
import io
import os
import tarfile
import tempfile
import types
import zipfile

import _env

archive, flow = _env.boot('archive', 'flow')
ck = _env.Checker()
LIMITS = {'max_files': 2000, 'max_uncompressed': 200 * 1048576, 'text_budget': 150000}


def mkzip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for n in names:
            zf.writestr(n, b'x' * 10)
    return buf.getvalue()


def mktar(names):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for n in names:
            info = tarfile.TarInfo(n)
            info.size = 10
            tf.addfile(info, io.BytesIO(b'x' * 10))
    return buf.getvalue()


def extract(data, fname='g.zip'):
    dest = tempfile.mkdtemp(dir=_env.SANDBOX)
    try:
        archive.extract(data, fname, dest, LIMITS)
        return None, dest
    except archive.ArchiveError as e:
        return e, dest


GOOD = ['g/mygame.cc', 'g/rule.md', 'g/board.h']

print('\n[1] hidden_members: 只报到隐藏那一级')
ck('.git 整棵树收敛成一条',
   archive.hidden_members(['g/.git/objects/ab/cd', 'g/.git/HEAD']) == ['g/.git'])
ck('顶层 .DS_Store', archive.hidden_members(['g/.DS_Store']) == ['g/.DS_Store'])
ck('多个不同隐藏项都列出',
   archive.hidden_members(['g/.git/x', 'g/.vscode/s.json', 'g/a.cc']) == ['g/.git', 'g/.vscode'])
ck('干净包为空', archive.hidden_members(GOOD) == [])
ck('去重保序', archive.hidden_members(['a/.x', 'b/.y', 'a/.x/z']) == ['a/.x', 'b/.y'])
ck('./ 前缀不误判', archive.hidden_members(['./g/a.cc']) == [])
ck('文件名里带点但不以点开头 → 不算', archive.hidden_members(['g/my.game.cc']) == [])

print('\n[2] 压缩包: 含隐藏文件就拒, 且一个字节不落盘')
for bad, why in ((['g/mygame.cc', 'g/.DS_Store'], '.DS_Store'),
                 (['g/mygame.cc', 'g/.git/HEAD'], '.git 目录'),
                 (['g/mygame.cc', 'g/.gitignore'], '.gitignore'),
                 (['g/.vscode/settings.json'], '.vscode 目录'),
                 (['__MACOSX/._mygame.cc', 'g/mygame.cc'], 'macOS 的 ._ 伴生文件')):
    err, dest = extract(mkzip(bad))
    ck(f'zip 拒收: {why}', isinstance(err, archive.HiddenMemberError), err)
    ck(f'  未落盘: {why}', os.listdir(dest) == [], os.listdir(dest))
err, dest = extract(mktar(['g/mygame.cc', 'g/.git/HEAD']), 'g.tar.gz')
ck('tar.gz 也拒收', isinstance(err, archive.HiddenMemberError), err)
ck('  tar 也未落盘', os.listdir(dest) == [], os.listdir(dest))

print('\n[3] 干净包照常放行')
err, dest = extract(mkzip(GOOD))
ck('无隐藏文件 → 不报错', err is None, err)
ck('文件都解出来了', sorted(os.listdir(os.path.join(dest, 'g'))) == ['board.h', 'mygame.cc', 'rule.md'])

print('\n[4] 报错文案')
err, _ = extract(mkzip(['g/mygame.cc', 'g/.git/HEAD', 'g/.DS_Store']))
msg = str(err)
ck('点明是隐藏文件', '以 . 开头的隐藏文件/目录' in msg, msg)
ck('列出具体项', '.git' in msg and '.DS_Store' in msg, msg)
ck('.git 只出现一次 (没把整棵树刷出来)', msg.count('.git') == 1, msg)
err, _ = extract(mkzip(['g/.h%d' % i for i in range(20)] + ['g/mygame.cc']))
ck('超过 8 项截断并给总数', '等 20 项' in str(err), str(err))

print('\n[5] 分类与处置')
ck('HiddenMemberError 是 ArchiveError 的子类',
   issubclass(archive.HiddenMemberError, archive.ArchiveError))
src = io.open(os.path.join(_env.TEST_ENV, 'app', 'flow.py'), encoding='utf-8').read()
i_h = src.find('except archive.HiddenMemberError')
i_a = src.find('except archive.ArchiveError')
ck('flow 里子类 except 排在父类之前 (否则永远命中不到)', 0 < i_h < i_a, (i_h, i_a))
ck('走独立 stage=hidden', "record.update(stage='hidden'" in src)
ck('不 @ 开发者', 'notify=False' in src[i_h:i_a], src[i_h:i_a][:200])

print('\n[6] 单文件: 拦在下载之前')
GAMES = os.path.join(_env.SANDBOX, 'games')
os.makedirs(os.path.join(GAMES, 'demo'), exist_ok=True)
CFG = {'enabled': True, 'allowed_groups': ['g'], 'upload_dir': GAMES,
       'notify_users': [], 'required_files': []}


def handle(filename, argline='demo', force=False):
    """跑 handle(); 返回 (是否受理, 最后一条消息, 是否起了后台任务 = 是否下载)。"""
    ev = types.SimpleNamespace(group_id='g', user_id='U1', username='x', sent=[])

    async def send(*a):
        ev.sent.append(a[-1])
        return {'id': 'm'}

    ev.reply, ev.send_to_group = send, send
    spawned = []
    saved = (flow.config.all_config, flow.quoted.extract_file_ref, flow._spawn,
             flow.store.perm_get)
    flow.config.all_config = lambda refresh=False: CFG
    flow.quoted.extract_file_ref = lambda e, allow_any=False: {'url': 'u', 'filename': filename}
    flow._spawn = lambda coro: (coro.close(), spawned.append(1))[1]
    flow.store.perm_get = lambda g: {'user_id': 'U1', 'username': 'x'}
    try:
        ok = asyncio.run(flow.handle(ev, argline, force))
    finally:
        (flow.config.all_config, flow.quoted.extract_file_ref, flow._spawn,
         flow.store.perm_get) = saved
        flow._busy = False
    return ok, (ev.sent[-1] if ev.sent else ''), spawned


for name in ('.DS_Store', '.gitignore', '.env', '.clang-format'):
    ok, msg, sp = handle(name)
    ck(f'单文件拒收 {name}', ok is False and not sp, (ok, msg))
    ck('  提示说明是隐藏文件', '隐藏文件' in msg, msg)
# 1.18.3 起群消息不复述用户输入 (插件目录 CLAUDE.md): 上传者起的文件名不能出现在提示里
ok, msg, sp = handle('.探针文件')
ck('  提示不复述文件名', ok is False and '探针' not in msg, msg)
ok, msg, sp = handle('.DS_Store')
ck('没起后台任务 (= 没下载)', sp == [], sp)
for name in ('mygame.cc', 'board.h', 'my.game.cc'):
    ok, _, sp = handle(name)
    ck(f'正常单文件放行 {name}', ok is True and len(sp) == 1, (ok, sp))
ok, _, sp = handle('.DS_Store', force=True)
ck('force 也拦 (与压缩包预检一致)', ok is False and not sp)
ok, msg, sp = handle('.secret.zip', argline='')
ck('以 . 开头的压缩包名也拦', ok is False and not sp and '隐藏文件' in msg, msg)
ok, _, sp = handle('demo.zip', argline='')
ck('正常压缩包不受影响', ok is True and len(sp) == 1, (ok, sp))

ck.done()
