"""接地闸、先检索后画图、泄漏工具调用的解析与代为执行。"""
from _harness import (
    FakeDrawPlugin, answer_with, check, finish, fresh_env, games, main, patched, section,
    use_draw,
)

base = fresh_env(draw_enabled=True, moderation_enabled=False, cooldown_seconds=0, daily_limit=0)
GAMES = {'move_chess': {'name': '换位象棋', 'dir': 'move_chess', 'path': 'lgtbot/games/move_chess'}}
LONG = '按你的描述画好了。' * 40     # 超过 300 字，足以触发「只列清单」那道闸

section('画图成功时接地闸让路，但编造出处照拦')
with patched(games, index=lambda current, refresh=False: GAMES):
    check('画成了：点名游戏 + 没读源码也放行',
          main._grounding_problem(LONG, {'draw_image'}, set(), base, '画一张换位象棋的图', True) == '')
    check('没画成：同样的回答照拦',
          main._grounding_problem(LONG, {'draw_image'}, set(), base, '换位象棋怎么结算', False) != '')
    check('问题为空时从严',
          main._grounding_problem(LONG, {'draw_image'}, set(), base, '', False) != '')
check('画成了也拦编造的出处',
      '不存在的文件' in main._grounding_problem(
          '见 x/game_logic/foo.py:12', {'draw_image'}, set(), base, '画图', True))
check('drew 默认 False，老调用不受影响',
      main._grounding_problem('x' * 700, set(), set(), base, 'LGTBot 是什么') != '')

section('画游戏里的东西必须先读源码')
with patched(games, index=lambda current, refresh=False: GAMES):
    hold = main._draw_needs_source('画一张换位象棋的棋盘', {'prompt': '棋盘'}, base, set())
    check('问题点名游戏、还没读源码 → 拦下', hold != '' and 'read_game_source' in hold, hold)
    check('问题没点名、但模型的画面描述点到了 → 也拦（追问式请求）',
          main._draw_needs_source('画一张这个游戏的插画', {'prompt': '换位象棋的棋盘俯视图'},
                                  base, set()) != '')
    check('读过源码之后放行',
          main._draw_needs_source('画一张换位象棋的棋盘', {'prompt': '棋盘'},
                                  base, {'read_game_source'}) == '')
    check('只列过清单不算读过源码',
          main._draw_needs_source('画一张换位象棋的棋盘', {'prompt': '棋盘'},
                                  base, {'list_games'}) != '')
    check('跟游戏无关的照常直接画',
          main._draw_needs_source('画一只在下雪的山间小屋', {'prompt': '雪中小屋'}, base, set()) == '')

section('端到端：点名游戏时先拦、不出图')
got = []


async def draw_first(current, messages, system_prompt, tool_handler, session_id):
    got.append(await tool_handler('draw_image', {'prompt': '棋盘'}))
    return {'text': '我先去读一下规则。'}


svc = FakeDrawPlugin()
use_draw(svc)
event = answer_with('画一张换位象棋的棋盘', draw_first, index=GAMES)
check('画图调用被拒并要求先检索', got and got[0]['ok'] is False and 'read_game_source' in got[0]['error'],
      got)
check('对方一次都没被调用（没烧额度）', svc.calls == [], svc.calls)
check('没有发出图片', event.image_messages() == [], event.texts)

section('泄漏成文本的工具调用')
leaked = main._parse_leaked_tool_calls(
    '<tool_call><tool_name>draw_image</tool_name>'
    '<arg_key>prompt</arg_key><arg_value>一只狗</arg_value></tool_call>')
check('键值标签写法能解析出画图调用', leaked == [('draw_image', {'prompt': '一只狗'})], leaked)
check('属性式写法能被识别为泄漏', main._leaked_tool_xml('<draw_image prompt="x"/>'))
check('DSML 命名空间写法也能识别', main._leaked_tool_xml('<|DSML|invoke name="search_code">'))
leaked = main._parse_leaked_tool_calls(
    '<invoke name="search_code"><parameter name="query"><![CDATA[score]]></parameter>'
    '<parameter name="case_sensitive">false</parameter></invoke>')
check('invoke 写法能解析，false 还原成布尔值',
      leaked == [('search_code', {'query': 'score', 'case_sensitive': False})], leaked)
check('不认识的工具名不执行', main._parse_leaked_tool_calls('<invoke name="rm_rf">') == [])
check('正常回答不算泄漏', not main._leaked_tool_xml('十七步按番数结算，出处 mygame.cc:88'))

section('内部占位符不能当答案')
check('「[请求执行工具]」是占位符', main._placeholder_answer('[请求执行工具]'))
check('全角括号的也是', main._placeholder_answer('【请求执行工具】'))
check('正常回答不是', not main._placeholder_answer('[注意] 这个游戏有两个版本，你问的是哪个？'))

finish()
