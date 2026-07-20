"""菜单展示"""

from core.plugin.decorators import handler


_MENU_TEXT = (
    "# 🥚 菜单\n"
    "> 💡 点击下方按钮快速开始："
)


async def _send_menu(event):
    buttons = [
        [
            {'text': '签到', 'data': '/签到', 'type': 2, 'style': 1},
            {'text': '排行', 'data': '/排行', 'type': 2, 'style': 1},
        ],
        [
            {'text': '谐音梗挑战', 'data': '谐音梗挑战', 'type': 2, 'style': 1},
            {'text': '舒尔特方格', 'data': '开始训练', 'type': 2, 'style': 1},
        ],
        [
            {'text': '点歌', 'data': '点歌', 'type': 2, 'style': 4},
            {'text': '恶臭生成', 'data': '恶臭123456', 'type': 2, 'style': 4},
            {'text': '验证QQ', 'data': '验证', 'type': 2, 'style': 4},
        ],
        [
            {'text': '测试按钮', 'data': '按钮大全', 'type': 2, 'style': 0},
            {'text': '测试文本', 'data': '崩溃测试', 'type': 2, 'style': 0},
        ],
    ]
    await event.reply(_MENU_TEXT, buttons=buttons)


@handler(r'^/?菜单$', name='菜单', desc='显示主菜单')
async def menu(event, match):
    await _send_menu(event)


@handler(r'^/?菜单$', name='菜单-回调',
         desc='显示主菜单（回调）',
         priority=10,
         block=True,
         event_types=['INTERACTION_CREATE'])
async def menu_callback(event, match):
    await event.ack_interaction(code=0)
    await _send_menu(event)
