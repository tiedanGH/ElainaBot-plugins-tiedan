"""工具清单：画图开关决定 schema，执行入口也要挡。"""
from _harness import (
    central, check, drawing, finish, fresh_env, main, run, section, tools,
)

base = fresh_env()
off = dict(base, draw_enabled=False)
on = dict(base, draw_enabled=True)

section('画图开关决定交给模型几个工具')
check('默认关闭画图', base['draw_enabled'] is False)
check('关着时 6 个只读工具', len(tools.schema_for(off)) == 6, len(tools.schema_for(off)))
check('开着时多出画图，共 7 个', len(tools.schema_for(on)) == 7, len(tools.schema_for(on)))
check('工具名不重复', len({item['function']['name'] for item in tools.schema_for(on)}) == 7)
check('draw_image 始终在 TOOL_NAMES（关着也要认得出泄漏的调用）',
      'draw_image' in tools.TOOL_NAMES)
check('只读工具清单里没有画图', 'draw_image' not in tools.READONLY_NAMES)
check('画图不算「读过代码」', 'draw_image' not in main._CONTENT_TOOLS)

section('schema 只暴露 prompt')
params = drawing.TOOL_SCHEMA['function']['parameters']['properties']
check('模型只能填 prompt（caller / max_images 由插件自己定）', list(params) == ['prompt'], list(params))
check('schema 禁止额外参数',
      drawing.TOOL_SCHEMA['function']['parameters'].get('additionalProperties') is False)

section('输入预算按开关分别计')
check('开画图时 schema 更长，预算会相应收紧',
      main._tools_schema_chars(on) > main._tools_schema_chars(off),
      (main._tools_schema_chars(off), main._tools_schema_chars(on)))

section('执行入口也挡，不只挡 schema')
result = run(tools.run('draw_image', {'prompt': 'x'}, off))
check('关着时代为执行的画图调用被拒', result['ok'] is False and '未开启' in result['error'], result)
result = run(tools.run('no_such_tool', {}, on))
check('未知工具被拒', result['ok'] is False, result)

section('提示词跟着开关走')
check('关着时提示词里没有画图规则', '【画图工具】' not in central.build_system_prompt(off, ''))
check('开着时才有', '【画图工具】' in central.build_system_prompt(on, ''))

finish()
