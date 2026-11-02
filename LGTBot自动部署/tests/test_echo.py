# -*- coding: utf-8 -*-
"""echo (用户输入回显) 确定性预扫描 —— review.scan_echo_sends 的回归。

扫描器的结论分量很重: 扫成「已排除」会作为可信事实禁止模型判 echo; 给出的「可疑点
清单」实测也可能被模型逐行抄成 finding (虽然它也会自己追到清单外的链路、不全收)。
所以每改一次扫描器, 都要把这里的全部真实用例跑一遍。

真实用例 (fixtures/) 各自的来历与期望结论见 fixtures/MANIFEST.md。
fishing / fangyuanchess / pirate 的原包已删, 按当时核过的形态做最小复现。
合成用例的期望命中行用 ``// HIT`` 标在源码里 —— 不手数行号 (手数就会漏掉 ``{`` 那一行)。
"""
import _env

review = _env.boot('review')
ck = _env.Checker()


def case(name, src, reg='AnyArg("x")', header=None):
    """造包; 期望命中 = mygame.cc 里带 // HIT 的行。reg 决定有没有自由文本入口。"""
    full = f'// {reg}\n' + src
    texts = [{'path': 'mygame.cc', 'content': full}]
    if header is not None:
        texts.append({'path': 'board.h', 'content': header})
    r = review.scan_echo_sends({'texts': texts})
    got = [n for p, n in r['hits'] if p == 'mygame.cc']
    want = [i for i, ln in enumerate(full.split('\n'), 1) if '// HIT' in ln]
    ck(name, got == want, f'got={got} want={want}')


def sig(f, p):
    return ('AtomReqErrCode ' + f + '(const PlayerID pid, const bool is_public, '
            'MsgSenderBase& reply, const std::string& ' + p + ')\n')


print('\n[1] 真实包')
for name, want in (('coin_card_battle-1e92', [607, 622]),   # v1 真回显
                   ('coin_card_battle-fc1a', [607, 622]),   # v2 套了 EscapeHtml_, 仍是真回显
                   ('coin_card_battle-dbad', []),           # v3 已修
                   ('forbidden_island-d3bb', []),           # 查表函数返回下标, 误判
                   ('forbidden_island-f468', []),
                   ('killer', []), ('blokus', [])):
    d = _env.fixture(name)
    if not d:
        ck.skip(name, '夹具不在')
        continue
    got = [n for _, n in review.scan_echo_sends(_env.pkg_from_dir(d))['hits']]
    ck(f'{name} → {want or "已排除"}', got == want, got)

print('\n[1b] 真回显包: 绝不能被扫成「已排除」(那会作为可信事实告诉模型不得判 echo)')
# must = 真回显行, 必须在可疑点里; gate = 先过了校验闸门、单列进 gated 的行;
# never = 1.17.0 开发中跨函数追踪一开就冒出来的假命中 (别的函数的 int / PlayerState*
# target 形参被并进处理器的 std::string& target), 两张表里都不许有。
# 不写成全等: 以后再补别的写法、真命中变多是好事, 不该让测试挂掉
for name, must, gate, never in (
        # 333 Skill_ 错误提示带原文; 667 / 1317 两个 Claim_ 零校验 → Log(dn + "：" + content)
        ('mouse-2886', {333, 667, 1317}, set(), {997, 1002, 1062, 1068, 1076}),
        # 553 Skill_; 1605 ExtraAttack_; 1619 PrivateClaim_ 零校验直接 Broadcast;
        # 949 / 1676 两个 Claim_ 过了 ValidateClaimTemplate 才 Log —— 校验模板带 .+, 形同虚设,
        # 但扫描器看不出来, 只能单列交给模型 (结构与 mouse-fixed 那三处一模一样)
        ('mouse-cf2e', {553, 1605, 1619}, {949, 1676}, {1289, 1349, 1354})):
    d = _env.fixture(name)
    if not d:
        ck.skip(name, '夹具不在')
        continue
    r = review.scan_echo_sends(_env.pkg_from_dir(d))
    got = {n for _, n in r['hits']}
    gated = {n for _, n in r['gated']}
    ck(f'{name} 不是「已排除」', bool(got), got)
    ck(f'{name} 可疑点包含 {sorted(must)}', must <= got, sorted(got))
    if gate:
        ck(f'{name} 闸门后发送包含 {sorted(gate)}', gate <= gated, sorted(gated))
    ck(f'{name} 不含并错实例的假命中', not (never & (got | gated)),
       sorted(never & (got | gated)))
    ck(f'{name} 认出 Log 是发送口', 0 in r['sinks'].get('Log', []), r['sinks'])

