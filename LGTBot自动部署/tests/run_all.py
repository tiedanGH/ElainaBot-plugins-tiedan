# -*- coding: utf-8 -*-
"""跑全部本地测试: ``python tests/run_all.py`` (在插件目录或任意目录下都行)。

顺序:
1. 先比对工作区与测试环境的代码 —— 测试加载的是**测试环境**那一份 (见 _env.py),
   两边不一致时结果反映的是测试环境, 不是你刚改的代码。有漂移就大声提示。
2. 逐个跑 test_*.py (各自一个子进程, 互不污染 sys.modules)。
3. 跑完检查 git 工作区里有没有被凭空造出 data/ —— 那是测试沙箱没兜住的信号。

可选参数: 只跑名字里含某个词的套件, 如 ``python tests/run_all.py echo``。
"""
import glob
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True        # 子进程另设 PYTHONDONTWRITEBYTECODE; 这里管父进程自己
from _env import TEST_ENV, WORKSPACE                                    # noqa: E402

# 会被同步到测试环境的文件 (与 git 跟踪的代码文件一致; tests/ 与 data/ 不在其中)
_SYNCED = ('main.py', 'panel.html', '审核提示词.txt', 'nginx示例配置.conf', '.gitignore')


def _sha(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return None


def drift():
    rels = list(_SYNCED) + [os.path.relpath(p, WORKSPACE)
                            for p in glob.glob(os.path.join(WORKSPACE, 'app', '*.py'))]
    out = []
    for rel in sorted(set(rels)):
        a = _sha(os.path.join(WORKSPACE, rel))
        if a is None:
            continue
        b = _sha(os.path.join(TEST_ENV, rel))
        if a != b:
            out.append(rel + ('  (测试环境里没有)' if b is None else ''))
    return out


def main():
    only = [a for a in sys.argv[1:] if not a.startswith('-')]
    d = drift()
    if d:
        print('⚠ 工作区与测试环境不一致 —— 下面的结果反映的是**测试环境**那一份:')
        for rel in d:
            print('    ' + rel)
        print('  先同步再跑, 否则测的不是你刚改的代码。\n')
    else:
        print('✓ 工作区与测试环境一致\n')

    suites = sorted(glob.glob(os.path.join(HERE, 'test_*.py')))
    if only:
        suites = [s for s in suites if any(w in os.path.basename(s) for w in only)]
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    results = []
    for s in suites:
        name = os.path.basename(s)
        r = subprocess.run([sys.executable, s], cwd=HERE, env=env,
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        tail = (r.stdout.strip().splitlines() or ['(无输出)'])[-1]
        results.append((name, r.returncode, tail))
        if r.returncode != 0:
            print(f'---- {name} ----')
            print('\n'.join(ln for ln in r.stdout.splitlines() if 'FAIL' in ln or 'Error' in ln))
            if r.stderr.strip():
                print(r.stderr.strip()[-2000:])
            print()

    print('=' * 50)
    for name, code, tail in results:
        print(f'  {"✓" if code == 0 else "✗"} {name:22} {tail}')
    leaked = os.path.isdir(os.path.join(WORKSPACE, 'data'))
    if leaked:
        print('\n✗ git 工作区里出现了 data/ —— 有测试绕过了沙箱, 查 _env.boot 的覆盖是否漏了')
    bad = sum(1 for _, c, _ in results if c != 0) + (1 if leaked else 0)
    print('\n' + ('ALL SUITES PASSED' if not bad else f'{bad} 项失败')
          + ('   (注意: 有漂移, 见上方)' if d else ''))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
