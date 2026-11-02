# -*- coding: utf-8 -*-
"""/download 打包下载 (1.19.0, 1.19.1 起所有人可用) —— app/download.py。

要守住的:
1. zip 里是 <目录名>/<相对路径>, 与 /upload 收的结构一致, 内容逐字节不变;
2. 只打包普通文件: 符号链接 (可能指到游戏目录外) 不打包、目录链接不进入;
3. 名字只认上传目录下已有的直属目录: 不合法、越界、不存在、是文件都不行;
4. 超过上限 (官方单文件 200MB) 中途就停; 空目录、发送失败、意外异常都给说法;
5. 回复不复述指令参数; 群里看得到的 zip 文件名只在目录名过了审 (或没有上传记录) 时用目录名
   (见插件目录 CLAUDE.md);
6. 谁能用、在哪用: 主人哪儿都能用; 群友只在「生效范围」的群里, 别处与插件关闭时静默;
7. 限流: 同一时间只处理一个, 群友每人有冷却 (主人不受限), 打错名字不吃冷却, 收尾必释放;
8. main.py 的注册: 不再 owner_only, 也没有 group_only。
"""
import asyncio
import io
import os
import re
import time
import zipfile

import _env

store, config, download, flow = _env.boot('store', 'config', 'download', 'flow')
ck = _env.Checker()
store.init()

GAMES = os.path.join(_env.SANDBOX, 'games')
os.makedirs(GAMES, exist_ok=True)
config.update({'upload_dir': GAMES, 'enabled': True, 'allowed_groups': ['g1']})
TARGET = config.upload_target()
P = '违规探针'


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data if isinstance(data, bytes) else data.encode('utf-8'))


def game(name, files):
    root = os.path.join(GAMES, name)
    for rel, data in files.items():
        write(os.path.join(root, *rel.split('/')), data)
    return root


FILES = {'mygame.cc': 'int main(){}', 'rule.md': '规则', 'resource/a.png': b'\x89PNG\x00\x01',
         'resource/sub/b.txt': '深层'}
G = game('pk_game', FILES)

print('\n[1] pack: 结构与内容')
data, count = download.pack(G, 'pk_game')
zf = zipfile.ZipFile(io.BytesIO(data))
names = sorted(zf.namelist())
ck('文件数对得上', count == len(FILES), count)
ck('成员都在 <目录名>/ 之下 (与 /upload 的结构一致)', names == sorted(f'pk_game/{k}' for k in FILES), names)
ck('内容逐字节不变', all(zf.read(f'pk_game/{k}') == (v if isinstance(v, bytes) else v.encode())
                        for k, v in FILES.items()))

print('\n[2] pack: 链接不打包')
outside = os.path.join(_env.SANDBOX, 'outside_secret.txt')
write(outside, 'server secret')
os.makedirs(os.path.join(_env.SANDBOX, 'outside_dir'), exist_ok=True)
write(os.path.join(_env.SANDBOX, 'outside_dir', 'x.txt'), 'x')
L = game('pk_link', {'mygame.cc': 'x'})
try:
    os.symlink(outside, os.path.join(L, 'leak.txt'))
    os.symlink(os.path.join(_env.SANDBOX, 'outside_dir'), os.path.join(L, 'leakdir'),
               target_is_directory=True)
    linked = True
except (OSError, NotImplementedError):
    linked = False
if linked:
    data, count = download.pack(L, 'pk_link')
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    ck('指向目录外的文件链接不打包', 'pk_link/leak.txt' not in names, names)
    ck('目录链接不进入', not any(n.startswith('pk_link/leakdir') for n in names), names)
    ck('普通文件照常打包', names == ['pk_link/mygame.cc'], names)
else:
    ck.skip('真的符号链接', '这台机器建不了 (Windows 需要开发者模式), 下面用模拟的')
# 模拟: 普通文件 / 目录, 但让 os.path.islink 认它们是链接 —— 在建不了链接的机器上也走一遍跳过分支
write(os.path.join(L, 'fake_leak.txt'), 'leak')
write(os.path.join(L, 'fake_dir', 'y.txt'), 'y')
fakes = {os.path.join(L, 'fake_leak.txt'), os.path.join(L, 'fake_dir')}
real_islink = os.path.islink
os.path.islink = lambda p: os.path.normcase(os.path.abspath(p)) in {
    os.path.normcase(f) for f in fakes} or real_islink(p)
try:
    data, count = download.pack(L, 'pk_link')
finally:
    os.path.islink = real_islink
names = zipfile.ZipFile(io.BytesIO(data)).namelist()
ck('(模拟) 文件链接不打包、目录链接不进入, 只剩普通文件', names == ['pk_link/mygame.cc'], names)

