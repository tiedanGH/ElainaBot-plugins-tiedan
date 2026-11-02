# -*- coding: utf-8 -*-
"""测试引导: 从**同步后的测试环境**加载插件, 落盘全部重定向到临时沙箱。

两条约束, 改这里之前先看明白:

1. 加载的是 ElainaBot_v2/plugins/ 下的测试环境副本, 不是这个 git 工作区。
   测试跑的必须是同步过去的那一份, 否则「测试通过」证明不了副本是好的 ——
   所以改完工作区代码要先同步再跑 (run_all.py 会先查一遍漂移)。
2. 插件的 DATA_DIR 是模块级常量, 指向插件目录下的 data/ —— 任何一次 store.init()
   或 config.all_config() 都会凭空造出那个目录。store 与 config 都用
   ``from .config import DATA_DIR`` 绑了各自的副本, 所以两个都要改指到沙箱,
   store 里由 DATA_DIR 拼出的路径常量 (import 时就算好了) 也得逐个覆盖。

宿主框架用空壳 stub 掉, 不拉起整个框架。
"""
import atexit
import glob
import importlib
import io
import os
import shutil
import sys
import tempfile
import types
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(HERE)                      # git 工作区里的插件目录
PLUGIN_NAME = os.path.basename(WORKSPACE)              # LGTBot自动部署
FRAMEWORK_ROOT = os.environ.get('LGTBOT_TEST_ENV_ROOT', r'D:\0QQbot\ElainaBot_v2')
TEST_ENV = os.path.join(FRAMEWORK_ROOT, 'plugins', PLUGIN_NAME)
PKG = f'plugins.{PLUGIN_NAME}'
FIXTURES = os.path.join(HERE, 'fixtures')

SANDBOX = tempfile.mkdtemp(prefix='lgtdeploy_test_')
atexit.register(shutil.rmtree, SANDBOX, ignore_errors=True)

_STORE_PATHS = {
    'RECORDS_FILE': 'records.jsonl', 'PERMS_FILE': 'permissions.json',
    'REVIEWS_DIR': 'reviews', 'ARCHIVES_DIR': 'archives', 'STAGING_DIR': 'staging',
    'BACKUPS_DIR': 'backups', 'DIAG_DIR': 'diagnostics', 'REPORTS_DIR': 'reports',
    'PENDING_DIR': 'pending',
}


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _Log:
    def __getattr__(self, _):
        return lambda *a, **k: None


def _stub_core():
    if 'core' in sys.modules:
        return
    _mod('core')
    _mod('core.base')
    _mod('core.base.logger', PLUGIN='plugin', get_logger=lambda *a, **k: _Log(),
         report_error=lambda *a, **k: None)
    _mod('core.plugin')
    _mod('core.plugin.decorators',
         handler=lambda *a, **k: (lambda f: f), on_load=lambda f: f, on_unload=lambda f: f)
    _mod('core.plugin.web_pages', register_page=lambda **k: None,
         unregister_page=lambda *a, **k: None, register_route=lambda *a, **k: None)
    _mod('core.application', get_app=lambda: None)


def boot(*names):
    """加载测试环境的 app 子模块, 返回按名字取的模块元组。"""
    if not os.path.isdir(TEST_ENV):
        raise SystemExit(f'测试环境不存在: {TEST_ENV}')
    _stub_core()
    if FRAMEWORK_ROOT not in sys.path:
        sys.path.insert(0, FRAMEWORK_ROOT)
    cfg = importlib.import_module(PKG + '.app.config')
    cfg.DATA_DIR = SANDBOX
    cfg._CONFIG_FILE = os.path.join(SANDBOX, 'config.yaml')
    cfg._cache = None
    store = importlib.import_module(PKG + '.app.store')
    store.DATA_DIR = SANDBOX
    for attr, rel in _STORE_PATHS.items():
        if hasattr(store, attr):
            setattr(store, attr, os.path.join(SANDBOX, rel))
    mods = []
    for n in names:
        m = importlib.import_module(PKG + '.app.' + n)
        if hasattr(m, 'DATA_DIR'):
            m.DATA_DIR = SANDBOX
        mods.append(m)
    return tuple(mods) if len(mods) != 1 else mods[0]


def fixture(name: str) -> str:
    """夹具的目录路径。``name.zip`` 解到沙箱里 (每个只解一次); ``name/`` 目录直接用。

    找不到返回空串 —— 调用方据此 SKIP, 不让一个缺失的夹具把整套测试打挂。
    """
    d = os.path.join(FIXTURES, name)
    if os.path.isdir(d):
        return d
    z = d + '.zip'
    if not os.path.isfile(z):
        return ''
    out = os.path.join(SANDBOX, 'fixtures', name)
    if not os.path.isdir(out):
        with zipfile.ZipFile(z) as zf:
            zf.extractall(out)
    return out


def pkg_from_dir(folder: str) -> dict:
    """把一个目录读成 review/archive 用的 ``{'texts': [...]}`` 结构 (只读文本文件)。"""
    texts = []
    for p in sorted(glob.glob(os.path.join(folder, '**', '*'), recursive=True)):
        if not os.path.isfile(p):
            continue
        try:
            content = io.open(p, encoding='utf-8').read()
        except (OSError, UnicodeDecodeError):
            continue
        texts.append({'path': os.path.relpath(p, folder).replace(os.sep, '/'),
                      'content': content})
    return {'texts': texts}


class Checker:
    """断言计数器。每套测试一个, 末尾 ``done()`` 打汇总并以通过与否作退出码。"""

    def __init__(self):
        self.fail = []
        self.passed = 0

    def __call__(self, name, cond, detail=''):
        if cond:
            self.passed += 1
            print('  PASS  ' + name)
        else:
            self.fail.append(name)
            print('  FAIL  ' + name + (f'   {detail}' if detail != '' else ''))

    def skip(self, name, why):
        print(f'  SKIP  {name} ({why})')

    def done(self):
        print('\n' + '=' * 46)
        if self.fail:
            print(f'FAILED: {len(self.fail)} / {len(self.fail) + self.passed}')
            for f in self.fail:
                print('  - ' + f)
        else:
            print(f'ALL PASSED ({self.passed})')
        sys.exit(1 if self.fail else 0)
