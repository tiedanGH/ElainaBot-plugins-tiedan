"""测试插件: 输出 LGT-Bot 引擎崩溃推送 markdown 预览

样式与 plugins/LGTBot_ElainaBot/app/callbacks.py::_try_send_crash_notification 同源,
方便直接对照生产推送效果。
"""

from core.plugin.decorators import handler


# 模拟运行时常量 (与 callbacks.py 一致)
_LGTBOT_CRASH_DELAY_S = 30.0


@handler(r'^/?崩溃测试$', name='崩溃测试', desc='输出崩溃推送 markdown 样式预览')
async def crash_test(event, match):
    # 模拟群聊触发场景: 用户 X 在群 Y 触发了 SIGSEGV
    sig_name = 'SIGSEGV'
    uid = 'A1B2C3D4E5F6XXXXXXXXXXXX123456'
    gid = 'G1H2I3J4K5L6YYYYYYYYYYYY123456'
    msg_len = 42
    target_block = f'群聊 {gid}\n用户 {uid}'

    md = (
        '$$\\textcolor{red}{\\Huge\\text{错误推送}}$$'
        '\n'
        '## 💥 LGT-Bot 引擎崩溃\n'
        '\n'
        '> 引擎发生致命错误导致程序崩溃，所有进行中的对局丢失\n'
        '\n'
        '```崩溃信息\n'
        f'- 信号: {sig_name}\n'
        '- 触发源:\n'
        f'{target_block}\n'
        f'- 消息长度: {msg_len} 字符（详见服务端日志）\n'
        '```\n'
        '\n'
        f'进程将在 **{_LGTBOT_CRASH_DELAY_S:.0f} 秒**后自动重启···\n'
        '\n'
        '> 💡 此消息为自动推送，请尽快联系开发者排查修复'
    )
    await event.reply(md)