print('\n[1c] 先白名单校验、再发送: 不能报 (2026-09-28 人工复核为干净)')
# mouse-fixed 的 content 经 ValidateClaimTemplate (无通配、整串匹配) 才进 Log / 私信 Send。
# 跨函数发送口识别能看到 content 流进了发送, 但只要认不出这道 bool 校验闸门, 就会把
# 940 / 1642 / 1702 三条已校验路径报成可疑点 —— 这条断言就是为它设的反例。
d = _env.fixture('mouse-fixed')
if not d:
    ck.skip('mouse-fixed', '夹具不在')
else:
    got = [n for _, n in review.scan_echo_sends(_env.pkg_from_dir(d))['hits']]
    ck('mouse-fixed → 已排除 (已校验的 content 不算回显)', got == [], got)

print('\n[1d] 闸门后的发送单列, 结论交给模型 (1.17.0)')
# mouse-fixed 与 cf2e 的闸门在扫描器眼里一模一样 (差别只在校验模板带不带 .+), 所以
# 不能把闸门当「已净化」: 那样 cf2e 那种无效校验的包就会被扫成「已被排除」。
if d:
    r = review.scan_echo_sends(_env.pkg_from_dir(d))
    ck('mouse-fixed 的三处已校验路径单列进 gated',
       {n for _, n in r['gated']} == {940, 1642, 1702}, r['gated'])
    blk = review._echo_scan_block(r)
    ck('  注入: 不写「已被排除」, 也不给可疑点清单', '已被排除' not in blk and '可疑点清单' not in blk)
    ck('  注入: 给出判校验的标准', '不评估校验够不够严' in blk and '整串匹配' in blk)

print('\n[2] 声明返回标量的函数 —— 调用结果不带文本 (1.16.1)')
case('int 查表函数 (forbidden_island 形态)',
     'int TileByName_(const std::string& name) const {\n'
     '  for (int i = 0; i < 24; ++i) if (tiles_[i].name_ == name) return i;\n'
     '  return -1;\n'
     '}\n'
     'std::string TileName_(int idx) const { return k_tile_defs[idx].name_; }\n'
     + sig('Move_', 'arg') +
     '{\n'
     '  int target = Main().TileByName_(arg);\n'
     '  Global().Boardcast() << "移动到了「" << Main().TileName_(target) << "」";\n'
     '}\n')
case('声明在头文件, 调用在 mygame.cc',
     sig('Move_', 'arg') +
     '{\n'
     '  int target = Main().TileByName_(arg);\n'
     '  Global().Boardcast() << Main().TileName_(target);\n'
     '}\n',
     header='int TileByName_(const std::string& name) const;\n'
            'std::string TileName_(int idx) const;\n')
case('std::optional<int> 返回也算标量',
     'std::optional<int> TileByDesc_(int c, const std::string& d) const { return 3; }\n'
     + sig('Sand_', 'desc') +
     '{\n'
     '  const auto t = Main().TileByDesc_(0, desc);\n'
     '  reply() << "排水: " << *t;\n'
     '}\n')
case('本文件声明的 enum 返回也算标量',
     'enum class Dir { Up, Down };\n'
     'Dir ParseDir_(const std::string& s) { return Dir::Up; }\n'
     + sig('Go_', 'arg') +
     '{\n'
     '  auto d = ParseDir_(arg);\n'
     '  reply() << DirName(d);\n'
     '}\n')
case('返回 std::string 的函数照常传播 (EscapeHtml_ 这种)',
     'std::string EscapeHtml_(const std::string& s) { return s; }\n'
     + sig('Q_', 'name') +
     '{\n'
     '  std::string safe = EscapeHtml_(name);\n'
     '  reply() << safe;  // HIT\n'
     '}\n')
case('返回指针的函数不抹 (可能是 char*)',
     'const char* Raw_(const std::string& s) { return s.c_str(); }\n'
     + sig('Q_', 'name') +
     '{\n'
     '  auto p = Raw_(name);\n'
     '  reply() << p;  // HIT\n'
     '}\n')
