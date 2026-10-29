"""入群自动审核: 监听群收到入群申请时, 按申请人的回答自动通过 / 拒绝, 或提示人工处理

配置 data/入群自动审核_config.yaml (首次加载自动生成; 每次收到申请现读, 改完即时生效):
  groups    : 监听的群 ID 列表 (在群里发「本群信息」可查本群 ID)
  keywords  : 白名单关键词, 任一回答包含其中一个即自动通过
  blacklist : 黑名单关键词, 任一回答包含其中一个即自动拒绝

判定顺序: 邀请入群 → 人工; 黑白名单都命中 → 人工; 只命中黑名单 → 拒绝;
只命中白名单 → 通过; 都没命中 → 人工。「回答」= 问答验证的每条答案 + 验证消息
(平台两种验证方式只会给其一; 被邀请的申请两者都没有)。

提示一律走主动消息 —— 入群申请事件没有可供被动回复的 msg_id / event_id。
提示里只出现命中的那个白名单词 (取配置原文, 不取回答里的片段), 从不复述申请人的
回答或昵称: 两者都是申请人自己写的任意文本, 可能夹带敏感词。黑名单词多半本身
就是敏感词, 拒绝提示里同样不写出来。

审核日志追加写在 data/入群自动审核.log 单个文件里, 每条申请一行 JSON (带时间),
含申请人原始回答与昵称 —— 只落本地, 不推送到任何地方。
"""

import json
import unicodedata
from datetime import datetime

from core.base.logger import PLUGIN, get_logger
from core.message._http import MSG_TYPE_TEXT
from core.message.event import GROUP_JOIN_REQUEST
from core.plugin.decorators import handler
import core.plugin.context as _ctx_mod

log = get_logger(PLUGIN, '入群自动审核')

_CONFIG_FILE = '入群自动审核_config.yaml'
_LOG_FILE = '入群自动审核.log'
_DEFAULTS = {'groups': [], 'keywords': [], 'blacklist': []}
_COMMENTS = {
    'groups': '监听的群 ID 列表',
    'keywords': '白名单关键词, 入群回答包含任意一个即自动通过 (不区分大小写与全半角)',
    'blacklist': '黑名单关键词, 入群回答包含任意一个即自动拒绝; 与白名单同时命中时转人工',
}

# loader exec 本模块时已把当前插件 ctx 注入 ctx_mod.ctx, 加载结束会被重置为 None, 先捕获
_ctx = _ctx_mod.ctx
_ctx.ensure_config(_DEFAULTS, _CONFIG_FILE, comments=_COMMENTS)

_MSG_APPROVED = '✅ 收到新入群申请，关键词 {keyword}，已自动通过'
_MSG_REJECTED = '⛔ 收到新入群申请，回答命中黑名单，已自动拒绝'
_MSG_MANUAL = '📋 收到新入群申请，回答未命中白名单，需人工处理'
_MSG_CONFLICT = '📋 收到新入群申请，回答同时命中白名单和黑名单，需人工处理'
_MSG_INVITED = '📋 收到新的邀请入群申请，需人工处理'
_MSG_FAILED = '⚠ 新入群申请处理失败，需人工处理'

# 平台重投同一条申请时不重复审批、不重复推送
_handled = set()
_HANDLED_MAX = 1000


def _load():
    """现读配置, 返回 (监听群集合, 白名单, 黑名单)"""
    cfg = _ctx.read_config(_CONFIG_FILE)

    def words(key):
        return [str(k).strip() for k in (cfg.get(key) or []) if str(k).strip()]

    groups = {str(g).strip() for g in (cfg.get('groups') or []) if str(g).strip()}
    return groups, words('keywords'), words('blacklist')


def _fold(text):
    """匹配前归一化: 全角转半角 (NFKC) + 忽略大小写"""
    return unicodedata.normalize('NFKC', text).casefold()


def _hits(answers, keywords):
    """按配置顺序返回所有命中的关键词 (配置原文); 一个都没命中返回空列表"""
    folded = [_fold(answer) for answer in answers]
    found = []
    for keyword in keywords:
        needle = _fold(keyword)
        if needle and any(needle in answer for answer in folded):
            found.append(keyword)
    return found


def _verify_message(event):
    return str((event.verify_info or {}).get('verify_message') or '')


