"""仓库卫生：tests/ 不能进运行目录，插件目录里不能冒出意料之外的 .py。

框架每 2 秒递归扫插件目录，任何不以 _ 开头的 .py 增删改都会热重载整个插件
（ElainaBot_v2/core/plugin/_watcher.py）。测试文件一旦被同步进运行目录，
改一行用例就重载一次线上插件，在跑的问答当场被取消。
"""
import os
import re

from _harness import PLUGIN_DIR, check, finish, read_source, section

REPO = os.path.dirname(PLUGIN_DIR)
NAME = os.path.basename(PLUGIN_DIR)

section('同步脚本跳过 tests/')
with open(os.path.join(REPO, 'sync_to_test.sh'), encoding='utf-8') as file:
    script = file.read()
check('同步脚本按 tests/ 过滤已跟踪文件', '"$name"/tests/*)' in script)
check('过滤发生在拷贝与校验之前（files 是过滤后的数组）',
      script.index('"$name"/tests/*)') < script.index('cp -p'))

section('插件目录里的 .py 都在预期位置')
unexpected = []
for root, dirs, files in os.walk(PLUGIN_DIR):
    dirs[:] = [d for d in dirs if d not in ('__pycache__', 'data', '.git')]
    for name in files:
        if not name.endswith('.py'):
            continue
        rel = os.path.relpath(os.path.join(root, name), PLUGIN_DIR).replace(os.sep, '/')
        if rel == 'main.py' or re.fullmatch(r'(app|tests)/[^/]+\.py', rel):
            continue
        unexpected.append(rel)
check('没有散落的脚本（同步后会触发热重载）', unexpected == [], unexpected)

section('插件代码不依赖测试')
imports = re.findall(r'^\s*(?:from|import)\s+[.\w]*\btests\b', read_source('main.py'), re.M)
check('main.py 不导入 tests', imports == [], imports)
check('.gitignore 没把 tests/ 忽略掉（本插件的用例要提交）',
      'tests' not in read_source('.gitignore'))

finish()
