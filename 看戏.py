"""看戏: 仅全量群响应的催促入局插件 (私聊禁用)"""

import random
from datetime import datetime

from core.plugin.decorators import handler
from core.base.config import cfg


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
    """判断是否位于全量群"""
    appid = event.appid or ''
    if cfg.get_bot_setting(appid, 'non_at_message.enabled', False):
        return True
    gid = event.group_id or ''
    wl = cfg.get_bot_setting(appid, 'non_at_message.group_whitelist', []) or []
    return bool(gid and gid in wl)


@handler(r'^看戏$', name='看戏',
         desc='随机回复一句催促入局的话',
         group_only=True)
async def kanxi(event, match):
    if not _is_full_volume_group(event):
        return  # 静默拒绝非全量群
    reply = random.choice(KX_REPLIES).format(
        time=datetime.now().strftime('%H:%M:%S')
    )
    await event.reply(f"<@{event.user_id}> {reply}")