case('void 的自定义发送函数不抹 (否则发送语句就看不见了)',
     'void SendMsg(const std::string& s) { Global().Boardcast() << s; }\n'
     + sig('Q_', 'name') +
     '{\n'
     '  SendMsg(name);  // HIT\n'
     '}\n')
case('认不出的类型一律当可能带文本 (auto 返回)',
     'auto Wrap_(const std::string& s) { return s; }\n'
     + sig('Q_', 'name') +
     '{\n'
     '  auto w = Wrap_(name);\n'
     '  reply() << w;  // HIT\n'
     '}\n')

print('\n[3] 以污染值为键的查表不传播 (1.16.1)')
case('map.find(arg) 取回的是常量表条目',
     sig('Go_', 'arg') +
     '{\n'
     '  const auto it = k_directions.find(arg);\n'
     '  reply() << it->second;\n'
     '}\n')
case('map.at(arg) 同理',
     sig('Go_', 'arg') +
     '{\n'
     '  const auto v = k_names.at(arg);\n'
     '  reply() << v;\n'
     '}\n')

print('\n[4] range-for 循环变量要传播 (1.16.1, 之前是漏报)')
case('push_back 进容器再遍历发送 → 抓到',
     sig('Say_', 'msg') +
     '{\n'
     '  std::vector<std::string> words;\n'
     '  words.push_back(msg);\n'
     '  for (const auto& w : words) {\n'
     '    reply() << w;  // HIT\n'
     '  }\n'
     '}\n')
case('遍历后只拿去查表 (forbidden_island 的 descs/loc 形态) → 不命中',
     'std::optional<int> TileByDesc_(int c, const std::string& d) const { return 3; }\n'
     + sig('Shore_', 'first') +
     '{\n'
     '  std::vector<std::string> descs;\n'
     '  descs.push_back(first);\n'
     '  for (const auto& loc : descs) {\n'
     '    const auto target = Main().TileByDesc_(0, loc);\n'
     '    if (!target) { reply() << "[错误] 无法识别排水位置"; return 1; }\n'
     '  }\n'
     '}\n')
case('两个循环各用各的 w, 只命中被污染的那个',
     sig('Say_', 'msg') +
     '{\n'
     '  std::vector<std::string> a;\n'
     '  a.push_back(msg);\n'
     '  for (const auto& w : a) reply() << w;  // HIT\n'
     '  for (const auto& w : k_fixed) reply() << w;\n'
     '}\n')

print('\n[5] 流插入 / 成员访问 / 不携带原文 / 分实例 (1.16.0)')
case('oss << name → oss.str()', sig('Q_', 'name') + '{\n  std::ostringstream oss;\n'
     '  oss << "未找到「" << name << "」";\n  reply() << oss.str();  // HIT\n}\n')
case('跨行 << 链', sig('Q_', 'name') + '{\n  std::ostringstream oss;\n'
     '  oss << "未找到「"\n      << name\n      << "」";\n  reply() << oss.str();  // HIT\n}\n')
case('EscapeHtml_ 不影响判定 (转义不限字符集)', sig('Q_', 'name')
     + '{\n  std::ostringstream oss;\n'
     '  oss << EscapeHtml_(name);\n  reply() << oss.str();  // HIT\n}\n')
case('append', sig('Q_', 'name') + '{\n  std::string msg = "你输入了";\n'
     '  msg.append(name);\n  reply() << msg;  // HIT\n}\n')
case('reply() << x << name 不把 x 当接收方', sig('Q_', 'name') + '{\n  int x = 1;\n'
     '  reply() << x << name;  // HIT\n  Global().Boardcast() << x;\n}\n')
case('成员访问不算同名变量', sig('Q_', 'name') + '{\n  for (auto* c : cards) {\n'
     '    std::string label = c->name;\n    reply() << label << CardInfoOf(x).name;\n  }\n}\n')
case('下标取字符不传播', sig('Sel_', 'arg') + "{\n  CardId t = team[arg[0] - '1'];\n"
     '  reply() << CardInfoOf(t).name;\n}\n')
case('size / find 不传播', sig('Q_', 'name') + '{\n  int n = name.size();\n'
     '  auto k = name.find("x");\n  reply() << n << k;\n}\n')
