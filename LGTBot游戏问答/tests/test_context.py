"""上下文：用户清自己的、面板清全部的，三种口径不能串味。"""
import json
import re

from _harness import (
    check, drawing, finish, fresh_env, read_source, run, section, store, webpanel,
)

fresh_env()

section('清空指令的写法')
# 装饰器在桩里原样返回函数，正则拿不到 —— 从源码里抠出来，测的就是线上那一条
handler = re.search(r"# 两种写法都认.*?\n((?:\s*r'[^\n]*\n)+)", read_source('main.py'), re.S)
check('能从 main.py 里找到清空指令的正则', handler is not None)
pattern = ''.join(re.findall(r"r'([^']*)'", handler.group(1))) if handler else '$^'
CLEAR = re.compile(pattern)
for text in ['/问答 清空', '/问答 clear', '/问答 重置', '/问答 清空上下文', '/lgtqa reset',
             '/清空上下文', '/重置对话', '/清除记录']:
    check(f'认得 {text}', CLEAR.match(text) is not None)
# 裸 /清空 太通用，同 bot 上别的插件可能也要用；本插件的指令是 block 的，抢了就还不回去
for text in ['/清空', '/重置', '/问答', '/清空全部', '/问答 清空 xxx', '清空上下文']:
    check(f'不误伤 {text}', CLEAR.match(text) is None)

section('用户只能清自己的')
store.append('lgtqa:a:u1', 'user', 'x', 10)
store.append('lgtqa:a:u1', 'assistant', 'y', 10)
store.append('lgtqa:a:u2', 'user', 'z', 10)
check('只删自己那份', store.clear('lgtqa:a:u1') == 2)
check('别人的还在', len(store.history('lgtqa:a:u2', 10, 3600)) == 1)

section('全体重置不动运营数据')
store.append('lgtqa:a:u3', 'user', 'w', 10)
store.record_usage('u3')
store.bump('draws', 5)
store.draw_cache_put(drawing._cache_key('lgtqa:a:u3', '一只猫'), 'https://bed/c.png', '512x512')
usage = store.stats()['questions_today']
wiped = store.clear_all()
check('清掉所有人的上下文', wiped == 2 and store.stats()['active_contexts'] == 0, wiped)
check('今日用量不受影响（不然等于把每日上限也清零了）', store.stats()['questions_today'] == usage)
check('出图统计不受影响', store.stats()['draws_total'] == 5)
check('已画图片缓存不受影响（不然下次又要重画）',
      store.draw_cache_get(drawing._cache_key('lgtqa:a:u3', '一只猫'), 3600).get('url')
      == 'https://bed/c.png')

section('面板 API 的三种口径')


class Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def clear_api(body) -> int:
    response = run(webpanel._clear_context(Request(body)))
    return json.loads(response.text)['data']['deleted']


store.append('lgtqa:a:u1', 'user', 'x', 10)
store.append('lgtqa:a:u2', 'user', 'y', 10)
check('指定 scope 只清那一个', clear_api({'scope': 'lgtqa:a:u1'}) == 1)
check('空 body 只清过期的，绝不误清全部', clear_api({}) == 0)
check('没过期的还在', store.stats()['active_contexts'] == 1)
check('显式 all=true 才清全部', clear_api({'all': True}) == 1)
check('确实清空了', store.stats()['active_contexts'] == 0)

panel = read_source('panel.html')
check('面板有「重置全部」按钮', 'id="reset-all"' in panel)
check('重置前弹窗确认', 'confirm(' in panel)
check('按钮显式传 all: true', '{all: true}' in panel)

finish()