print('\n[3] pack: 上限')
B = game('pk_big', {'a.bin': os.urandom(300_000), 'b.bin': os.urandom(300_000)})
try:
    download.pack(B, 'pk_big', limit=200_000)
    ck('超过上限 → TooLarge', False, '没抛')
except download.TooLarge:
    ck('超过上限 → TooLarge', True)
data, count = download.pack(B, 'pk_big', limit=10_000_000)
ck('上限够大照常打包', count == 2)

print('\n[4] find_game: 只认上传目录下已有的直属目录')
write(os.path.join(GAMES, 'pk_file'), 'not a dir')
os.makedirs(os.path.join(GAMES, '.hidden'), exist_ok=True)     # 真有这个目录, 名字照样不认
for name, why in (('pk_game', '已有目录'),):
    ck(why, download.find_game(TARGET, name) == os.path.realpath(G))
for name, why in (('不存在的', '不存在'), ('pk_file', '是文件不是目录'), ('..', '上一级'),
                  ('pk_game/resource', '多一层'), ('../games', '绕回自己'), ('.hidden', '以 . 开头'),
                  ('', '空')):
    ck(f'{why} → 不认', download.find_game(TARGET, name) == '', name)

print('\n[5] 指令流程 (生效群里的群友)')
SENT, FILES_SENT = [], []
OWNER = 'owner_openid'


class _BotCfg:
    """顶替框架的 core.base.config.cfg: appid 对得上才给 owner_ids。"""
    broken = False

    def get_bot_config(self, appid):
        if self.broken:
            raise RuntimeError('配置读不出来')
        return {'appid': 'app1', 'owner_ids': [OWNER]} if appid == 'app1' else None


BOT_CFG = _BotCfg()
_env._mod('core.base.config', cfg=BOT_CFG)


class Ev:
    appid, username = 'app1', '群友'

    def __init__(self, fail=None, user='u1', group='g1'):
        self.fail = fail
        self.error = None
        self.user_id, self.group_id = user, group

    async def reply(self, text):
        SENT.append(text)
        return True

    async def send_to_group(self, gid, text):
        SENT.append(text)

    async def reply_file(self, data, content='', *, file_name=None, **kw):
        if self.fail:
            self.error = self.fail
            return None
        FILES_SENT.append((data, content, file_name))
        return {'id': 'm'}


def run(arg, ev=None, fresh=True):
    """跑 handle; 起了后台任务就把它跑完。返回 (这次新发的消息, 起没起任务)。

    fresh: 先清掉冷却记录 —— 只有专测限流的用例才留着上一次的。
    """
    ev = ev or Ev()
    if fresh:
        download._last.clear()
    spawned = []
    saved = flow._spawn
    flow._spawn = lambda coro: (spawned.append(coro), None)[1]
    s = len(SENT)
    try:
        asyncio.run(download.handle(ev, arg))
        for coro in spawned:
            asyncio.run(coro)
    finally:
        flow._spawn = saved
    return SENT[s:], bool(spawned)


said, spawned = run('pk_game')
data, content, fname = FILES_SENT[-1]
ck('发出了 zip, 文件名是 <目录名>.zip', spawned and fname == 'pk_game.zip', fname)
ck('  发出的就是打好的包', sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
   == sorted(f'pk_game/{k}' for k in FILES))
ck('  附带文件数与大小', '共 4 个文件' in content and 'MB' in content, content)
ck('  先回了一句「正在打包」', said and '正在打包' in said[0], said)

for arg, why, want in ((f'{P}', '目录不存在', '没有这个游戏目录'), ('', '没给参数', '用法'),
                       ('a b', '参数过多', '用法'), (f'{P}..x', '名字不合法', '名称不合法')):
    said, spawned = run(arg)
    ck(f'{why} → 拒绝, 不起任务', not spawned and any(want in m for m in said), said)
    ck(f'  不复述参数', not any(P in m for m in said), said)

game('pk_empty', {})
os.makedirs(os.path.join(GAMES, 'pk_empty'), exist_ok=True)
said, _ = run('pk_empty')
ck('空目录 → 说清楚', any('是空的' in m for m in said), said)

saved_max = download.MAX_BYTES
download.MAX_BYTES = 100_000
try:
    said, _ = run('pk_big')
finally:
    download.MAX_BYTES = saved_max
ck('超过上限 → 说明上限, 不发', any('无法发送' in m for m in said), said)