case('substr 仍带原文 (只截断不限字符集也算)', sig('Q_', 'name')
     + '{\n  reply() << name.substr(0, 8);  // HIT\n}\n')
case('reply() << name.size() 不算', sig('Q_', 'name') + '{\n  reply() << name.size();\n}\n')
case('按声明点分实例', sig('Q_', 'name') + '{\n  std::ostringstream oss;\n  oss << name;\n'
     '  reply() << oss.str();  // HIT\n}\nvoid Other() {\n  std::ostringstream oss;\n'
     '  oss << "固定";\n  Global().Boardcast() << oss.str();\n}\n')
case('成员变量跨函数照追', sig('Say_', 'msg') + '{\n  buf_ = msg;\n}\n'
     'void g() {\n  status_ = buf_;\n  Global().Boardcast() << status_;  // HIT\n}\n')
case('非处理器函数的同名形参不算种子', sig('Q_', 'name') + '{\n  reply() << "ok";\n}\n'
     'void Helper(const std::string& name) {\n  Global().Boardcast() << name;\n}\n')

print('\n[6] 三种旧误判形态 (原包已删, 最小复现) + 基础覆盖')
case('fishing 形态: 辅助函数形参 + 昵称拼 HTML, 无自由文本入口',
     'void BoardcastBoard(const std::string& phase) {\n'
     '  Global().Boardcast() << Markdown(board.Html(phase));\n}\n'
     + sig('Status_', 'unused') + '{\n  reply() << Markdown(board.Html(cur_phase_));\n}\n',
     reg='VoidChecker("赛况")')
case('fangyuanchess 形态: std::string& err 出参全是固定文案',
     'bool TryPlace(int p, std::string& err) {\n  err = "[错误] 位置被占用";\n  return false;\n}\n'
     + sig('Place_', 'pos_str') + '{\n  Position pos;\n'
     '  if (!ParsePos(pos_str, pos)) { reply() << "[错误] 位置无效"; return 1; }\n'
     '  std::string err;\n  if (!TryPlace(0, err)) { reply() << err; return 1; }\n}\n')
case('pirate 形态: 只有 AlterChecker, 比较出的 bool 进三元',
     sig('Rotate_', 'action') + '{\n  bool cw = (action == "cw");\n'
     '  reply() << (cw ? "顺时针" : "逆时针");\n}\n', reg='AlterChecker<std::string>(k)')
case('直接回显', sig('S_', 'msg') + '{\n  Global().Boardcast() << msg;  // HIT\n}\n')
case('一级洗白 (=)', sig('S_', 'msg') + '{\n  std::string out = "说: " + msg;\n'
     '  reply() << out;  // HIT\n}\n')
case('sender 变量', sig('S_', 't') + '{\n  auto sender = Global().Boardcast();\n'
     '  sender << t;  // HIT\n}\n')
case('同行 Parse(s) 不误命中', sig('S_', 's')
     + '{\n  if (!Parse(s, v)) { reply() << "[错误] 无效"; }\n}\n')
case('无自由文本入口直接排除', sig('S_', 'msg') + '{\n  Global().Boardcast() << msg;\n}\n',
     reg='AlterChecker<std::string>(m)')

print('\n[7] 注入给模型的结论')
blk = review._echo_scan_block({'files': 1, 'names': 0, 'hits': [], 'freetext': True})
ck('扫不到 → 写「已被排除」', '已被排除' in blk)
for phrase in ('参数是 AnyArg / 自由文本', '未做字符集限制', '若被拼接则构成回显', '存在注入风险'):
    ck(f'  逐字封掉说辞: {phrase}', phrase in blk)
ck('命中时给可疑点清单, 不写已排除',
   '可疑点清单' in review._echo_scan_block(
       {'files': 1, 'names': 1, 'hits': [('a.cc', 3)], 'freetext': True})
   and '已被排除' not in review._echo_scan_block(
       {'files': 1, 'names': 1, 'hits': [('a.cc', 3)], 'freetext': True}))
ck('无自由文本入口 → 专门的结论',
   '没有找到任何自由文本入口' in review._echo_scan_block(
       {'files': 1, 'names': 0, 'hits': [], 'freetext': False}))
ck('关掉 echo_review 后标准不入提示词',
   '用户输入回显' not in review.build_system_prompt('archive', '', 'N', 1, None, False))
