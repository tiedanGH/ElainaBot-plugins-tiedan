# -*- coding: utf-8 -*-
"""面板「下载留档」—— store.download_path 与 webapi 的下载路由。

要守住的只有一件事: 这个接口**只收记录号**, 只发记录里登记过的审核留档 md 与上传原件,
而且文件必须落在各自的留档目录里。记录是插件自己写的, 但 records.jsonl 被改坏 / 手改
之后, 登记路径指向 config.yaml、permissions.json 也不能照发。
"""
import asyncio
import json
import os

import _env

store, webapi = _env.boot('store', 'webapi')
ck = _env.Checker()
store.init()


def add(rid, **kw):
    r = {'id': rid, 'review_file': '', 'archive_file': ''}
    r.update(kw)
    store.add_record(r)
    return r


class Req:
    def __init__(self, **query):
        self.query = query


def call(**query):
    return asyncio.run(webapi._download_record(Req(**query)))


print('\n[1] 正常下载')
rid = '20260928-120000-aaaa'
md_rel = store.save_review_text(rid, '审核结论', header='# 头')
zip_rel = store.save_archive(rid, '老鼠.zip', b'PK\x03\x04fake')
add(rid, review_file=md_rel, archive_file=zip_rel)
full, err = store.download_path(rid, 'md')
ck('md → reviews/<记录号>.md', not err and full.endswith(os.path.join('reviews', rid + '.md')), (full, err))
ck('  内容就是留档', open(full, encoding='utf-8').read().endswith('审核结论'))
full, err = store.download_path(rid, 'archive')
ck('archive → archives/<记录号>_原名', not err and os.path.basename(full) == rid + '_老鼠.zip',
   (full, err))

print('\n[2] 拿不到的情形')
rid2 = '20260928-120001-bbbb'
add(rid2, review_file=store.save_review_text(rid2, 'x'))
ck('没开留档的记录 → 说清楚没有压缩包', '没有压缩包留档' in store.download_path(rid2, 'archive')[1])
ck('记录不存在', store.download_path('20990101-000000-zzzz', 'md')[1] == '记录不存在')
ck('未知类型', store.download_path(rid, 'config')[1] == '未知的下载类型')
rid3 = '20260928-120002-cccc'
z3 = store.save_archive(rid3, 'a.zip', b'PK')
add(rid3, archive_file=z3)
os.remove(os.path.join(store.DATA_DIR, z3))
ck('留档文件已被删 → 提示去文件页看', '不存在' in store.download_path(rid3, 'archive')[1])

print('\n[3] 登记路径被改坏也不越出留档目录')
with open(os.path.join(store.DATA_DIR, 'config.yaml'), 'w', encoding='utf-8') as f:
    f.write('secret: 1\n')
with open(os.path.join(store.DATA_DIR, 'permissions.json'), 'w', encoding='utf-8') as f:
    f.write('{}')
add('20260928-120003-dddd', archive_file='config.yaml', review_file='permissions.json')
ck('archive_file 指向 config.yaml → 拒',
   store.download_path('20260928-120003-dddd', 'archive')[0] is None)
ck('review_file 指向 permissions.json → 拒',
   store.download_path('20260928-120003-dddd', 'md')[0] is None)
add('20260928-120004-eeee', archive_file='archives/../config.yaml',
    review_file='../../etc/passwd')
ck('archives/../ 绕回根目录 → 拒', store.download_path('20260928-120004-eeee', 'archive')[0] is None)
ck('../../ 越出 data/ → 拒', store.download_path('20260928-120004-eeee', 'md')[0] is None)

print('\n[4] 路由')
resp = call(id=rid, kind='archive')
cd = resp.headers.get('Content-Disposition', '')
ck('返回文件响应', type(resp).__name__ == 'FileResponse', type(resp).__name__)
ck('  中文名走 filename*', "filename*=UTF-8''" + rid + '_%E8%80%81%E9%BC%A0.zip' in cd, cd)
ck('  另有 ASCII 兜底名', f'filename="{rid}___.zip"' in cd, cd)
ck('  attachment + no-store', cd.startswith('attachment;')
   and resp.headers.get('Cache-Control') == 'no-store')
resp = call(id=rid2, kind='archive')
body = json.loads(resp.text)                     # json_response 默认把中文转成 \uXXXX
ck('拿不到 → 404 + JSON 错误', resp.status == 404 and '没有压缩包留档' in body.get('error', ''),
   (resp.status, body))

ck.done()
