# -*- coding: utf-8 -*-
""".py 预检 (1.18.1) —— 包里带 .py 一律拒收。

主框架每 2 秒递归扫一遍每个插件目录 (data/ 也在内), 不以 _ 开头的 .py 有增删改就热重载
那个插件。1.18.0 出过一次: 新游戏包里有个生成素材的 .py, 解压进 data/staging 两秒内本插件
被重载, 审核中的任务被取消。压缩包解压前扫成员名, 单文件拦在下载之前 (留档原件也会写成
.py); 服务器上早就带 .py 的旧目录不能挪进备份 (deploy.reload_triggers)。
"""
import asyncio
import io
import os
import tarfile
import tempfile
import types
import zipfile

import _env

archive, flow, deploy, config, store = _env.boot('archive', 'flow', 'deploy', 'config', 'store')
ck = _env.Checker()
store.init()
LIMITS = {'max_files': 2000, 'max_uncompressed': 200 * 1048576, 'text_budget': 150000}
GAMES = os.path.join(_env.SANDBOX, 'games')
os.makedirs(os.path.join(GAMES, 'demo'), exist_ok=True)


def mkzip(names, dirs=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for d in dirs:
            zf.writestr(zipfile.ZipInfo(d), b'')
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


def py_under(root):
    return [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.lower().endswith('.py')]


print('\n[1] script_members / is_script')
ck('任意层级都找得到', archive.script_members(['g/tools/gen.py', 'g/a.cc']) == ['g/tools/gen.py'])
ck('不分大小写', archive.is_script('g/X.PY') and archive.is_script('gen.Py'))
ck('_ 开头的也拦 (__init__.py)', archive.script_members(['g/__init__.py']) == ['g/__init__.py'])
ck('.pyc / .pyw / 名字里带 py 都不算',
   archive.script_members(['g/a.pyc', 'g/b.pyw', 'g/happy.cc', 'g/py/x.cc', 'g/copy']) == [])
ck('去重保序', archive.script_members(['a/x.py', 'b/y.py', 'a/x.py']) == ['a/x.py', 'b/y.py'])

print('\n[2] 压缩包: 含 .py 就拒, 一个字节不落盘')
for bad, why in ((['g/mygame.cc', 'g/tools/generate_assets.py'], '生成素材的开发脚本 (出事那次的形态)'),
                 (['g/mygame.cc', 'g/__init__.py'], '__init__.py'),
                 (['g/mygame.cc', 'g/T.PY'], '大写扩展名')):
    err, dest = extract(mkzip(bad))
    ck(f'zip 拒收: {why}', isinstance(err, archive.ScriptMemberError), err)
    ck(f'  未落盘: {why}', os.listdir(dest) == [], os.listdir(dest))
err, dest = extract(mktar(['g/mygame.cc', 'g/tools/gen.py']), 'g.tar.gz')
ck('tar.gz 也拒收', isinstance(err, archive.ScriptMemberError), err)
ck('  tar 也未落盘', os.listdir(dest) == [], os.listdir(dest))
err, _ = extract(mkzip(['g/.git/hooks/x.py', 'g/mygame.cc']))
ck('隐藏目录里的 .py → 先按隐藏文件报', isinstance(err, archive.HiddenMemberError), err)
err, dest = extract(mkzip(['g/mygame.cc'], dirs=['g/odd.py/']))
ck('叫 x.py 的目录不算 (框架只看文件)', err is None, err)

print('\n[3] 报错文案')
err, _ = extract(mkzip(['g/mygame.cc', 'g/tools/a.py', 'g/tools/b.py']))
msg = str(err)
ck('点明是 .py 并列出路径', '.py 文件' in msg and 'g/tools/a.py' in msg and 'g/tools/b.py' in msg, msg)
err, _ = extract(mkzip(['g/s%d.py' % i for i in range(12)] + ['g/mygame.cc']))
ck('超过 8 个截断并给总数', '等 12 个' in str(err), str(err))

print('\n[4] 分类与处置')
ck('ScriptMemberError 是 ArchiveError 的子类',
   issubclass(archive.ScriptMemberError, archive.ArchiveError))
src = io.open(os.path.join(_env.TEST_ENV, 'app', 'flow.py'), encoding='utf-8').read()
i_s = src.find('except archive.ScriptMemberError')
i_a = src.find('except archive.ArchiveError')
ck('flow 里子类 except 排在父类之前', 0 < i_s < i_a, (i_s, i_a))
ck('走独立 stage=script', "record.update(stage='script'" in src)
ck('不 @ 开发者', 'notify=False' in src[i_s:i_a], src[i_s:i_a][:200])

print('\n[5] 单文件: 拦在下载之前')
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


for name in ('gen.py', 'X.PY', '__init__.py'):
    ok, msg, sp = handle(name)
    ck(f'单文件拒收 {name}', ok is False and not sp, (ok, msg))
    # 1.18.3 起群消息不复述用户输入 (插件目录 CLAUDE.md)
    ck('  提示说明是 .py, 但不复述文件名', name not in msg and '.py' in msg, msg)
ok, _, sp = handle('gen.py', force=True)
ck('force 也拦', ok is False and not sp)
ok, _, sp = handle('mygame.cc')
ck('正常单文件照常受理', ok is True and len(sp) == 1, (ok, sp))

print('\n[6] 端到端: 带 .py 的压缩包走拒收收尾, 数据目录里一个 .py 都不出现')
config.update({'upload_dir': GAMES, 'enabled': True, 'allowed_groups': ['g'],
               'required_files': [], 'keep_archive': True, 'notify_users': ['DEV']})
sent = []


class Ev:
    group_id, user_id, username = 'g', 'U1', '上传者'

    async def reply(self, text):
        sent.append(text)
        return True

    async def send_to_group(self, gid, text):
        sent.append(text)


async def fake_download(url, max_bytes, timeout):
    return mkzip(['angry/mygame.cc', 'angry/rule.md', 'angry/tools/generate_assets.py']), ''


flow.quoted.download = fake_download
asyncio.run(flow._run(Ev(), config.all_config(), {'filename': 'angry.zip', 'url': 'u'},
                      config.upload_target(), '', False, True))
rec = store.list_records(1)[0]
ck('记录 stage=script', rec['stage'] == 'script' and 'generate_assets.py' in rec['error'], rec)
ck('群里说清楚是 .py, 并给出处理办法', any('压缩包含 .py 文件' in m and '删掉这些 .py' in m
                                         for m in sent), sent)
ck('  不 @ 开发者', not any('<@DEV>' in m for m in sent), sent)
ck('data/ 下一个 .py 都没有 (原件留档是 .zip, 解压前就拦下)', py_under(store.DATA_DIR) == [],
   py_under(store.DATA_DIR))
ck('游戏目录也没建出来', not os.path.exists(os.path.join(GAMES, 'angry')))

print('\n[7] 服务器上早就带 .py 的旧目录: 不挪进备份')
TARGET = config.upload_target()
old = os.path.join(GAMES, 'old')
for rel in ('mygame.cc', 'tools/gen.py', '__init__.py'):
    os.makedirs(os.path.dirname(os.path.join(old, rel)), exist_ok=True)
    open(os.path.join(old, rel), 'w').close()
ck('reload_triggers 与框架同一条规则 (_ 开头的不算)', deploy.reload_triggers(old) == ['tools/gen.py'],
   deploy.reload_triggers(old))
stg = tempfile.mkdtemp(dir=_env.SANDBOX)
open(os.path.join(stg, 'mygame.cc'), 'w').close()
res = deploy.deploy_archive(stg, '', 'old', TARGET, config.all_config())
ck('拒绝替换; 文件名只在明细里, 群里的说法不带', not res['ok'] and 'tools/gen.py' in res['detail']
   and 'tools/gen.py' not in res['error'], res)
ck('  旧目录原样、没挪进 backups', os.path.isfile(os.path.join(old, 'tools', 'gen.py'))
   and not [n for n in os.listdir(store.BACKUPS_DIR) if n.startswith('old.')])
ck('  新内容还在暂存目录里, 没动', os.listdir(stg) == ['mygame.cc'])
os.remove(os.path.join(old, 'tools', 'gen.py'))
res = deploy.deploy_archive(stg, '', 'old', TARGET, config.all_config())
ck('只剩 __init__.py (不触发重载) → 照常替换', res['ok'], res)

ck.done()