blk = review._echo_scan_block({'files': 1, 'names': 0, 'hits': [], 'freetext': True})
ck('已排除的结论里交代跨函数也追过, 剩下的缺口只有间接调用',
   '交给自定义函数' in blk and '函数指针' in blk)
blk = review._echo_scan_block({'files': 1, 'names': 1, 'hits': [], 'gated': [('a.cc', 9)],
                               'freetext': True})
ck('只有闸门后发送 → 不写已排除, 给判校验的标准',
   '已被排除' not in blk and 'a.cc:9' in blk and '整串匹配' in blk and '放行分支' in blk)
blk = review._echo_scan_block({'files': 1, 'names': 1, 'hits': [('a.cc', 3)],
                               'gated': [('a.cc', 9)], 'freetext': True})
ck('可疑点与闸门后发送并存 → 两样都列', '可疑点清单' in blk and 'a.cc:9' in blk)

print('\n[8] 跨函数: 交给会把参数发出去的自定义函数 (1.17.0)')
case('交给自定义发送函数 → 命中调用处, 不命中函数里那句',
     'void Log(const std::string& msg) {\n'
     '  Global().Boardcast() << msg;\n'
     '}\n'
     + sig('Claim_', 'content') +
     '{\n'
     '  std::string dn = "某人";\n'
     '  Main().Log(dn + "：" + content);  // HIT\n'
     '}\n')
case('层层嵌套: A 交给 B, B 才发送',
     'void SendTo(int pid, const std::string& text) {\n'
     '  Global().Tell(pid) << text;\n'
     '}\n'
     'void Notify(const std::string& what) {\n'
     '  SendTo(0, "通知：" + what);\n'
     '}\n'
     + sig('Say_', 'msg') +
     '{\n'
     '  Notify(msg);  // HIT\n'
     '}\n')
case('污染值落在非发送位置上 → 不命中',
     'void SendTo(int pid, const std::string& text) {\n'
     '  Global().Tell(pid) << text;\n'
     '}\n'
     + sig('Say_', 'msg') +
     '{\n'
     '  SendTo(msg.size(), "固定文案");\n'
     '  SendTo(0, "收到");\n'
     '}\n')
case('只解析不发送的辅助函数不是发送口',
     'int ParseNum(const std::string& s) {\n'
     '  return std::stoi(s);\n'
     '}\n'
     'void Check(const std::string& s) {\n'
     '  if (s.empty()) Global().Boardcast() << "空";\n'
     '}\n'
     + sig('Num_', 'arg') +
     '{\n'
     '  Check(arg);\n'
     '  reply() << "你选了 " << ParseNum(arg);\n'
     '}\n')
case('存进成员、别处再发 → 调用处命中',
     'void Remember(const std::string& s) {\n'
     '  last_words_ = s;\n'
     '}\n'
     'void Replay() {\n'
     '  Global().Boardcast() << last_words_;\n'
     '}\n'
     + sig('Say_', 'msg') +
     '{\n'
     '  Remember(msg);  // HIT\n'
     '}\n')
case('返回 bool 的发送函数不被当成「结果不带文本」抹掉',
     'bool Say(const std::string& s) {\n'
     '  if (s.size() > 50) return false;\n'
     '  Global().Boardcast() << s;\n'
     '  return true;\n'
     '}\n'
     + sig('Say_', 'msg') +
     '{\n'
     '  if (!Say(msg)) { reply() << "太长了"; return 1; }  // HIT\n'
     '}\n')

print('\n[9] 同名变量按所属函数分实例 (1.17.0)')
case('别的函数里同名的 int / 指针形参不并进处理器的实例',
     'struct PlayerState { int role_id; };\n'
     + sig('Attack_', 'target') +
     '{\n'
     '  reply() << "找不到「" << target << "」";  // HIT\n'
     '}\n'
     'bool IsProtected(PlayerState* target, int attacker) {\n'
     '  Global().Boardcast() << RoleName(target->role_id) << "受到保护";\n'
     '  return false;\n'
     '}\n'
     'void Announce(int target) {\n'
     '  Global().Boardcast() << "目标: " << RoleName(target);\n'
     '}\n')
