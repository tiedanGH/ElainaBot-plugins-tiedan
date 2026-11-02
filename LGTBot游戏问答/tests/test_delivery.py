"""画图结果的投递，以及一整轮问答里画图的张数上限、复用与发送顺序。"""
import os

from _harness import (
    FakeDrawPlugin, FakeEvent, PLUGIN_DIR, answer_with, check, finish, fresh_env, main, run,
    section, store, use_draw,
)

fresh_env(draw_enabled=True, draw_max_per_question=1, moderation_enabled=False,
          cooldown_seconds=0, daily_limit=0, disclaimer='> 免责')
IMG = {'url': 'https://bed/a.png', 'size': '512x768'}

section('Markdown 图片消息的格式')
check('尺寸解析', main._pixel_size('512x768') == (512, 768))
check('尺寸缺失或乱写按 1024 见方',
      main._pixel_size('') == (1024, 1024) and main._pixel_size('乱写') == (1024, 1024))
event = FakeEvent()
run(main._deliver_images(event, [dict(IMG)]))
text, kw = event.texts[0]
check('先 @提问人，换行后放图（与 AI画图 同一套写法）',
      text == '<@u1>\n![画图结果 #512px #768px](https://bed/a.png)', text)
check('走 Markdown 消息、不拼签名尾巴',
      kw.get('msg_type') == 2 and kw.get('skip_suffix') is True, kw)
check('Markdown 送达就不再发富媒体', event.media == [], event.media)
event = FakeEvent(is_group=False)
run(main._deliver_images(event, [dict(IMG)]))
check('私聊没有 @', not event.texts[0][0].startswith('<@'), event.texts[0][0])

section('送不出去时的兜底')
event = FakeEvent(markdown_ok=False)
run(main._deliver_images(event, [dict(IMG)]))
check('Markdown 被拒（域名没报备等）退回富媒体', event.media == ['https://bed/a.png'], event.media)
before = store.stats()['draws_total']
event = FakeEvent(markdown_ok=False, media_ok=False)
images = [dict(IMG)]
run(main._deliver_images(event, images))
check('两条路都没送达不计入出图统计', store.stats()['draws_total'] == before)
check('送没送到都只试一次，不重发', len(event.media) == 1, event.media)
check('处理过的图会出队（重复调用不会再发）', images == [], images)

section('本插件不碰图床')
check('没有 hosting 模块的依赖', not hasattr(main, 'hosting'))
check('图床适配文件已删除', not os.path.exists(os.path.join(PLUGIN_DIR, 'app', 'hosting.py')))

section('一整轮问答：同一描述反复调用')
ROUNDS = 4


def asker(prompts):
    got = []

    async def fake_ask(current, messages, system_prompt, tool_handler, session_id):
        for i in range(ROUNDS):
            got.append(await tool_handler('draw_image', {'prompt': prompts[i % len(prompts)]}))
        return {'text': '画好了，见图。'}
    return fake_ask, got


svc = FakeDrawPlugin()
use_draw(svc)
reused_before = store.stats()['draws_reused_total']
drawn_before = store.stats()['draws_total']
fake_ask, got = asker(['橘色的猫'])
event = answer_with('画一只猫', fake_ask, index={})
check(f'{ROUNDS} 次同描述只真画 1 次', len(svc.calls) == 1, len(svc.calls))
check('模型每次都拿到图（后几次是复用）', all(item['ok'] for item in got), got)
check('只发出一条图片消息', len(event.image_messages()) == 1, event.texts)
check('文字在前、图片在后',
      len(event.texts) == 2 and event.texts[0][1].get('msg_type') != 2
      and event.texts[1][1].get('msg_type') == 2, event.texts)
answer_text = event.text_messages()[0]
check('文字带 @提问人', answer_text.startswith('<@u1>'), answer_text)
check('文字带免责声明', '> 免责' in answer_text, answer_text)
check('文字里没有图片链接', 'https://' not in answer_text, answer_text)
check('出图计 1 次', store.stats()['draws_total'] == drawn_before + 1)
check('复用计 3 次', store.stats()['draws_reused_total'] == reused_before + ROUNDS - 1,
      store.stats()['draws_reused_total'])

section('一整轮问答：换着描述就撞张数上限')
svc = FakeDrawPlugin()
use_draw(svc)
fake_ask, got = asker(['小狗A', '小狗B', '小狗C', '小狗D'])
event = answer_with('多画几张', fake_ask, index={})
check('超出上限的被拒', sum(1 for r in got if not r['ok'] and '已经画过' in r['error']) == ROUNDS - 1,
      got)
check('只真画了 1 次', len(svc.calls) == 1, len(svc.calls))
check('只发出一条图片消息', len(event.image_messages()) == 1, event.texts)

finish()
