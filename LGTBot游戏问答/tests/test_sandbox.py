"""只读沙箱的边界：任何改动都不能让它松。"""
import os

from _harness import PLUGIN_DIR, check, config, finish, fresh_env, sandbox, section

base = fresh_env()
guard = dict(base, lgtbot_dir=os.path.dirname(PLUGIN_DIR))

section('越界路径一律拒绝')
for path in ('lgtbot/../data/config.yaml', 'data/config.yaml', '.git/config',
             '../../../etc/passwd', 'lgtbot/games/../../data/lgtbot.db',
             'lgtbot/games/../../../LGTBot游戏问答/data/config.json'):
    try:
        sandbox.resolve(guard, path, must_be_file=True)
        check(f'拒绝 {path}', False, '竟然放行了')
    except sandbox.SandboxError:
        check(f'拒绝 {path}', True)

section('默认可检索范围')
paths = [root['path'] for root in config.DEFAULT_ROOTS]
check('LGTBot 自己的 data/（config.yaml、lgtbot.db）不在任何范围里',
      not any(path == 'data' or path.startswith('data/') for path in paths), paths)
check('部署文档默认不检索',
      next(root for root in config.DEFAULT_ROOTS if root['id'] == 'deploy')['enabled'] is False)
check('上游 README 在范围里', any(root['path'] == 'lgtbot/README.md' for root in config.DEFAULT_ROOTS))

finish()