case('成员照追, 但别的函数里声明成 int 的同名局部变量不算',
     sig('Pick_', 'name') +
     '{\n'
     '  current = name;\n'
     '}\n'
     'void Show() {\n'
     '  Global().Boardcast() << "当前: " << current;  // HIT\n'
     '}\n'
     'void Tick() {\n'
     '  int current = 3;\n'
     '  Global().Boardcast() << "倒计时 " << current;\n'
     '}\n')
case('原型的形参不做种子 (否则会落到同名成员上)',
     'class MainStage {\n'
     ' public:\n'
     '  AtomReqErrCode Say_(const PlayerID pid, const bool is_public, MsgSenderBase& reply, '
     'const std::string& msg);\n'
     '  void Show() { Global().Boardcast() << msg; }\n'
     '  std::string msg;\n'
     '};\n')


def gated(name, src):
    """闸门用例: 可疑点与 gated 各自的期望行 (用源码里的标记 // HIT / // GATED 定位)。"""
    full = '// AnyArg("x")\n' + src
    r = review.scan_echo_sends({'texts': [{'path': 'mygame.cc', 'content': full}]})
    lines = full.split('\n')
    want_h = [i for i, ln in enumerate(lines, 1) if '// HIT' in ln]
    want_g = [i for i, ln in enumerate(lines, 1) if '// GATED' in ln]
    got_h = [n for _, n in r['hits']]
    got_g = [n for _, n in r['gated']]
    ck(name, (got_h, got_g) == (want_h, want_g),
       f'hits={got_h} want={want_h}  gated={got_g} want={want_g}')


print('\n[10] 校验闸门 (1.17.0)')
VALIDATE = ('bool Validate(const std::string& s, std::string& err) {\n'
            '  if (s != "是" && s != "否") { err = "只能回答是或否"; return false; }\n'
            '  return true;\n'
            '}\n')
gated('取反闸门 + return → 之后的发送单列', VALIDATE + sig('Answer_', 'text') +
      '{\n'
      '  std::string err;\n'
      '  if (!Validate(text, err)) {\n'
      '    reply() << err;\n'
      '    return 1;\n'
      '  }\n'
      '  Global().Boardcast() << "回答：" << text;  // GATED\n'
      '}\n')
gated('闸门没有 return → 不算挡过', VALIDATE + sig('Answer_', 'text') +
      '{\n'
      '  std::string err;\n'
      '  if (!Validate(text, err)) reply() << err;\n'
      '  Global().Boardcast() << "回答：" << text;  // HIT\n'
      '}\n')
gated('不取反的闸门只挡 if 体内', VALIDATE + sig('Answer_', 'text') +
      '{\n'
      '  std::string err;\n'
      '  if (Validate(text, err)) {\n'
      '    Global().Boardcast() << "回答：" << text;  // GATED\n'
      '  }\n'
      '  reply() << "你说的是 " << text;  // HIT\n'
      '}\n')
gated('闸门挡了一个参数, 另一个没挡 → 照常可疑', VALIDATE +
      'AtomReqErrCode Say_(const PlayerID pid, const bool is_public, MsgSenderBase& reply, '
      'const std::string& a, const std::string& b)\n'
      '{\n'
      '  std::string err;\n'
      '  if (!Validate(a, err)) return 1;\n'
      '  reply() << a << b;  // HIT\n'
      '}\n')
gated('条件里还有别的 (&&) → 不认作闸门', VALIDATE + sig('Answer_', 'text') +
      '{\n'
      '  std::string err;\n'
      '  if (!Validate(text, err) && strict_) return 1;\n'
      '  Global().Boardcast() << text;  // HIT\n'
      '}\n')
gated('别的函数里的闸门挡不到这里', VALIDATE + sig('Answer_', 'text') +
      '{\n'
      '  Global().Boardcast() << text;  // HIT\n'
      '}\n' + sig('Check_', 'text') +
      '{\n'
      '  std::string err;\n'
      '  if (!Validate(text, err)) return 1;\n'
      '  reply() << "ok";\n'
      '}\n')
gated('交给发送口的调用同样认闸门',
      'void Log(const std::string& msg) { Global().Boardcast() << msg; }\n' + VALIDATE
      + sig('Answer_', 'text') +
      '{\n'
      '  std::string err;\n'
      '  if (!Validate(text, err)) { reply() << err; return 1; }\n'
      '  Main().Log("回答：" + text);  // GATED\n'
      '}\n')

ck.done()