said, _ = run('pk_game', Ev(fail={'code': 40093002, 'message': '超过今日上限'}))
ck('发送失败 (当日容量) → 报错误码与说明', any('40093002' in m and '明天再试' in m for m in said), said)
said, _ = run('pk_game', Ev(fail='weird'))
ck('发送失败 (认不出的响应) → 也有一句', any('文件发送失败' in m for m in said), said)


class EvBoom(Ev):
    async def reply_file(self, *a, **k):
        raise RuntimeError(f'上传炸了 /srv/games/{P}.zip')


real_pack = download.pack


def pack_boom(*a, **k):
    raise ValueError(f'意外 /srv/games/{P}')


for ev, patch, why in ((EvBoom(), None, '发送时抛异常'), (Ev(), pack_boom, '打包时抛非读盘异常')):
    if patch:
        download.pack = patch
    try:
        said, spawned = run('pk_game', ev)
    finally:
        download.pack = real_pack
    ck(f'{why} → 回一句固定说法', spawned and any('出现异常' in m for m in said), said)
    ck(f'  异常原文不进群', not any(P in m or '/srv' in m for m in said), said)
    ck(f'  释放了占位标记', download._busy is False)

config.update({'enabled': False})
try:
    said, spawned = run('pk_game')
    said_o, spawned_o = run('pk_game', Ev(user=OWNER))
finally:
    config.update({'enabled': True})
ck('插件关闭 → 群友: 静默', not spawned and not said, said)
ck('插件关闭 → 主人: 明确回一句', not spawned_o and any('插件已在后台面板关闭' in m for m in said_o), said_o)

print('\n[6] 谁能用、在哪用')
for ev, why in ((Ev(group='g2'), '不在生效范围的群'), (Ev(group=''), '私聊'),
                (Ev(group=None), '私聊 (group_id 为 None)')):
    said, spawned = run('pk_game', ev)
    ck(f'群友在{why} → 静默, 不起任务', not spawned and not said, said)
for ev, why in ((Ev(user=OWNER, group=''), '私聊'), (Ev(user=OWNER, group='g2'), '不在生效范围的群')):
    n = len(FILES_SENT)
    said, spawned = run('pk_game', ev)
    ck(f'主人在{why} → 照常发', spawned and len(FILES_SENT) == n + 1, said)
ck('主人认得出来', download._is_owner(Ev(user=OWNER)))
other_bot = Ev(user=OWNER)
other_bot.appid = 'app2'
ck('别的 bot (appid 对不上) 的主人不算', not download._is_owner(other_bot))
ck('没有 user_id 的不算', not download._is_owner(Ev(user='')))
BOT_CFG.broken = True
try:
    ck('配置读不出来 → 按群友处理', not download._is_owner(Ev(user=OWNER)))
finally:
    BOT_CFG.broken = False

print('\n[7] 限流: 同一时间一个、群友每人有冷却 (主人不受限)')
said, spawned = run('pk_game')
ck('第一次照常发', spawned, said)
said, spawned = run('pk_game', fresh=False)
ck('冷却中再来 → 拒绝, 不起任务', not spawned and any('下载冷却中' in m for m in said), said)
secs = [int(x) for m in said for x in re.findall(r'请 (\d+) 秒后再试', m)]
ck('  说清还要等几秒 (1..冷却时长)', secs and 0 < secs[0] <= download._COOLDOWN, said)
said, spawned = run('pk_game', Ev(user='u2'), fresh=False)
ck('别人不受这个人的冷却影响', spawned, said)
run(P, Ev(user='u3'), fresh=False)
said, spawned = run('pk_game', Ev(user='u3'), fresh=False)
ck('打错名字不吃冷却', spawned, said)
download._last['stale'] = time.monotonic() - download._COOLDOWN - 1
download._last['u1'] -= download._COOLDOWN          # 把 u1 上次的时刻往前拨一整个冷却期
said, spawned = run('pk_game', fresh=False)
ck('冷却过了照常发', spawned, said)
ck('  冷却过了的记录顺手清掉, 不会越攒越多', 'stale' not in download._last, sorted(download._last))
said1, sp1 = run('pk_game', Ev(user=OWNER), fresh=False)
said2, sp2 = run('pk_game', Ev(user=OWNER), fresh=False)
ck('主人连着下不受冷却限制', sp1 and sp2, said2)

