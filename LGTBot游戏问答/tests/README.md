# LGTBot游戏问答 测试

```bash
python tests/run_all.py            # 全部跑，只展开失败文件的输出
python tests/run_all.py -v         # 每个文件的输出都展开
python tests/run_all.py drawing    # 只跑文件名含 drawing 的
python tests/test_drawing.py       # 单独跑一个
```

不依赖测试环境、不依赖真实的中央 AI LLM 模块、不读写插件真实的 `data/`：
`_harness.py` 把 `core.*` 打成桩，从**仓库里这份**代码载入插件，每个文件在临时目录里起
全新的配置与数据库。Python 3.9 即可，无第三方依赖。

## ⚠️ tests/ 提交但不同步

本插件的用例**进仓库**（与 LGTBot自动部署 不同，那边的 tests/ 是 gitignore 的），
但仓库根的 `sync_to_test.sh` 对所有插件的 `tests/` 一律**跳过不同步**：

框架每 2 秒递归扫插件目录，任何不以 `_` 开头的 `.py` 增删改都会热重载整个插件
（`ElainaBot_v2/core/plugin/_watcher.py`）。测试文件进了运行目录，改一行用例就重载一次
线上插件，在跑的问答当场被取消。`test_repo_hygiene.py` 锁着这条：同步脚本的过滤被删掉、
或者插件目录里冒出 `main.py` / `app/` / `tests/` 以外的 `.py`，用例就会挂。

## 文件

| 文件 | 管什么 |
|---|---|
| `_harness.py` | 框架打桩、载入插件、`check` / `finish`、假的中央服务与消息事件 |
| `run_all.py` | 每个文件一个子进程跑（插件有模块级状态，同进程连跑会串味），汇总结果 |
| `test_tools_schema.py` | 画图开关决定交给模型几个工具；执行入口也挡 |
| `test_prompt.py` | 项目简介口径（智力竞技、维护者）、游戏数量现算、硬规则块都在 |
| `test_timeouts.py` | 画图时间计入调用超时、默认 180 秒、瞬时 / 永久故障分类 |
| `test_drawing.py` | 能力发现、AI画图 1.7 接口、尺寸解析、诊断、被打断后的复用 |
| `test_delivery.py` | Markdown 图片消息格式与兜底、一整轮问答里的张数上限与发送顺序 |
| `test_grounding.py` | 接地闸、画游戏内容前必须先读源码、泄漏工具调用的解析 |
| `test_moderation.py` | 入口 / 出口拦截话术分开、必填文案清空恢复默认 |
| `test_context.py` | 清空指令写法、只清自己、全体重置不动运营数据、面板三种口径 |
| `test_sandbox.py` | 只读沙箱的越界拒绝、默认范围不含 LGTBot 的 data/ |
| `test_repo_hygiene.py` | tests/ 不同步、插件目录无散落 `.py` |

## 写用例的约定

- **断言不变量，不断言逐字文案。** 提示词和话术随时会被润色，用例要测的是
  「出口话术不叫用户改表达」「项目简介不写死数量」这类性质。
- **每条断言都应该能挂。** 新加的用例先故意弄坏对应代码跑一次，确认真的会红 ——
  首次就全绿的用例最可疑。
- 装饰器参数（指令正则、优先级）在桩里拿不到，需要时从源码里抠出来比对，
  测的就是线上那一条，见 `test_context.py`。
- 需要真实游戏索引的地方用 `patched(games, index=...)` 给假索引，
  别让结果取决于本机有没有 checkout lgtbot 源码。
