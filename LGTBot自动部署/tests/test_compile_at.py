# -*- coding: utf-8 -*-
"""编译结论 @ 谁 (1.20.1) —— flow._compile_text + compile.needs_admin。

编译失败 / 超时 / 异常 @ 上传者 (编译器报错改代码重传, 临时问题稍后 /compile);
只有服务器 / 配置那边的问题 (接口配错、token 错、目标名过不了白名单之类) 才 @ 开发者,
而且不给上传者「改代码」「稍后重编」这种不对症的出路。「上传者」是发起这次的人
(/compile 的记录里就是发指令的那位); 开发者那边 force 一律不 @。
最后跑真的 _run / _run_recompile, 确认流程里发出去的就是这条。
"""
import asyncio
import io
import os
import zipfile

import _env

store, config, flow, compilemod, review = _env.boot('store', 'config', 'flow', 'compile', 'review')
ck = _env.Checker()
store.init()

CFG = {'notify_users': ['DEV'], 'compile_timeout': 180}
U, DEV = '<@u1>', '<@DEV>'


def rec(**kw):
    base = {'id': 'r1', 'user_id': 'u1', 'verdict': 'pass', 'folder': '', 'deploy_name': 'g',
            'mode': 'archive', 'target': 'lgtbot', 'filename': 'g.zip', 'is_new': False,
            'forced': False, 'stage': 'deployed'}
    base.update(kw)
    return base


def text(result, record=None):
    return flow._compile_text(CFG, record or rec(), result, 'g')


CODE = {'status': 'failed', 'ok': False, 'http_status': 500, 'returncode': 2,
        'error': '编译失败 (退出码 2)'}
BUSY = {'status': 'failed', 'ok': False, 'http_status': 409, 'error': '已有编译在进行中'}
DOWN = {'status': 'failed', 'ok': False, 'http_status': 503, 'error': '编译服务未就绪'}
TIMEOUT = {'status': 'timeout', 'ok': False, 'terminate': {'ok': True}, 'error': '等待 180 秒无响应'}
NET = {'status': 'error', 'ok': False, 'error': 'ClientConnectorError: 连不上'}
TOKEN = {'status': 'failed', 'ok': False, 'http_status': 401, 'error': 'token 缺失或错误'}
BAD_REQ = {'status': 'failed', 'ok': False, 'http_status': 400, 'error': '缺少 target'}
NO_RC = {'status': 'failed', 'ok': False, 'http_status': 500, 'error': 'build.sh 执行异常'}
NOT_FOUND = {'status': 'failed', 'ok': False, 'http_status': 404, 'error': 'HTTP 404: Not Found'}
MISCONFIG = {'status': 'misconfig', 'ok': False, 'error': '编译 API 地址不是完整 URL'}
INVALID = {'status': 'invalid', 'ok': False, 'error': '目录名不符合编译 API 命名规则'}
OK = {'status': 'success', 'ok': True, 'http_status': 200, 'elapsed_sec': 3}

print('\n[1] compile.needs_admin')
for res, want, why in ((CODE, False, '编译器报错'), (BUSY, False, '409 编译进程被占用'),
                       (DOWN, False, '503 服务未就绪'), (TIMEOUT, False, '超时'),
                       (NET, False, '网络异常'), (TOKEN, True, '401 token 错'), (BAD_REQ, True, '400'),
                       (NO_RC, True, '500 但没有编译器输出'), (NOT_FOUND, True, '404 (接口路径不对)'),
                       (MISCONFIG, True, '接口地址配错'), (INVALID, True, '目标名过不了白名单'),
                       (OK, False, '成功'), ({'status': 'disabled', 'ok': False}, False, '未启用编译'),
                       ({}, False, '空')):
    ck(f'{why} → {"管理员" if want else "上传者"}', compilemod.needs_admin(res) is want, res)

print('\n[2] 上传者能处理的 → @ 上传者, 不 @ 开发者')
for res, tip, why in ((CODE, '修改源码', '编译器报错 → 改代码重传'), (BUSY, '/compile', '409 → 稍后重编'),
                      (DOWN, '/compile', '503 → 稍后重编'), (TIMEOUT, '/compile', '超时 → 稍后重编'),
                      (NET, '/compile', '网络异常 → 稍后重编')):
    t = text(res)
    ck(why, f'{U} 请复查编译问题' in t and DEV not in t, t)
    ck('  给出路', tip in t, t)

print('\n[3] 服务器 / 配置那边的 → @ 开发者, 不 @ 上传者, 也不给不对症的出路')
for res, why in ((TOKEN, '401 token 错'), (BAD_REQ, '400'), (NO_RC, '500 没有编译器输出'),
                 (NOT_FOUND, '404'), (MISCONFIG, '接口地址配错'), (INVALID, '目标名过不了白名单')):
    t = text(res)
    ck(why, f'{DEV} 请复查编译问题' in t and U not in t, t)
    ck('  不叫上传者改代码或重编', '修改源码' not in t and '/compile' not in t, t)

