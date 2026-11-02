"""测试插件: QQ 按钮大全 — 一次展示所有 样式 / 功能 / 身份组

设计:
  - 所有 type=1 (回调) 按钮共用 data = _CB_DATA, 点击都触发同一回调响应
  - 所有 type=2 (指令) 按钮共用 data = _CMD_DATA, 用户发送后触发同一指令响应
  - bot 配置 button_enter_to_send=true 时, type=2+enter 会被转成 type=1,
    所以回调 handler 同时匹配 _CB_DATA 和 _CMD_DATA, 不漏
  - 小按钮: 与按钮大全同一组按钮, 整组 small (字号是键盘级属性, 不能单独设某个按钮)
  - 点我: 一个带二次确认弹窗 (action.modal) 的回调按钮, 弹窗里是提问 + 两个选项;
    只有点确认项才会发回调, 回调只 ack 不回复, 点取消项是纯客户端行为, bot 收不到

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
    modal    点击后弹窗: {'content': 提问 (≤40 字符, 不能有 URL),
                         'confirm_text': 确认项 (≤4 字符, 默认「确认」),
                         'cancel_text': 取消项 (≤4 字符, 默认「取消」)}; 只传字符串时仅设提问
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

def _gallery_keyboard(event, font_size=None):
    """按钮大全的整组按钮 (按钮大全 / 小按钮 共用, 两边完全一致); font_size 为键盘级字号"""
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
    keyboard = build_keyboard(buttons, event.appid, font_size=font_size)
    keyboard['content']['rows'][3]['buttons'][3]['action']['anchor'] = 1
    return keyboard


@handler(r'^按钮大全$', name='按钮大全', desc='一次展示所有按钮样式/功能/权限示例', block=True)
async def button_gallery(event, match):
    await event.reply(_MD.format(uid=event.user_id), keyboard=_gallery_keyboard(event))


# ==================== 小按钮 ====================
# 字号是整组键盘的属性 (官方 botgo KeyboardStyle.FontSize → keyboard.content.style.font_size),
# 不能单独设某一个按钮; 所以把按钮大全的同一组按钮整组以 small 发出。
# 示例插件也有一条 ^小按钮$ (仅主人), priority=10 + block 保证总是这条先响应。

_SMALL_MD = """# 小按钮
- `small` 参数小按钮
"""


@handler(r'^小按钮$', name='小按钮', desc='按钮大全的同一组按钮, 整组以 small 小按钮展示',
         priority=10, block=True)
async def small_buttons(event, match):
    await event.reply(_SMALL_MD, keyboard=_gallery_keyboard(event, font_size='small'))


# ==================== 回调响应 ====================
# 任意 type=1 按钮被点击 → button_data 进入 event.content → 命中此 handler
# 同时匹配 _CMD_DATA, 因为 button_enter_to_send=true 时 type=2+enter 会被转成 type=1

@handler(rf'^({_CB_DATA}|{_CMD_DATA})$', name='按钮回调响应',
         desc='任何回调按钮点击的统一响应',
         event_types=['INTERACTION_CREATE'], block=True)
async def callback_responder(event, match):
    # 必须在 5 秒内 ack, 否则客户端显示"操作超时"
    # code: 0=操作成功 / 1=操作失败 / 2=操作频繁 / 3=重复操作 / 4=没有权限 / 5=暂不支持
    await event.ack_interaction(code=0)
    await event.reply(f"<@{event.user_id}> ✓ 收到回调按钮点击")


# ==================== 指令响应 ====================
# 指令按钮把 _CMD_DATA 插入输入框, 用户发送后命中此 handler
# 兼容带/不带 / 前缀 (有的客户端会带, 有的不带)

@handler(rf'^/?{_CMD_DATA}$', name='按钮指令响应',
         desc='指令按钮发送后的统一响应', block=True)
async def command_responder(event, match):
    await event.reply(f"<@{event.user_id}> ✓ 收到指令按钮发送的消息")


# ==================== 6 个指令按钮测试 ====================

@handler(r'^按钮测试$', name='按钮测试', desc='按钮样式测试', block=True)
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
        [
            {'text': '🏠 欢迎菜单', 'type': 1, 'data': '菜单', 'style': 1},
            {'text': '📖 指令帮助', 'data': '/帮助', 'style': 1},
        ],
    ]
    await event.reply("# 按钮测试", buttons=buttons)


# ==================== 点我 → 弹窗按钮测试 ====================
# 官方 action.modal (二次确认弹窗): 弹窗内只能是一句提问 + 确认 / 取消两个选项。

_CATGIRL_OPEN = 'catgirl_open'   # 点我按钮回调 data (弹窗点「是的」后才会发出)


@handler(r'^/?点我$', name='点我', desc='发送一个按钮, 点击弹出提问和两个选项', block=True)
async def catgirl_entry(event, match):
    buttons = [[{
        'text': '点我', 'data': _CATGIRL_OPEN, 'type': 1, 'style': 1,
        'modal': {'content': '你是小猫娘吗？', 'confirm_text': '是的', 'cancel_text': '我是'},
    }]]
    await event.reply("点击下方按钮 👇", buttons=buttons)


# block: 不能漏给 LGTBot 的回调兜底, 否则 catgirl_open 会被当成指令派发给引擎
@handler(rf'^{_CATGIRL_OPEN}$', name='点我回调',
         desc='弹窗选「是的」后的回调 (仅 ack, 无任何响应)',
         event_types=['INTERACTION_CREATE'], block=True)
async def catgirl_answer(event, match):
    # 只 ack 防客户端超时提示, 不回复任何消息
    await event.ack_interaction(code=0)


# ==================== 单选按钮 ====================
# 官方 group_id: 同一分组内有一个按钮被点后, 组内其它按钮变灰不可点击 (仅回调按钮 type=1 有效)。
# 两行各用一个分组, 互不影响; 每个选项各带自己的回调 data, 选哪个都能回传。
# 变灰是客户端行为, 不代替服务端校验 (框架文档原话)。

_RADIO_LABELS = {'radio_a': '选项 A', 'radio_b': '选项 B', 'radio_single': '单独按钮'}

_RADIO_MD = """# 单选按钮
- 第一行二选一，点一个另一个变灰
- 第二行单独一个，点了即变灰
"""


@handler(r'^单选按钮$', name='单选按钮', desc='同组互斥的单选按钮演示', block=True)
async def radio_buttons(event, match):
    radio = lambda data, group, style: {
        'text': _RADIO_LABELS[data], 'data': data, 'type': 1, 'style': style, 'group_id': group,
    }
    buttons = [
        [radio('radio_a', 'radio_pair', 1), radio('radio_b', 'radio_pair', 1)],
        [radio('radio_single', 'radio_single', 0)],
    ]
    await event.reply(_RADIO_MD, buttons=buttons)


# block: 同点我回调, 不能漏给 LGTBot 的回调兜底
@handler(r'^radio_(?:a|b|single)$', name='单选按钮回调',
         desc='单选按钮被点后回传所选项', event_types=['INTERACTION_CREATE'], block=True)
async def radio_answer(event, match):
    await event.ack_interaction(code=0)
    await event.reply(f"<@{event.user_id}> ✓ 已选择：{_RADIO_LABELS[match.group(0)]}")
