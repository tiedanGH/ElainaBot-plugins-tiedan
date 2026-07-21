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
    """判断是否位于全量群: 查当前事件对应 bot 的 data.db full_access_groups 记录。

    不能用 _bot_manager_ref.get_full_access_groups() —— 它固定取 _bots 里的第一个 bot,
    多 bot 部署时会查错库。这里按 event.appid 定位到本条消息所属的 bot 再查其 db。
    """
    if not event.is_group:
        return False
    gid = event.group_id or ''
    if not gid:
        return False
    try:
        from core.bot.manager import _bot_manager_ref
        if not _bot_manager_ref:
            return False
        bot = _bot_manager_ref.get_bot(event.appid)
        log_service = getattr(bot, 'log_service', None) if bot else None
        if not log_service:
            return False
        rows = log_service.query_data(
            'SELECT 1 FROM full_access_groups WHERE group_id = ? LIMIT 1',
            (gid,),
        )
        return bool(rows)
    except Exception:
        pass
    return False


@handler(r'^看戏$', name='看戏',
         desc='[仅全量] 随机回复一句催促入局的话',
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
