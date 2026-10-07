"""审核通过后把文件落到 lgtbot 上传目录; 编译没过再撤回来。

两种落地方式, 对应两种指令用法:
  · 压缩包  ``/upload``            → 解压到 ``<上传目录>/<压缩包名>/``
  · 单文件  ``/upload <文件夹名>``  → 写入 ``<上传目录>/<文件夹名>/<文件名>``

同名目录 / 同名文件一律**直接替换**; 被替换的旧内容**一律先挪进** ``data/backups/``
(目录备份为 ``<名称>.<时间戳>/``, 单文件备份为 ``<文件夹>/<文件名>.<时间戳>``),
不再按上传目标分子目录。不管面板开没开「替换前备份」都先挪 —— 编译出结论之前,
这份旧内容是回滚 (``rollback``) 的唯一来源; 没开备份时它只是临时的, 编译成功后由
flow 调 ``discard_backup`` 删掉, 效果与原先「直接删」一样。

目标路径先经名称合法性校验, 落地前再用 realpath 确认没有跳出上传目录, 防止靠构造
压缩包名 / 文件夹名穿越目录。回滚同样只认「上传目录下一层 (游戏目录) 或两层 (单
文件)」的那一个路径 —— 它来自记录文件, 不能被拿去删上传目录以外的东西。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import time

from . import store

_ARCHIVE_SUFFIXES = ('.tar.gz', '.tar.bz2', '.tar.xz', '.tgz', '.tbz2', '.txz',
                     '.tar', '.zip', '.rar', '.7z')


def strip_archive_ext(name: str) -> str:
    """去掉压缩包扩展名 (含 .tar.gz 这类复合扩展)。"""
    base = (name or '').strip()
    low = base.lower()
    for ext in _ARCHIVE_SUFFIXES:
        if low.endswith(ext):
            return base[: -len(ext)]
    return base


def bad_name(name: str) -> str:
    """校验用户可控的目录名 / 压缩包名; 返回错误信息, 空串表示合法。"""
    if not name:
        return '名称为空'
    if '/' in name or '\\' in name or '..' in name:
        return f'名称不合法: {name}'
    if name.startswith('.') or name in ('.', '..'):
        return f'名称不合法: {name}'
    if any(c in name for c in ':*?"<>|'):
        return f'名称含非法字符: {name}'
    return ''


def check_target(target: dict) -> str:
    """校验上传目录配置; 返回错误信息, 空串表示可用。"""
    key = (target or {}).get('key') or '上传'
    path = (target or {}).get('path') or ''
    if not path:
        return f'{key} 上传目录未配置, 请在后台面板「LGTBot 自动部署」页填写'
    if not os.path.isabs(path):
        return f'{key} 上传目录必须是绝对路径: {path}'
    if not os.path.isdir(path):
        return f'{key} 上传目录不存在或不是目录: {path}'
    return ''


def _inside(base_real: str, path_real: str) -> bool:
    return path_real == base_real or path_real.startswith(base_real + os.sep)


def _out(ok: bool, name: str, error: str = '', dest: str = '', backup: str = '',
         note: str = '', replaced: bool = False, backup_temp: bool = False) -> dict:
    """deploy_* 的统一返回。``replaced`` = 落地前这里本来就有东西 (回滚时据此决定
    是放回旧版本还是只撤掉新内容); ``backup_temp`` = 备份只为回滚而留, 编译成功后要删。"""
    return {'ok': ok, 'error': error, 'dest': dest, 'name': name, 'backup': backup,
            'note': note, 'replaced': replaced, 'backup_temp': backup_temp}


def _stash(src: str, rel: str) -> str:
    """把将被替换的旧目录 / 旧文件挪进 ``data/backups/``, 返回备份路径。

    一律挪走、不直接删 —— 见模块 docstring。
    """
    dest = os.path.join(store.BACKUPS_DIR, f'{rel}.{time.strftime("%Y%m%d-%H%M%S")}')
    # 时间戳只有秒级: 同一秒内两次替换同一目标会撞名, shutil.move 会把新备份
    # 塞进旧备份目录里 (或直接报错), 撞名时追加序号保证路径唯一
    base, i = dest, 1
    while os.path.exists(dest):
        dest = f'{base}-{i}'
        i += 1
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(src, dest)
    return dest


def deploy_archive(staging: str, root: str, base_name: str, target: dict, cfg: dict) -> dict:
    """解压结果 → ``<target.path>/<base_name>/``。

    ``root`` 是压缩包内唯一顶层目录名: 当它与 ``base_name`` 同名时把它的内容提上来
    (避免出现 ``五子棋/五子棋/...`` 的重复层级), 其余情况保持压缩包原结构。
    /compile 换回暂存的新代码也走这里 (``root`` 传空, 整个目录原样挪过去)。

    返回 ``{ok, dest, name, backup, note, error, replaced, backup_temp}``。
    """
    err = check_target(target) or bad_name(base_name)
    if err:
        return _out(False, base_name, err)

    base_real = os.path.realpath(target['path'])
    dest = os.path.realpath(os.path.join(base_real, base_name))
    if os.path.dirname(dest) != base_real:
        return _out(False, base_name, f'目标路径越界: {base_name}')

    replaced = os.path.exists(dest)
    backup = ''
    if replaced:
        try:
            backup = _stash(dest, base_name)
        except OSError as e:
            return _out(False, base_name, f'替换旧目录失败: {e}', dest)
    temp = replaced and not cfg.get('keep_replaced_backup', True)

    note = ''
    src = staging
    if root and root == base_name:
        src = os.path.join(staging, root)
        note = '（已提升压缩包内同名文件夹）'
    try:
        shutil.move(src, dest)
    except OSError as e:
        try:
            shutil.copytree(src, dest)
        except OSError as e2:
            # 旧目录已经挪走、新的没放进去 (或只放了一半): 当场复原, 别在 games/ 下
            # 留个半截目录 —— 那和编不过的代码一样会卡住下次完整编译
            undo = rollback(target, dest, backup, replaced)
            err = f'部署失败: {e} / {e2}'
            if not undo['ok']:
                err += f'; 恢复原状也失败了: {undo["error"]}'
            return _out(False, base_name, err, dest, '' if undo['ok'] else backup)
    return _out(True, base_name, dest=dest, backup=backup, note=note,
                replaced=replaced, backup_temp=temp)


def deploy_single(data: bytes, filename: str, folder: str, target: dict, cfg: dict) -> dict:
    """单文件 → ``<target.path>/<folder>/<filename>``。

    目标文件夹**必须已存在** (避免打错字凭空建目录); 同名文件直接替换。
    返回 ``{ok, dest, name, backup, note, error, replaced, backup_temp}``。
    """
    err = check_target(target) or bad_name(folder) or bad_name(filename)
    if err:
        return _out(False, folder, err)

    base_real = os.path.realpath(target['path'])
    folder_real = os.path.realpath(os.path.join(base_real, folder))
    if os.path.dirname(folder_real) != base_real:
        return _out(False, folder, f'目标路径越界: {folder}')
    if not os.path.isdir(folder_real):
        return _out(False, folder, f'目标下不存在文件夹「{folder}」, 请检查文件夹名称')

    dest = os.path.realpath(os.path.join(folder_real, filename))
    if not _inside(folder_real, dest) or os.path.dirname(dest) != folder_real:
        return _out(False, folder, f'目标路径越界: {filename}')

    replaced = os.path.exists(dest)
    backup, note = '', ''
    if replaced:
        if os.path.isdir(dest):
            return _out(False, folder, f'目标下已存在同名文件夹「{filename}」', dest)
        try:
            backup = _stash(dest, os.path.join(folder, filename))
        except OSError as e:
            return _out(False, folder, f'替换旧文件失败: {e}', dest)
        note = '（已替换同名文件）'
    temp = replaced and not cfg.get('keep_replaced_backup', True)
    try:
        with open(dest, 'wb') as f:
            f.write(data)
    except OSError as e:
        undo = rollback(target, dest, backup, replaced)
        err = f'写入失败: {e}'
        if not undo['ok']:
            err += f'; 恢复原状也失败了: {undo["error"]}'
        return _out(False, folder, err, dest, '' if undo['ok'] else backup)
    return _out(True, folder, dest=dest, backup=backup, note=note,
                replaced=replaced, backup_temp=temp)


# ==================== 编译没过: 撤回 ====================

def _live_path(target: dict, dest: str) -> str:
    """回滚要动的路径: 只认上传目录下一层 (游戏目录) 或两层 (单文件), 否则返回空串。"""
    base = (target or {}).get('path') or ''
    if not base or not dest:
        return ''
    base_real = os.path.realpath(base)
    real = os.path.realpath(dest)
    parent = os.path.dirname(real)
    if parent == base_real or os.path.dirname(parent) == base_real:
        return real
    return ''


def _under(root: str, path: str) -> str:
    """``path`` 落在 ``root`` 里 (不含 root 本身) 就返回其 realpath, 否则空串。"""
    if not path:
        return ''
    root_real = os.path.realpath(root)
    real = os.path.realpath(path)
    return real if real.startswith(root_real + os.sep) else ''


def _touch_tree(path: str):
    """把放回 games/ 的文件的修改时间刷成现在。

    make 只按修改时间判断要不要重编。放回去的旧版本带着当初的时间戳, 而编译目录里
    可能还留着新版本编出来的 .o (编译过了、链接没过) —— 它比旧源码新, 下次编译就
    直接拿它去链接, 同样的链接错误再来一遍, 照样卡住。/compile 换回的暂存同理: 期间
    旧版本若被重编过, 暂存的源码反倒比 .o 旧, 会被当成「不用编」。刷新时间戳, 逼它
    按 games/ 里现在的源码重编。
    """
    now = time.time()
    if os.path.isdir(path):
        files = [os.path.join(d, f) for d, _, fs in os.walk(path) for f in fs]
    else:
        files = [path]
    for f in files:
        with contextlib.suppress(OSError):
            os.utime(f, (now, now))


def _remove(path: str):
    """删掉 games/ 下的一个目录或文件。

    先整个挪进 data/staging 再删: 同一文件系统下这一步是一次 rename, games/ 下要么
    整份还在、要么整份没了, 不会因为删到一半出错而留个半截目录。
    """
    os.makedirs(store.STAGING_DIR, exist_ok=True)
    trash = os.path.join(store.STAGING_DIR, f'_rollback-{os.urandom(4).hex()}')
    shutil.move(path, trash)
    if os.path.isdir(trash) and not os.path.islink(trash):
        shutil.rmtree(trash, ignore_errors=True)
    else:
        with contextlib.suppress(OSError):
            os.remove(trash)


def rollback(target: dict, dest: str, backup: str, replaced: bool, park: str = '') -> dict:
    """把一次落地撤回去: 新内容拿走, 原来有东西就从 ``backup`` 放回原位。

    ``park`` 为空 → 新内容直接删 (编译器报错, 这份代码不会再用);
    非空 → 挪到这个路径暂存 (临时性失败, 留给 /compile 换回来再编), 须在 data/pending/ 里。
    ``replaced`` = 落地前这里本来就有东西。有的话必须能从 backup 恢复 —— 备份找不到
    就**一个文件都不动**: 撤掉新的却放不回旧的, 等于把这个游戏的源码整个弄没了。

    返回 ``{ok, action, error, parked, taken}``; action: ``restored`` 已恢复上一版本 /
    ``removed`` 原本没有, 已移除; taken = 新内容已经从 games/ 拿走 (失败时据此分清
    是「什么都没动」还是「撤下了新的、旧的没放回去」)。
    """
    out = {'ok': False, 'action': '', 'error': '', 'parked': '', 'taken': False}
    live = _live_path(target, dest)
    if not live:
        out['error'] = f'路径不在上传目录内, 拒绝回滚: {dest}'
        return out
    old = ''
    if replaced:
        old = _under(store.BACKUPS_DIR, backup)
        if not old or not os.path.exists(old):
            out['error'] = '找不到上一版本的备份, 服务器上的文件没有动'
            return out
    if park and not _under(store.PENDING_DIR, park):
        out['error'] = f'暂存路径不在 data/pending 内, 拒绝回滚: {park}'
        return out

    try:
        if os.path.lexists(live):
            if park:
                os.makedirs(os.path.dirname(park), exist_ok=True)
                shutil.move(live, park)
                out['parked'] = park
            else:
                _remove(live)
    except OSError as e:
        out['error'] = f'撤下新内容失败: {e}'
        return out
    out['taken'] = True

    if not replaced:
        out.update(ok=True, action='removed')
        return out
    try:
        shutil.move(old, live)
    except OSError as e:
        out['error'] = (f'新内容已撤下, 但上一版本没能放回: {e} '
                        f'(备份仍在 backups/{os.path.basename(old)})')
        return out
    _touch_tree(live)
    out.update(ok=True, action='restored')
    return out


def describe_rollback(record: dict) -> str:
    """服务器上的代码现在是什么样 —— 一句话, 只有固定文案, 不带报错原文。

    群消息、留档、报告页共用这一套说法 (面板 panel.html 的 rollbackText 与之对应)。
    报错原文 (带服务器路径) 只进后台留档, 不进群、不进公网上的报告页。
    """
    rb = (record or {}).get('rollback') or {}
    if not rb:
        return ''
    single = record.get('mode') == 'file'
    if rb.get('skipped'):
        return '这次没有自动回滚 (依据的是旧版本插件的记录), 新代码仍在服务器上'
    if not rb.get('ok'):
        if not rb.get('taken'):
            return '自动回滚失败, 新代码仍在服务器上'
        return (('新代码已暂存待重编' if rb.get('pending') else '新代码已撤下')
                + ', 但上一版本没能放回服务器')
    if rb.get('pending'):
        now = ('服务器上暂时恢复为上一版本' if rb.get('action') == 'restored'
               else '新加的文件暂时从服务器上撤下' if single
               else '新游戏目录暂时从服务器上撤下')
        return f'新代码已暂存待重编, {now}'
    if rb.get('action') == 'restored':
        return '已回滚: 编不过的新代码已撤下, 服务器上恢复为上一版本'
    if single:
        return '已回滚: 新加的文件已从服务器上撤下'
    return ('已回滚: 新游戏目录已移除, 修改后重新上传仍按新游戏处理'
            + (', 自动绑定的目录权限已一并收回' if rb.get('unbound') else ''))


def discard_backup(backup: str) -> bool:
    """删掉只为回滚临时留的旧内容 (面板没开「替换前备份」, 而编译已经成功)。"""
    path = _under(store.BACKUPS_DIR, backup)
    if not path or not os.path.lexists(path):
        return False
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        with contextlib.suppress(OSError):
            os.remove(path)
    # 单文件备份在 backups/<文件夹>/ 下, 删空了就把这层也收掉
    parent = os.path.dirname(path)
    if parent != os.path.realpath(store.BACKUPS_DIR):
        with contextlib.suppress(OSError):
            os.rmdir(parent)
    return not os.path.lexists(path)


def deploy_pending(pending: str, game: str, folder: str, target: dict, cfg: dict) -> dict:
    """把暂存的新内容重新落地 (/compile 用), 返回值与 deploy_archive / deploy_single 相同。

    ``folder`` 非空 = 单文件上传, 暂存的是那一个文件; 否则暂存的是整个游戏目录。
    """
    path = _under(store.PENDING_DIR, pending)
    if not path or not os.path.exists(path):
        return _out(False, folder or game, '暂存的新代码已不在 (可能已在后台被清理)')
    if folder:
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError as e:
            return _out(False, folder, f'读取暂存失败: {e}')
        res = deploy_single(data, os.path.basename(path), folder, target, cfg)
        if res['ok']:
            with contextlib.suppress(OSError):
                os.remove(path)
    else:
        res = deploy_archive(path, '', game, target, cfg)
        if res['ok']:
            _touch_tree(res['dest'])        # 单文件那条是重新写入的, 时间戳本来就是新的
    if res['ok']:
        # data/pending/<记录号>/ 这一层空了就收掉
        with contextlib.suppress(OSError):
            os.rmdir(os.path.dirname(path))
    return res
