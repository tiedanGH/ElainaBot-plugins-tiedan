# 测试夹具清单

全部是**真实上传过的游戏包**, 来自群内审核记录 (`data/reports/`) 与人工备份。
这些包是上传者的作品, 只在本机做回归, 不进仓库 (见插件目录的 `.gitignore`); 进仓库的只有这份清单。

每个夹具的价值不只在包本身, 更在「为什么是这个结论」—— 那是人工逐行核过的。
改扫描器 / 改审核标准之后, 下面每一条都必须仍然成立。

## echo (用户输入回显)

| 夹具 | 记录号 | 期望 | 为什么 |
|---|---|---|---|
| `coin_card_battle-1e92.zip` | 20260923-210917-1e92 | **命中 607、622** | **真回显**。`CardQuery_` 的查询词 `name` 是 `BasicChecker<std::string>` 自由文本, 被 `oss << "未找到名称包含「" << name << "」…"` 拼进消息, 再 `reply() << oss.str()` 发出。玩家能让 bot 说任意文字 |
| `coin_card_battle-fc1a.zip` | 20260923-211453-fc1a | **命中 607、622** | **仍是真回显**。上传者给 `name` 套了 `EscapeHtml_`, 但 HTML 转义只处理 `<>&"'`, 不限字符集 |
| `coin_card_battle-dbad.zip` | 20260923-212020-dbad | 已排除 | 上传者把 `name` 从两处提示文案里彻底删了。模型照旧 reject, 这版是误判 |
| `forbidden_island-d3bb.zip` | 20260925-134105-d3bb | 已排除 | 6 条 finding 全指向 `TileName_(x)`。`TileName_` 从 `tiles_[].name_` 取名, 而 `tiles_` 全文件只在初始化时拷贝一次编译期常量表 `k_tile_defs`, 从没被写入过玩家文本。玩家输入的板块名只经 `int TileByName_(…)` 查成下标 |
| `forbidden_island-f468.zip` | 20260925-142707-f468 | 已排除 | 同上, 上传者把 `TileName_` 改成直接读 `k_tile_defs`。**这一版的 6 条 finding 与 1.16.0 扫描器给的 6 处「可疑点」逐行一致** —— 模型把清单原样抄成了判决, 于是 1.16.1 给扫描器补上了返回类型识别 |
| `mouse-2886.zip` | 20260928-142646-2886 | **命中, 至少含 333 / 667 / 1317** | **真回显, 两处**。① `Claim_` 的 `content` (`AnyArg`) 零校验 → `Log(dn + "：" + content)` → `Broadcast` —— **跨函数链**, 1.16.x 扫描器看不见, 是模型自己追到的 (它报的 133 就是 `Log` 里那句 `Broadcast`); 1.17.0 补上跨函数追踪后, 两个 stage 的 `Claim_` 里那两处调用 (667 / 1317) 都会报。② `Skill_` 查不到角色时回 `"未找到角色：" << name` (333)。**不许出现 997 / 1002 / 1062 / 1068 / 1076**: 那是 1.17.0 开发中同名变量并错实例的假命中 (`IsProtected(PlayerState* target, …)`、`int target` 形参被并进 `ExtraAttack_` 的 `std::string& target`), 改成按所属函数分实例后消失 |
| `mouse-cf2e.zip` | 20260928-221404-cf2e | **可疑点至少含 553 / 1605 / 1619; 闸门后发送 (gated) 含 949 / 1676** | **真回显, 四处**。上传者给 `Claim_` 加了 `ValidateClaimTemplate`, 但 5 条模板带 `.+` (如 `对<角色>表示.+`), 任意文字照样能过 —— 1.16.x 扫描器与模型都没发现; 1.17.0 能追到这两处 `Log` (949 / 1676), 但它们前面有一道 `if (!ValidateClaimTemplate(…)) return`, **与 mouse-fixed 结构一模一样**, 扫描器分不出校验有没有用, 所以单列进 gated、把「看校验函数有没有放行任意文字的分支」交给模型。新加的 `PrivateClaim_` 零校验直接 `Broadcast` (1619, 两边都找到了); `Skill_` 的错误提示没改 (553); 新加的 `ExtraAttack_` 的 `name` 没校验 (1605)。**不许出现 1289 / 1349 / 1354** (同上, 并错实例) |
| `mouse-fixed.zip` | (人工送来复核, 2026-09-28) | **已排除** | **修好了, 上面 cf2e 的四处全改对**, 另外还修了我复核时漏看的两处 (第三章 3 条 `第.+个` 模板、≤30 字节的「纯致辞兜底」)。`ValidateClaimTemplate` 已无任何 `.+`/`.*`/兜底分支, 填充位全是固定选择集 + `[0-9]+`, `regex_match` 整串匹配; `PrivateClaim_` 过同一道校验后改走私信给配对伙伴; `Skill_` 错误提示改固定文案; `ExtraAttack_` 的 name 白名单化并按枚举重建输出。`received_messages` 回放 / 备忘录 / `ClaimList` 都只含内部文案。**这是「先白名单校验、再发送」的正确写法 —— 跨函数发送口识别 (`_find_sinks`) 如果认不出 bool 校验闸门, 会把 940 / 1642 / 1702 三条已校验路径误报出来**。1.17.0 的处理: 这三处单列进 gated, 可疑点为空; 但注入的结论**不写「已被排除」**, 而是把判校验的标准交给模型 —— 因为 cf2e 的闸门长得一模一样, 把闸门当「已净化」会让那种无效校验被扫成「已排除」 |
| `killer/` | 20260909-160413-98b8 等 | 已排除 | `pos_str` (`AnyArg`) 只进 `Position::ParsePos` 解析成坐标。被连续误判过三次 |
| `blokus/` | (人工备份) | 已排除 | 坐标类输入, 解析后不回显。早先用于「k_properties 在文件 96% 处被截断」那个游戏名读取的问题 |

已删的原包 (fishing / fangyuanchess / pirate) 在 `test_echo.py` 里有最小复现:
- fishing —— 辅助函数的 string 形参 (阶段名) + `PlayerName` 拼进 HTML, 且整包没有自由文本入口
- fangyuanchess —— `std::string& err` 出参, 十七处赋值全是固定错误文案
- pirate —— 只有 `AlterChecker<std::string>` 白名单, `bool cw = (action == "cw")` 进三元

## 其他

玩家昵称 (`Global().PlayerName(...)`) 由 LGTBot 桥接层取自平台并在那一层审核过,
**不算回显** —— 这是用户定的规则, 不是扫描器的判断。
