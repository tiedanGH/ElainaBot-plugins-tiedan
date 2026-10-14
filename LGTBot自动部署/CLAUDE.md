# LGTBot自动部署 —— 项目约束

## 群消息里绝不出现用户输入 (最高优先级, 没有例外)

bot 发回 QQ 群的**每一句**消息, 都不能包含任何用户可控的文本。上传者起个违规的文件名,
bot 原样复述出去, 违规的是 bot 自己, 会被平台处罚。

**不能进群消息的** —— 只要是用户给的、或从用户内容里取出来的, 都算:

- 文件名 (`ref['filename']`、`record['filename']`), 以及由它推出的目录名 / 游戏名
  (`game`、`deploy_name`、压缩包基名)
- 指令参数: `/upload <文件夹名>`、`/compile <文件夹名>` 里的名字 (`folder`、`args`)
- 压缩包内的成员路径与文件名: 隐藏文件、.py、路径穿越等报错里的名字, 送审超限时的大文件路径
- 模型输出: `game_name`、`game_desc`、`findings` 的 `target` 与 `reason`, 以及模型给的任何文本
- 昵称 (`username`、绑定人的昵称)
- 报错原文: `str(e)`、`OSError`、下载异常 —— 里面常带文件名、路径与链接

**可以进群消息的**: 本插件写死的固定文案与标签 (违规分类名、编译状态名)、数字 (大小、
行号、文件数、退出码)、记录号、面板配置的文字 (目标 key、必需文件清单)、`<@openid>` 形式的 @。

**网页报告 (`app/report.py`) 与后台 (面板、`data/reviews/` 留档、`records.jsonl`) 不受此限**,
可以照常展示文件名、路径与模型说明 (报告页必须逐字 `html.escape`)。

LGTBot 会转发的文字同样算群消息: 计划重启的维护原因会出现在发给玩家的维护提示里,
所以固定为 `flow._RESTART_REASON`。

### 现有做法 (新增或修改消息时照着来)

- 「📦」那一行用 `flow._file_line(record)` (类型 + 大小 + 去向); 去向用 `flow._where`, 不带文件夹名
- 失败结论 `flow._fail_text(..., reason=...)`: 原因由调用方显式给出固定说法, **不读 `record['error']`** (那是后台明细)
- 压缩包错误 `archive.ArchiveError(detail, public)`: `str(e)` 进记录, `e.public` 进群; 没给 `public` 就是一句笼统的
- 部署错误 `deploy._out(..., error=群里的说法, detail=明细)`; `deploy.bad_name` 与 `check_target` 的返回会进群, 不带名字与路径
- 违规位置 `flow._finding_lines(record, public=True)`: 只点名 `flow._KNOWN_FILES` 与必需文件清单里的固定文件名, 其余写「其他文件」
- `/compile` 的提示只给格式 (`flow._COMPILE_CMD`), 不替人填目录名
- 编译 API 的报错进群前经 `flow._compile_reason` 把目录名换掉

### 改完自查

- 改到任何 `_send(...)`、`event.reply`、`send_to_group` 的文案时, 逐个确认里面每个 `{...}` 的来源
- 本地测试 `tests/test_no_echo.py` (tests/ 不进仓库, 只在本机): 用探针字符串当文件名、文件夹名、
  成员名、模型输出、昵称与报错原文, 把各条流程走一遍, 断言群消息里一个探针都没有。
  新增群消息路径时, 在那里补一个用例