def _answers(event):
    """申请人写的全部回答: 问答验证的每条答案 + 验证消息"""
    answers = [str(item.get('answer') or '') for item in event.review_qa_list]
    message = _verify_message(event)
    if message:
        answers.append(message)
    return answers


def _is_invited(event):
    # 官方文档: apply_source 为 self_apply / invited, invited_by 仅在 invited 时有值
    return event.apply_source == 'invited' or bool(event.invited_by)


def _decide(invited, white, black):
    """返回 (动作, 原因, 群提示)。动作: approve / decline / manual"""
    if invited:
        return 'manual', '邀请入群', _MSG_INVITED
    if white and black:
        return 'manual', '同时命中白名单和黑名单', _MSG_CONFLICT
    if black:
        return 'decline', '命中黑名单', _MSG_REJECTED
    if white:
        return 'approve', '命中白名单', _MSG_APPROVED.format(keyword=white[0])
    return 'manual', '未命中白名单', _MSG_MANUAL


def _write_log(record):
    """追加一行到审核日志 (data/ 由框架建好)。写失败只记警告, 不影响审核本身。"""
    try:
        with open(_ctx.get_data_path(_LOG_FILE), 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as error:
        log.warning(f'写入入群审核日志失败: {error}')


async def _push(event, text):
    """主动推送到申请所在的群。失败只记日志, 绝不抛出 —— 不能连累后面的审批。"""
    try:
        ok, *_ = await event.sender.send_to_group(event.group_id, text, msg_type=MSG_TYPE_TEXT)
        return ok
    except Exception as error:
        log.warning(f'入群审核提示推送异常 (group={event.group_id}): {error}')
        return False


# 不设 block: 入群申请是旁路事件, 别的插件可能也要观察它
@handler(r'.*', name='入群自动审核',
         desc='监听群的入群申请: 回答含白名单自动通过、含黑名单自动拒绝, 其余提示人工处理',
         event_types=[GROUP_JOIN_REQUEST], group_only=True)
async def on_join_request(event, match):
    groups, white_list, black_list = _load()
    if event.group_id not in groups:
        return

    member = event.raw_user_id or event.user_id
    key = event.join_request_id or f'{event.group_id}:{member}:{event.apply_at}'
    if key in _handled:
        return
    if len(_handled) >= _HANDLED_MAX:
        _handled.clear()
    _handled.add(key)

    answers = _answers(event)
    white, black = _hits(answers, white_list), _hits(answers, black_list)
    record = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'group_id': event.group_id,
        'member_openid': member,
        'username': event.username or '',
        'join_request_id': event.join_request_id or '',
        'apply_at': event.apply_at or '',
        'apply_source': event.apply_source or '',
        'invited_by': event.invited_by or '',
        'verify_method': event.verify_method or '',
        'qa': [
            {'question': str(item.get('question') or ''), 'answer': str(item.get('answer') or '')}
            for item in event.review_qa_list
        ],
        'verify_message': _verify_message(event),
        'white_hits': white,
        'black_hits': black,
    }
    # 无论中途走到哪一步, 这条申请都要留下日志
    try:
        strategy = (event.auto_approved or {}).get('strategy_id')
        if strategy:
            # 已被群自己的审批策略自动通过, 无需审批也无需人工, 只留日志
            record.update(action='platform', reason='平台策略已自动通过', strategy_id=strategy)
            return

        action, reason, notice = _decide(_is_invited(event), white, black)
        record.update(action=action, reason=reason)
        # 先提示后审批。审批只看判定结果: 提示发失败 (无主动消息权限 / 额度用完) 也照样执行
        record['notified'] = await _push(event, notice)
        if action == 'manual':
            return

        ok, resp = await event.sender.review_group_join_request(
            event.group_id, member, action, join_request_id=event.join_request_id,
        )
        record['review_ok'] = ok
        if ok:
            log.info(f'已自动{"通过" if action == "approve" else "拒绝"}入群申请 (group={event.group_id})')
        else:
            record['review_error'] = str(resp.get('message', resp) if isinstance(resp, dict) else resp)
            log.warning(f'自动审批入群申请失败 (group={event.group_id}, 动作={action}): {resp}')
            await _push(event, _MSG_FAILED)
    finally:
        _write_log(record)