print('\n[4] 谁算上传者 / force / 没有 user_id')
t = text(CODE, rec(stage='recompile', user_id='u9', uploader_id='u1'))
ck('/compile 的失败 → @ 发指令的那位', '<@u9> 请复查编译问题' in t and U not in t and DEV not in t, t)
t = text(CODE, rec(forced=True, verdict='force'))
ck('force 的编译器报错 → 照样 @ 上传者 (主人自己)', f'{U} 请复查编译问题' in t and DEV not in t, t)
t = text(TOKEN, rec(forced=True, verdict='force'))
ck('force 的服务器问题 → 开发者那边照旧不 @', DEV not in t and U not in t, t)
t = text(CODE, rec(user_id=''))
ck('没有 user_id → 不出空 @', '<@' not in t and '请复查编译问题' not in t, t)

print('\n[5] 其余消息不变')
t = text(OK)
ck('编译成功 (常规更新) → 只 @ 上传者', U in t and DEV not in t, t)
t = text(OK, rec(is_new=True))
ck('编译成功 (新游戏) → @ 上传者', U in t and DEV not in t, t)
t = flow._compile_text(CFG, rec(is_new=True), OK, 'g',
                       restart={'status': 'failed', 'ok': False, 'error': '维护模式未生效'})
ck('计划重启请求失败 → 仍然 @ 开发者手动安排 (运维的事)', f'{DEV} 计划重启未生效' in t, t)

# ---- 端到端: 真的 _run / _run_recompile, 下载 / 审核 / 编译用假函数 ----
print('\n[6] 端到端')
GAMES = os.path.join(_env.SANDBOX, 'games')
os.makedirs(GAMES, exist_ok=True)
config.update({'upload_dir': GAMES, 'enabled': True, 'allowed_groups': ['g1'],
               'required_files': [], 'keep_archive': False, 'report_base_url': '',
               'review_enabled': True, 'compile_enabled': True, 'notify_users': ['DEV']})
TARGET = config.upload_target()
SENT, PACKAGE, NEXT = [], {}, []
PASS = {'verdict': 'pass', 'categories': [], 'manual': False, 'model': 'fake', 'error': '',
        'http_status': 200, 'raw': '{}', 'elapsed': 0.1, 'criteria': [], 'findings': [],
        'game_name': '', 'game_desc': '', 'retryable': True}


class Ev:
    group_id, username = 'g1', '上传者'

    def __init__(self, user='u1'):
        self.user_id = user

    async def reply(self, text):
        SENT.append(text)
        return True

    async def send_to_group(self, gid, text):
        SENT.append(text)


async def fake_download(url, max_bytes, timeout):
    return PACKAGE['data'], ''


async def fake_review(pkg, meta, cfg):
    return dict(PASS)


async def fake_compile(game_name, cfg, is_new=False):
    res = dict(NEXT.pop(0)) if NEXT else dict(OK)
    res['new'] = bool(is_new)
    return res


async def fake_restart(cfg, reason, auto=True):
    return {'status': 'success', 'ok': True, 'enabled': True, 'auto': True, 'reason': reason,
            'active_matches': 0, 'http_status': 200}


flow.quoted.download = fake_download
review.review = fake_review
compilemod.request_compile = fake_compile
compilemod.request_planned_restart = fake_restart


def mkzip(root, ver):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, body in (('mygame.cc', ver), ('rule.md', 'rule')):
            zf.writestr(zipfile.ZipInfo(f'{root}/{name}', date_time=(2026, 1, 1, 0, 0, 0)), body)
    return buf.getvalue()


def upload(name, ver='v1'):
    PACKAGE['data'] = mkzip(name, ver)
    s = len(SENT)
    asyncio.run(flow._run(Ev(), config.all_config(), {'filename': name + '.zip', 'url': 'u'},
                          TARGET, '', False, True))
    return SENT[s:]


NEXT[:] = [CODE]
said = upload('at_code')
ck('上传后编译器报错 → 结论 @ 上传者, 不 @ 开发者',
   said and f'{U} 请复查编译问题' in said[-1] and DEV not in said[-1], said)
NEXT[:] = [TOKEN]
said = upload('at_token')
ck('上传后编译接口 401 → @ 开发者, 不 @ 上传者',
   said and f'{DEV} 请复查编译问题' in said[-1] and U not in said[-1], said)
NEXT[:] = [BUSY]
upload('at_busy')
NEXT[:] = [BUSY]
s = len(SENT)
asyncio.run(flow._run_recompile(Ev('u1'), config.all_config(), 'at_busy',
                                store.last_game_record('at_busy')))
said = SENT[s:]
ck('/compile 又遇到临时问题 → @ 发指令的人', said and f'{U} 请复查编译问题' in said[-1]
   and DEV not in said[-1], said)

ck.done()
