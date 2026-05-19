"""看戏: 仅全量群响应的催促入局插件 (私聊禁用)"""

import random
from datetime import datetime

from core.plugin.decorators import handler


KX_REPLIES = (
    "还在看戏，还不赶紧加入！",
    "看什么戏，还不快in！",
    "in，为什么不in！",
    "都看了多久戏了，为什么还不in！",
    "看戏，看戏！为什么不加入！",
    "你看看这都 {time} 了，还不打算加入！",
    "别让等待成为遗憾，加入，现在就开",
    "理论不如实践，看戏不如行动",
    "看戏虽好，但亲自上场才会更有乐趣",
    "机会稍纵即逝，现在加入，不要错过享受游戏的机会！",
)


def _is_full_volume_group(event):
    """判断是否位于全量群: 用主框架 data.db 中的 full_access_groups 记录 (与 /全量列表 同源)"""
    if not event.is_group:
        return False
    gid = event.group_id or ''
    if not gid:
        return False
    try:
        from core.bot.manager import _bot_manager_ref
        if not _bot_manager_ref:
            return False
        rows = _bot_manager_ref.get_full_access_groups() or []
        for r in rows:
            rid = r.get('group_id') if isinstance(r, dict) else r
            if rid == gid:
                return True
    except Exception:
        pass
    return False


@handler(r'^看戏$', name='看戏',
         desc='随机回复一句催促入局的话',
         group_only=True)
async def kanxi(event, match):
    # 群场景: 仅全量群可触发
    if event.is_group and not _is_full_volume_group(event):
        btn = [[{'text': '全量消息授权', 'data': '全量申请', 'style': 4}]]
        await event.reply("ℹ 此功能仅全量群可用", btn)
        return

    reply = random.choice(KX_REPLIES).format(
        time=datetime.now().strftime('%H:%M:%S')
    )
    await event.reply(f"<@{event.user_id}> {reply}")
