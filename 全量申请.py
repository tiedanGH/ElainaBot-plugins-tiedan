"""全量申请: 生成群全量消息授权链接

配置文件: data/全量申请_config.json
{
    "botUin": "机器人QQ号",
    "botUid": "机器人UID"
}

指令:
  全量申请 <群号>     — 生成授权链接
  全量列表           — 列出全量群 (owner)
  设置机器人QQ <号码> — 设置 botUin (owner)
  设置机器人UID <uid> — 设置 botUid (owner)
"""

import os
import json

from core.plugin.decorators import handler

_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_DIR, 'data')
_CFG_PATH = os.path.join(_DATA_DIR, '全量申请_config.json')

_URL_TPL = (
    'https://club.vip.qq.com/transfer?open_kuikly_info='
    '%7B%22page_name%22%3A%20%22ai_group_service_agreement_pop_page%22'
    '%2C%22groupCode%22%3A{group_code}'
    '%2C%22botUin%22%3A{bot_uin}'
    '%2C%22botUid%22%3A%22{bot_uid}%22'
    '%2C%22screen%22%3A1%7D'
)

_IMG = '![菜单头图 #1200px #1000px](https://qqbot.ugcimg.cn/102813815/9fd08ad10f048984fc0a9d36f71dd450e0780587/c7f24f5aeadfb1908561622d43de3169)'


_CFG_DEFAULT = {"botUin": "", "botUid": ""}

# 插件加载时立即创建 data 目录和默认配置文件
os.makedirs(_DATA_DIR, exist_ok=True)
if not os.path.isfile(_CFG_PATH):
    with open(_CFG_PATH, 'w', encoding='utf-8') as f:
        json.dump(_CFG_DEFAULT, f, ensure_ascii=False, indent=4)


def _load_config():
    if not os.path.isfile(_CFG_PATH):
        return None
    with open(_CFG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_config(cfg):
    with open(_CFG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


@handler(r'^全量申请(?:\s+(\d{6,10}))?$', name='全量申请', desc='生成群全量消息授权链接')
async def apply_full_access(event, match):
    if event.event_type == 'GROUP_MESSAGE_CREATE':
        return await event.reply(f"<@{event.user_id}> 当前群已开启全量消息，无需再次申请")
    group_code = match.group(1)
    if not group_code:
        md = (
            f"请输入本群群号\n"
            "```指令详情\n"
            "正确格式：全量申请 <本群群号>\n"
            "```"
        )
        btn = [[{'text': '重新申请', 'data': '全量申请', 'style': 4}]]
        return await event.reply(md, btn)
    cfg = _load_config()
    if not cfg or not cfg.get('botUin') or not cfg.get('botUid'):
        return await event.reply(f"<@{event.user_id}> 请先配置 全量申请_config.json（需填写 botUin 和 botUid）")
    url = _URL_TPL.format(group_code=group_code, bot_uin=cfg['botUin'], bot_uid=cfg['botUid'])
    msg = (
        "## 🔔 全量消息授权\n"
        "群主授权后，机器人可以推送主动消息，*无需再点击刷新按钮*\n"
        f"{_IMG}\n"
        "**请群主点击下方按钮授权**\n"
        "> **需要更新QQ到最新版(9.2.90及以上)**"
    )
    btn = [[{'text': '群主大大请点击这里同意申请', 'link': url, 'style': 1, 'admin': True}]]
    await event.reply(msg, btn)


@handler(r'^全量列表$', name='全量列表', desc='列出所有已开启全量消息的群', owner_only=True)
async def list_full_access(event, match):
    from core.bot.manager import _bot_manager_ref
    if not _bot_manager_ref:
        return await event.reply(f"<@{event.user_id}> 服务未就绪")
    groups = _bot_manager_ref.get_full_access_groups()
    if not groups:
        return await event.reply(f"<@{event.user_id}> 暂无全量群记录")
    gids = "\n".join(r['group_id'] for r in groups)
    md = (
        f"全量群列表（共 {len(groups)} 个）：\n"
        "```群列表\n"
        f"{gids}\n"
        "```"
    )
    await event.reply(md)


@handler(r'^设置机器人QQ\s*(\d{5,15})$', name='设置机器人QQ', desc='设置全量申请的 botUin', owner_only=True)
async def set_bot_uin(event, match):
    cfg = _load_config() or _CFG_DEFAULT.copy()
    cfg['botUin'] = match.group(1)
    _save_config(cfg)
    await event.reply(f"<@{event.user_id}> 已设置机器人QQ: {cfg['botUin']}")


@handler(r'^设置机器人UID\s*(\S+)$', name='设置机器人UID', desc='设置全量申请的 botUid', owner_only=True)
async def set_bot_uid(event, match):
    cfg = _load_config() or _CFG_DEFAULT.copy()
    cfg['botUid'] = match.group(1)
    _save_config(cfg)
    await event.reply(f"<@{event.user_id}> 已设置机器人UID: {cfg['botUid']}")
