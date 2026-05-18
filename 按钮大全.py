"""测试插件: QQ 按钮大全 — 一次展示所有 样式 / 功能 / 身份组

设计:
  - 所有 type=1 (回调) 按钮共用 data = _CB_DATA, 点击都触发同一回调响应
  - 所有 type=2 (指令) 按钮共用 data = _CMD_DATA, 用户发送后触发同一指令响应
  - bot 配置 button_enter_to_send=true 时, type=2+enter 会被转成 type=1,
    所以回调 handler 同时匹配 _CB_DATA 和 _CMD_DATA, 不漏

字段说明 (参考 core/message/keyboard.py 与 QQ 官方/社区实测):
    text     按钮文字 (必填)
    style    0/1/2/3/4 共 5 种, 官方仅写 0灰框 / 1蓝框, 2/3/4 为社区实测
    link     跳转链接 (设置后 type 自动变 0)
    type     1=回调 / 2=指令(插入输入框)  (官方仅 0/1/2 三种)
    data     回调或指令数据
    enter    指令按钮: 自动发送 (单聊有效)
    reply    指令按钮: 带引用回复
    admin    仅管理员可点 → permission.type=1
    list     [openid,...]   指定用户 → permission.type=0
    role     [role_id,...]  指定身份组 → permission.type=3 (仅频道)
    tips     不支持时提示
"""

from core.plugin.decorators import handler
from core.message.keyboard import build_keyboard


_CB_DATA = 'btn_callback_demo'   # 所有回调按钮共用
_CMD_DATA = '按钮指令演示'         # 所有指令按钮共用 (会插入到输入框)


_MD = """# 按钮大全

## 🎨 样式 - 共 5 种
- 具体样式见下方按钮

## ⚙️ 功能 - 共 3 种
- `0` **跳转** - 打开 URL / 小程序
- `1` **回调** - 触发后台 callback
- `2` **指令** - 自动 @bot 并把 `data` 插入输入框
  - `enter=True` 直接发送 *(仅单聊)*
  - `reply=True` 带引用回复
  - `anchor=1` 唤起选图器 *(仅单聊)*

## 👥 身份组
- `0` 指定用户
- `1` 仅管理员
- `2` 所有人 *(默认)*
- `3` 指定身份组 *(仅频道)*

> 排版上限: **5 行 × 每行 10 个**
"""


# ==================== 主入口 ====================

@handler(r'^按钮大全$', name='按钮大全', desc='一次展示所有按钮样式/功能/权限示例')
async def button_gallery(event, match):
    cb = {'data': _CB_DATA, 'type': 1}      # 所有回调按钮共用配置
    buttons = [
        # 行 1 — 样式 0/1
        [
            {'text': 'style=0', 'style': 0, **cb},
            {'text': 'style=1', 'style': 1, **cb},
        ],
        # 行 2 — 样式 2/3/4
        [
            {'text': 'style=2', 'style': 2, **cb},
            {'text': 'style=3', 'style': 3, **cb},
            {'text': 'style=4', 'style': 4, **cb},
        ],
        # 行 3 — 跳转 + 回调
        [
            {'text': '跳转链接', 'link': 'https://bot.q.qq.com/wiki/'},
            {'text': '回调 type=1', **cb},
        ],
        # 行 4 — 指令 (type=2) 四个变体, 共用同一指令 data
        [
            {'text': '指令', 'data': _CMD_DATA, 'type': 2, 'style': 0},
            {'text': 'enter', 'data': _CMD_DATA, 'type': 2, 'style': 1, 'enter': True},
            {'text': 'reply', 'data': _CMD_DATA, 'type': 2, 'style': 1, 'reply': True},
            {'text': 'anchor', 'data': _CMD_DATA, 'type': 2, 'style': 4, 'tips': '唤起选图器，需手机 8983+ 单聊'},
        ],
        # 行 5 — 身份组 (permission)
        [
            {'text': '指定用户', 'list': [event.user_id], 'style': 0, **cb},
            {'text': '仅管理员', 'admin': True, 'style': 3, **cb},
            {'text': '指定身份组', 'role': ['4'], 'tips': '仅频道支持', 'style': 1, **cb},
        ],
    ]
    # build_keyboard 不识别 anchor 字段, 手动构建后 patch 行4第4个按钮
    keyboard = build_keyboard(buttons, event.appid)
    keyboard['content']['rows'][3]['buttons'][3]['action']['anchor'] = 1

    await event.reply(_MD.format(uid=event.user_id), keyboard=keyboard)


# ==================== 回调响应 ====================
# 任意 type=1 按钮被点击 → button_data 进入 event.content → 命中此 handler
# 同时匹配 _CMD_DATA, 因为 button_enter_to_send=true 时 type=2+enter 会被转成 type=1

@handler(rf'^({_CB_DATA}|{_CMD_DATA})$', name='按钮回调响应',
         desc='任何回调按钮点击的统一响应',
         event_types=['INTERACTION_CREATE'])
async def callback_responder(event, match):
    # 必须在 5 秒内 ack, 否则客户端显示"操作超时"
    # code: 0=操作成功 / 1=操作失败 / 2=操作频繁 / 3=重复操作 / 4=没有权限 / 5=暂不支持
    await event.ack_interaction(code=0)
    await event.reply(f"<@{event.user_id}> ✓ 收到回调按钮点击")


# ==================== 指令响应 ====================
# 指令按钮把 _CMD_DATA 插入输入框, 用户发送后命中此 handler
# 兼容带/不带 / 前缀 (有的客户端会带, 有的不带)

@handler(rf'^/?{_CMD_DATA}$', name='按钮指令响应',
         desc='指令按钮发送后的统一响应')
async def command_responder(event, match):
    await event.reply(f"<@{event.user_id}> ✓ 收到指令按钮发送的消息")


# ==================== 6 个指令按钮测试 ====================

@handler(r'^按钮测试$', name='按钮测试', desc='按钮样式测试')
async def six_buttons(event, match):
    buttons = [
        [
            {'text': '数字蜂巢', 'data': '/新游戏 数字蜂巢', 'type': 2, 'style': 0},
            {'text': '天赋云巢', 'data': '/新游戏 天赋云巢', 'type': 2, 'style': 0},
            {'text': '炼金术士', 'data': '/新游戏 炼金术士', 'type': 2, 'style': 0},
        ],
        [
            {'text': '差值投标', 'data': '/新游戏 差值投标', 'type': 2, 'style': 0},
            {'text': '决胜五子', 'data': '/新游戏 决胜五子', 'type': 2, 'style': 0},
            {'text': '彩虹奇兵', 'data': '/新游戏 彩虹奇兵', 'type': 2, 'style': 0},
        ],
        [
            {'text': '困兽棋', 'data': '/新游戏 困兽棋', 'type': 2, 'style': 0},
            {'text': '五子棋', 'data': '/新游戏 五子棋', 'type': 2, 'style': 0},
            {'text': '六贯棋', 'data': '/新游戏 六贯棋', 'type': 2, 'style': 0},
        ],
    ]
    await event.reply("# 按钮测试", buttons=buttons)