# 并发: 第一个的后台任务还没跑完, 第二条指令就到了
download._last.clear()
spawned = []
saved = flow._spawn
flow._spawn = lambda coro: (spawned.append(coro), None)[1]
s = len(SENT)
try:
    async def two():
        return (await download.handle(Ev(user='u1'), 'pk_game'),
                await download.handle(Ev(user='u2'), 'pk_game'))
    first, second = asyncio.run(two())
    ck('前一个还没发完 → 后一个拒绝', first and not second and len(spawned) == 1
       and any('已有下载任务' in m for m in SENT[s:]), SENT[s:])
    ck('  被拒的不吃冷却', 'u2' not in download._last, sorted(download._last))
    ck('  处理中一直占着标记', download._busy is True)
    for coro in spawned:
        asyncio.run(coro)
finally:
    flow._spawn = saved
ck('发完释放标记', download._busy is False)
said, spawned = run('pk_game', Ev(user='u2'), fresh=False)
ck('  释放后后一个人能下了', spawned, said)
for ev, why in ((Ev(fail={'code': 40093002}), '发送失败'),):
    run('pk_game', ev)
    ck(f'{why}后也释放标记', download._busy is False)
saved_max = download.MAX_BYTES
download.MAX_BYTES = 100_000
try:
    run('pk_big')
finally:
    download.MAX_BYTES = saved_max
ck('超限后也释放标记', download._busy is False)
run('pk_empty')
ck('空目录后也释放标记', download._busy is False)
download._busy = False


def boom(coro):
    coro.close()
    raise RuntimeError('建不了任务')


saved = flow._spawn
flow._spawn = boom
try:
    asyncio.run(download.handle(Ev(user='u9'), 'pk_game'))
    ck('建任务失败 → 异常照常抛出', False, '没抛')
except RuntimeError:
    ck('建任务失败 → 异常照常抛出', True)
finally:
    flow._spawn = saved
ck('  且不留着占位标记', download._busy is False)

print('\n[8] zip 文件名: 群里看得到, 目录名过了审才用')
_seq = iter(range(1000))


def rec(verdict, stage='deployed', folder='pk_game'):
    store.add_record({'id': f'rz-{next(_seq)}', 'stage': stage, 'folder': folder, 'verdict': verdict})


ck('没有上传记录 (上游自带 / 管理员手放) → 用目录名', download._zip_name('pk_game') == 'pk_game.zip')
for verdict, stage, want, why in (
        ('skip', 'deployed', 'lgtbot_game.zip', '面板关了审核 (skip)'),
        ('force', 'deployed', 'lgtbot_game.zip', 'force 强制上传'),
        ('pass', 'deployed', 'pk_game.zip', '审核通过'),
        ('fail', 'review', 'pk_game.zip', '之后又来一个没落地的 (不算)'),
        ('skip', 'recompile', 'lgtbot_game.zip', '重新编译, 沿用的是 skip'),
        ('pass', 'recompile', 'pk_game.zip', '重新编译, 沿用的是 pass')):
    rec(verdict, stage)
    got = download._zip_name('pk_game')
    ck(f'最近一次: {why} → {want}', got == want, got)
rec('skip', 'deployed', folder='pk_empty')
ck('别的目录的记录不串', download._zip_name('pk_game') == 'pk_game.zip')
rec('force')
said, spawned = run('pk_game')
fname, content = FILES_SENT[-1][2], FILES_SENT[-1][1]
ck('走完整流程: 没审过的目录 → 发出的是中性文件名', spawned and fname == 'lgtbot_game.zip', fname)
ck('  消息与文件说明里都没有目录名', not any('pk_game' in m for m in said + [content]), said + [content])
rec('pass')

print('\n[9] 注册: 所有人可用, 群聊私聊都进得来')
src = io.open(os.path.join(_env.TEST_ENV, 'main.py'), encoding='utf-8').read()
m = re.search(r"@handler\(r'\^/\?download[\s\S]*?\)\nasync def (\w+)", src)
deco = m.group(0) if m else ''
ck('main.py 注册了 /download', bool(m), '没找到')
ck('  不再 owner_only', 'owner_only' not in deco, deco)
ck('  没有 group_only (主人私聊也能用)', 'group_only' not in deco, deco)
ck('  调的是 download.handle', m and 'download.handle' in src[m.end():m.end() + 200])
lines = flow.usage().splitlines()
i = lines.index('/download <文件夹名>') if '/download <文件夹名>' in lines else 0
ck('帮助里 /download 那条不再标「仅主人」', i and '仅主人' not in lines[i - 1], lines[i - 1] if i else '没找到')

print('\n[10] 不在插件数据目录落盘')
before = {os.path.join(d, f) for d, _, fs in os.walk(store.DATA_DIR) for f in fs}
run('pk_game')
after = {os.path.join(d, f) for d, _, fs in os.walk(store.DATA_DIR) for f in fs}
ck('打包下载前后 data/ 里没有多出文件', after == before, sorted(after - before))

ck.done()
