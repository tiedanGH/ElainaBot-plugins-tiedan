"""内容安全：入口与出口的拦截话术分开；必填文案清空后自动恢复默认。"""
import json
import os

from _harness import (
    answer_with, check, config, finish, fresh_env, read_source, section, store,
)

fresh_env(moderation_enabled=True, cooldown_seconds=0, daily_limit=0, disclaimer='> 免责')
IN_MSG = config.DEFAULT_CONFIG['moderation_blocked_response']
OUT_MSG = config.DEFAULT_CONFIG['blocked_response']

section('两条话术按方向分')
check('入口和出口不是同一句', IN_MSG != OUT_MSG)
check('入口话术说的是用户的提问', '提问' in IN_MSG, IN_MSG)
# 出口被拦时提问本身往往正常 —— 素材来自玩家上传的游戏源码。再叫用户「换一种合规表达」
# 就是拿机器人自己的输出去怪他。
check('出口话术不叫用户改表达', '合规的表达' not in OUT_MSG, OUT_MSG)
check('出口话术点明是生成的内容出了问题', '生成的内容' in OUT_MSG, OUT_MSG)


async def flagged(current, text):
    return {'available': True, 'flagged': True}


async def unavailable(current, text):
    return {'available': False, 'flagged': False, 'error': '审核接口超时'}


async def short_answer(current, messages, system_prompt, tool_handler, session_id):
    # 不调工具、答案压在 60 字内，零检索也能过接地闸 —— 这里只关心审核那一段
    return {'text': '按番数结算。'}


section('端到端：入口被拦')
before = store.stats()
event = answer_with('一个违规提问', short_answer, moderate_input=flagged)
reply = event.text_messages()[-1]
check('用入口话术', IN_MSG in reply, reply)
check('没用出口话术', OUT_MSG not in reply, reply)
check('入口拦截计数 +1', store.stats()['blocked_input_total'] == before['blocked_input_total'] + 1)

section('端到端：出口被模型复审拦下')
before = store.stats()
event = answer_with('十七步怎么结算', short_answer, moderate_output=flagged)
reply = event.text_messages()[-1]
check('用出口话术', OUT_MSG in reply, reply)
check('不再把锅甩给用户', IN_MSG not in reply, reply)
check('被拦的回复不挂免责声明（那不是 AI 的回答）', '> 免责' not in reply, reply)
check('出口拦截计数 +1', store.stats()['blocked_output_total'] == before['blocked_output_total'] + 1)

section('端到端：出口审核不可用时也不发（永远 fail-closed）')
event = answer_with('十七步怎么结算', short_answer, moderate_output=unavailable)
reply = event.text_messages()[-1]
check('审核挂了就用出口话术，不裸奔', OUT_MSG in reply and '按番数结算' not in reply, reply)

section('端到端：出口命中违规词')
config.save({'blocked_words': ['番数']})
event = answer_with('十七步怎么结算', short_answer)
reply = event.text_messages()[-1]
check('违规词命中同样用出口话术', OUT_MSG in reply and '番数' not in reply, reply)
config.save({'blocked_words': []})

section('违规词只过滤输出')
source = read_source('main.py')
check('safe_output 只在出口调用', source.count('safety.safe_output(') == 1)

section('必填文案：清空 = 恢复默认')
for key in config._REQUIRED_TEXTS:
    saved = config.save({key: ''})
    check(f'{key} 清空后恢复', saved[key] == config.DEFAULT_CONFIG[key], saved[key][:40])
    saved = config.save({key: '  \n '})
    check(f'{key} 纯空白也恢复', saved[key] == config.DEFAULT_CONFIG[key], saved[key][:40])

config.save({'safety_review_prompt': '我自己写的审核规则', 'cooldown_reply': '慢点问'})
check('自定义内容不会被覆盖', config.load()['safety_review_prompt'] == '我自己写的审核规则'
      and config.load()['cooldown_reply'] == '慢点问')

section('必填文案：手改成空串，加载时自愈')
path = config.config_path()
with open(path, encoding='utf-8') as file:
    raw = json.load(file)
raw.update({'safety_review_prompt': '', 'daily_limit_reply': '', 'disclaimer': ''})
with open(path, 'w', encoding='utf-8') as file:
    json.dump(raw, file, ensure_ascii=False)
config._cache = None
loaded = config.load()
check('加载时补回审核提示词', loaded['safety_review_prompt'] == config.DEFAULT_SAFETY_REVIEW_PROMPT)
check('加载时补回超限话术（否则被限流的人什么都收不到）',
      loaded['daily_limit_reply'] == config.DEFAULT_CONFIG['daily_limit_reply'])

section('留空有含义的字段不能被填回')
check('免责声明可以留空', loaded['disclaimer'] == '', loaded['disclaimer'])
config.save({'disclaimer': '', 'extra_prompt': '', 'lgtbot_dir': ''})
after = config.load()
for key in ('disclaimer', 'extra_prompt', 'lgtbot_dir'):
    check(f'{key} 保存后仍为空', after[key] == '', after[key])
    check(f'{key} 不在必填名单里', key not in config._REQUIRED_TEXTS)
check('测试没碰插件真实的 data/', not os.path.abspath(path).startswith(
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))))

finish()
