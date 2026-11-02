"""提示词：项目简介的口径、游戏数量现算、各硬规则块都在。

断言的是**不变量**（定位、谁维护、不写死数字），不是逐字文案 —— 文案随时会被润色。
"""
from _harness import central, check, finish, fresh_env, games, patched, section

base = fresh_env()
prompt = central.build_system_prompt(base, '')

section('项目定位')
check('定位是智力竞技', '智力竞技' in prompt)
check('不再自称「文字推理」', '文字推理' not in prompt)
check('不再自称桌游', '桌游' not in prompt)
check('「LGT」出处保留', 'Liar Game Tournament' in prompt)

section('作者与维护者')
check('原作者 @Slontia', '@Slontia' in prompt)
check('写明铁蛋在维护 LGTBot 本体，不只是适配层',
      '铁蛋' in prompt and '维护' in prompt and '适配层' in prompt)

section('游戏数量按真实索引现算，不写死')
check('提示词里没有写死的「50+」', '50+' not in prompt)
fake = {f'g{i}': {'name': f'游戏{i}', 'dir': f'g{i}', 'path': f'p{i}'} for i in range(73)}
with patched(games, index=lambda current, refresh=False: fake):
    facts = central.project_facts(base)
check('数量取自索引', '73 款' in facts, facts[:120])


def broken(current, refresh=False):
    raise RuntimeError('源码目录没配好')


with patched(games, index=broken):
    facts = central.project_facts(base)
check('索引不可用时退回 70+，元问题照样能答', '70+ 款' in facts, facts[:120])
check('退回时其余事实不受影响', '@Slontia' in facts and '铁蛋' in facts)
with patched(games, index=lambda current, refresh=False: {}):
    facts = central.project_facts(base)
check('索引为空也不会说「0 款」', '0 款' not in facts.replace('70+ 款', ''), facts[:120])

section('写死在代码里的硬规则块都在')
for label, marker in [('代码布局', '【源码结构】'), ('作答前提', '【作答前提】'),
                      ('工具调用格式', '【工具调用格式】'), ('项目简介', '【项目简介】'),
                      ('安全底线', '安全规则：')]:
    check(f'{label}块在', marker in prompt)
check('不点名任何具体游戏（只写项目级事实）', '天赋' not in prompt and '云巢' not in prompt)

section('面板可编辑的系统提示词')
check('默认口径同步改成智力竞技', '智力竞技' in central.build_system_prompt(base, '').split('\n')[0])

section('画图规则')
draw = central.DRAW_RULE
check('只在明确要求时画', '明确要求' in draw)
check('画游戏里的东西要先读源码', 'read_game_source' in draw)
check('不许往回答里写链接', 'Markdown 图片语法' in draw and '链接' in draw)

finish()
