"""跑全部用例：python tests/run_all.py [-v] [关键词...]

每个 test_*.py 用独立子进程跑 —— 插件里有模块级状态（配置缓存、SQLite 连接、
画图任务表），同进程连跑会互相串味。

  -v        把每个文件的完整输出都打出来（默认只打失败文件的）
  关键词     只跑文件名里含这些词的，如 `run_all.py drawing moderation`
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = re.compile(r'^RESULT (\d+) / (\d+)$', re.M)


def main() -> int:
    if not os.environ.get('PYTHONIOENCODING'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    args = sys.argv[1:]
    verbose = '-v' in args
    keywords = [arg for arg in args if arg != '-v']
    files = sorted(
        name for name in os.listdir(HERE)
        if name.startswith('test_') and name.endswith('.py')
        and (not keywords or any(word in name for word in keywords))
    )
    if not files:
        print('没有匹配的用例文件')
        return 1

    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    passed_total = all_total = 0
    broken: list = []
    started = time.monotonic()
    for name in files:
        began = time.monotonic()
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, name)],
            capture_output=True, env=env, cwd=HERE,
        )
        # Windows 下子进程输出是 \r\n，不归一的话 RESULT 行末的 \r 会让 `$` 匹配不上
        output = (proc.stdout.decode('utf-8', 'replace')
                  + proc.stderr.decode('utf-8', 'replace')).replace('\r\n', '\n')
        match = RESULT.search(output)
        passed, total = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
        passed_total += passed
        all_total += total
        ok = proc.returncode == 0 and match is not None
        mark = '✓' if ok else '✗'
        detail = f'{passed}/{total}' if match else '没跑完（看下面的报错）'
        print(f'{mark} {name:<28} {detail:>10}   {time.monotonic() - began:5.1f}s')
        if not ok:
            broken.append(name)
        if verbose or not ok:
            print('\n'.join('    ' + line for line in output.rstrip().splitlines()))

    print(f'\n共 {len(files)} 个文件，断言 {passed_total} / {all_total} 通过，'
          f'用时 {time.monotonic() - started:.1f}s')
    if broken:
        print('失败的文件: ' + '、'.join(broken))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
