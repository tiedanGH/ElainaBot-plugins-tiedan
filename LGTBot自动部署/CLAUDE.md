# LGTBot自动部署 —— 项目约束

## 群消息里绝不出现未经内容审核的用户输入 (最高优先级)

bot 发回 QQ 群的消息里, **没过内容审核的用户输入一个字都不能有**。上传者起个违规的文件名,
bot 原样复述出去, 违规的是 bot 自己, 会被平台处罚。**已经过了内容审核的信息允许复述。**

### 什么算过了审

送审正文 (`review._build_digest`) 里有: 压缩包 / 单文件的文件名、目标文件夹名、完整文件清单、
全部文本 (含 mygame.cc)。所以审核通过 (`record['verdict'] == 'pass'`, 判断统一用 `flow._reviewed`)
之后, 下面这些可以进群:

- 文件名 (`record['filename']`)、文件夹名 (`record['folder']`)、由文件名推出的目录名 (`game`)
- mygame.cc 里的游戏名 —— 用 `record['shown_name']` (`flow._reviewed_name` 取送审原文里的
  `.name_`), **不要直接用 `record['game_name']`**: 那是模型回显, 不一定是文件里原样写的字

**没过审、不能复述的场合**: 收到确认 (此时还没审)、审核前的各种拒收 (查重、隐藏文件、.py、
缺文件、超限、下载 / 解压失败)、审核不通过、审核服务异常、force 强制上传与面板关闭审核
(`verdict` 为 `force` / `skip`, 根本没审)、`/compile` 与 `/upload` 指令参数里用户新打的字。
重复上传时, 这次的文件名可能和上次不同、也没送审 —— 只能用上次那条过审记录里的名字。

### 任何时候都不能进群的

- 包内成员路径与文件名 (只在拒收时出现; 违规位置的路径是模型报的)
- 模型自由发挥的文字: `findings` 的 `reason` 与 `target`、对不上原文的游戏名、`game_desc`
- 昵称 (`username`、绑定人的昵称)
- 报错原文: `str(e)`、`OSError`、下载异常 —— 里面常带文件名、路径与链接

**一直可以进群的**: 本插件写死的固定文案与标签 (违规分类名、编译状态名)、数字 (大小、行号、
文件数、退出码)、记录号、面板配置的文字 (目标 key、必需文件清单)、`<@openid>` 形式的 @。

**网页报告 (`app/report.py`) 与后台 (面板、`data/reviews/` 留档、`records.jsonl`) 不受此限**,
可以照常展示文件名、路径与模型说明 (报告页必须逐字 `html.escape`)。

LGTBot 会转发的文字同样算群消息: 计划重启的维护原因会出现在发给玩家的维护提示里,
所以过审的写「新游戏《游戏名》」, 没审过的写固定的「新游戏上线」(`flow._restart_reason`)。

### 现有做法 (新增或修改消息时照着来)

- 名字类信息一律先看 `flow._reviewed(record)`: `_file_line` (「📦」那一行)、`_where`、`_game_label`、
  `_compile_cmd`、`_restart_reason` 都已按它分好两种说法, 直接用
- 失败结论 `flow._fail_text(..., reason=...)`: 原因由调用方显式给出固定说法, **不读 `record['error']`** (那是后台明细)
- 压缩包错误 `archive.ArchiveError(detail, public)`: `str(e)` 进记录, `e.public` 进群; 没给 `public` 就是一句笼统的
- 部署错误 `deploy._out(..., error=群里的说法, detail=明细)`; `deploy.bad_name` 与 `check_target` 的返回会进群, 不带名字与路径
- 违规位置 `flow._finding_lines(record, public=True)`: 只点名 `flow._KNOWN_FILES` 与必需文件清单里的固定文件名, 其余写「其他文件」
- 编译 API 的报错进群前经 `flow._compile_reason` 把目录名换掉

### 改完自查

- 改到任何 `_send(...)`、`event.reply`、`send_to_group` 的文案时, 逐个确认里面每个 `{...}` 的来源,
  以及发这条消息时内容审没审过
- 本地测试 `tests/test_no_echo.py` (tests/ 不进仓库, 只在本机): 用两种探针 —— 「没审过的」
  (昵称、模型自由发挥的文字、报错原文、成员路径, 以及 force / 拒收 / 审核前的文件名) 任何消息里都不许有;
  「过审的」(文件名、目录名、mygame.cc 游戏名) 只许出现在审核通过之后的消息里。
  新增群消息路径时, 在那里补一个用例
