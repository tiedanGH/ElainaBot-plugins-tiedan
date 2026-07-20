"""每日签到插件 (/签到, /排行)

数据持久化在 plugins/tiedan/data/signin.db (SQLite).
按 (appid, user_id) 复合主键, 多 bot 部署互不干扰.

随机机制:
  · 普通签到: 100~300 积分
  · 2% 概率「金色传说」: 积分 × 10 (不展示倍率, 仅文案提示)
  · 连续签到: 昨日有签到则 +1, 否则重置为 1
"""

import sqlite3
import random
import asyncio
from datetime import datetime, timedelta

from core.plugin.decorators import handler
import core.plugin.context as _ctx_mod


# ==================== 配置 ====================

# 模块顶层捕获 ctx (此时主框架已经注入), 后续即使 ctx 被 reset 也不影响
_ctx = _ctx_mod.ctx
_DB_PATH = _ctx.get_data_path('signin.db') if _ctx else 'signin.db'

_QQ_AVATAR_URL = 'https://q.qlogo.cn/qqapp/{appid}/{openid}/100'
_AVATAR_SIZE_PX = 40

_POINTS_MIN = 100
_POINTS_MAX = 300
_LEGEND_PROBABILITY = 0.02
_LEGEND_MULTIPLIER = 10
_RANK_TOP_N = 10


# ==================== 数据库 ====================

def _conn():
    """获取 sqlite 连接 (每次新建, 用 with 自动 commit/close)"""
    c = sqlite3.connect(_DB_PATH, timeout=5.0)
    c.row_factory = sqlite3.Row
    return c


def _init_db():
    if not _ctx:
        return
    try:
        with _conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS signin (
                    appid TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    nickname TEXT DEFAULT '',
                    last_signin_date TEXT DEFAULT '',
                    last_signin_time TEXT DEFAULT '',
                    points INTEGER DEFAULT 0,
                    consecutive_days INTEGER DEFAULT 0,
                    total_signins INTEGER DEFAULT 0,
                    PRIMARY KEY (appid, user_id)
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_appid_points ON signin(appid, points DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_appid_date_time ON signin(appid, last_signin_date, last_signin_time)")
    except Exception:
        pass


_init_db()


def _today_str():
    return datetime.now().strftime('%Y-%m-%d')


def _yesterday_str():
    return (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')


def _now_iso():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _get_user(appid, user_id):
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT * FROM signin WHERE appid = ? AND user_id = ?",
                (appid, user_id),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _do_signin(appid, user_id, nickname):
    """执行签到 (同步, 在 to_thread 内调用).

    返回: (success, info_dict | error_str)
    info_dict 字段: is_legend, points_gained, total_points,
                   consecutive_days, total_signins, today_rank
    """
    today = _today_str()
    yesterday = _yesterday_str()
    now = _now_iso()

    cur = _get_user(appid, user_id)
    if cur and cur.get('last_signin_date') == today:
        return False, '今日已签到, 明日再来 ✦'

    base = random.randint(_POINTS_MIN, _POINTS_MAX)
    is_legend = random.random() < _LEGEND_PROBABILITY
    gained = base * (_LEGEND_MULTIPLIER if is_legend else 1)

    if cur and cur.get('last_signin_date') == yesterday:
        consecutive = (cur.get('consecutive_days') or 0) + 1
    else:
        consecutive = 1

    prev_signins = (cur.get('total_signins') if cur else 0) or 0
    prev_points = (cur.get('points') if cur else 0) or 0
    new_signins = prev_signins + 1
    new_points = prev_points + gained

    try:
        with _conn() as c:
            c.execute("""
                INSERT INTO signin (appid, user_id, nickname, last_signin_date, last_signin_time,
                                    points, consecutive_days, total_signins)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(appid, user_id) DO UPDATE SET
                    nickname = excluded.nickname,
                    last_signin_date = excluded.last_signin_date,
                    last_signin_time = excluded.last_signin_time,
                    points = excluded.points,
                    consecutive_days = excluded.consecutive_days,
                    total_signins = excluded.total_signins
            """, (appid, user_id, nickname, today, now,
                  new_points, consecutive, new_signins))
    except Exception as e:
        return False, f'签到失败: {type(e).__name__}'

    # 今日排名: <= 我的签到时间的人数
    try:
        with _conn() as c:
            row = c.execute("""
                SELECT COUNT(*) FROM signin
                WHERE appid = ? AND last_signin_date = ? AND last_signin_time <= ?
            """, (appid, today, now)).fetchone()
        today_rank = row[0] if row else 1
    except Exception:
        today_rank = 1

    return True, {
        'is_legend': is_legend,
        'points_gained': gained,
        'total_points': new_points,
        'consecutive_days': consecutive,
        'total_signins': new_signins,
        'today_rank': today_rank,
    }


def _get_top_points(appid, limit):
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT user_id, points FROM signin
                WHERE appid = ? ORDER BY points DESC, last_signin_time ASC LIMIT ?
            """, (appid, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_my_points_rank(appid, user_id):
    """返回 (我的总积分, 名次); 未签到过返回 (0, 0)"""
    cur = _get_user(appid, user_id)
    if not cur:
        return 0, 0
    my_points = cur.get('points') or 0
    try:
        with _conn() as c:
            row = c.execute("""
                SELECT COUNT(*) FROM signin WHERE appid = ? AND points > ?
            """, (appid, my_points)).fetchone()
        rank = (row[0] if row else 0) + 1
    except Exception:
        rank = 0
    return my_points, rank


def _get_top_today(appid, limit):
    today = _today_str()
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT user_id, last_signin_time FROM signin
                WHERE appid = ? AND last_signin_date = ?
                ORDER BY last_signin_time ASC LIMIT ?
            """, (appid, today, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_my_today_rank(appid, user_id):
    """返回 (今日签到时间, 名次); 未签到返回 ('', 0)"""
    today = _today_str()
    cur = _get_user(appid, user_id)
    if not cur or cur.get('last_signin_date') != today:
        return '', 0
    my_time = cur.get('last_signin_time') or ''
    try:
        with _conn() as c:
            row = c.execute("""
                SELECT COUNT(*) FROM signin
                WHERE appid = ? AND last_signin_date = ? AND last_signin_time < ?
            """, (appid, today, my_time)).fetchone()
        rank = (row[0] if row else 0) + 1
    except Exception:
        rank = 0
    return my_time, rank


# ==================== markdown 构造 ====================

def _avatar_md(appid, user_id):
    """40×40 头像 markdown (40px 显示尺寸, 100px 源图)"""
    url = _QQ_AVATAR_URL.format(appid=appid, openid=user_id)
    return f"![头像 #{_AVATAR_SIZE_PX}px #{_AVATAR_SIZE_PX}px]({url})"


def _hhmmss(iso_str):
    """从 'YYYY-MM-DD HH:MM:SS' 取 'HH:MM:SS'"""
    return iso_str[11:19] if len(iso_str) >= 19 else iso_str


# 「📅 今日签到」按钮 (type=2 指令插入输入框, style=1 蓝色)
_SIGNIN_BUTTONS = [[
    {'text': '📅 今日签到', 'data': '/签到', 'type': 2, 'style': 1},
]]


# ==================== 异步包装 (sqlite 同步, 用 to_thread 避免阻塞 event loop) ====================

async def _async_signin(appid, user_id, nickname):
    return await asyncio.to_thread(_do_signin, appid, user_id, nickname)


async def _async_points_data(appid, user_id):
    top = await asyncio.to_thread(_get_top_points, appid, _RANK_TOP_N)
    my_p, rank = await asyncio.to_thread(_get_my_points_rank, appid, user_id)
    return top, my_p, rank


async def _async_today_data(appid, user_id):
    top = await asyncio.to_thread(_get_top_today, appid, _RANK_TOP_N)
    my_t, rank = await asyncio.to_thread(_get_my_today_rank, appid, user_id)
    return top, my_t, rank


# ==================== 主入口: 签到 ====================

@handler(r'^/?签到$', name='签到',
         desc='每日签到 (100~300 积分, 2% 金色传说)')
async def cmd_signin(event, match):
    appid = event.appid or ''
    user_id = event.user_id or ''
    nickname = event.username or ''

    ok, info = await _async_signin(appid, user_id, nickname)

    # 第一行: 40×40 头像 | @用户
    lines = [f"{_avatar_md(appid, user_id)} | <@{user_id}>"]

    if not ok:
        # 已签到 / 失败: 仍带按钮 (用户可继续操作)
        lines.append(f"**{info}**")
        await event.reply('\n'.join(lines), _SIGNIN_BUTTONS)
        return

    # 金色传说提示 (合在加粗里, 不展示倍率)
    legend_tail = ' 哇, 金色传说!' if info['is_legend'] else ''
    lines.extend([
        f"**🎉 签到成功!{legend_tail}**",
        f"连续签到: **{info['consecutive_days']}** 天",
        f"总签到数: **{info['total_signins']}** 次",
        f"今日排名: 第 **{info['today_rank']}** 名",
        f"签到奖励: **{info['points_gained']}** 积分",
        f"总积分数: **{info['total_points']}**",
    ])
    await event.reply('\n'.join(lines), _SIGNIN_BUTTONS)


# ==================== 主入口: 排行 ====================

@handler(r'^/?排行(?:\s+(\S+))?\s*$', name='排行',
         desc='/排行 总积分; /排行 签到 今日签到排行')
async def cmd_rank(event, match):
    appid = event.appid or ''
    user_id = event.user_id or ''
    sub = (match.group(1) or '').strip()

    if sub == '签到':
        top, my_time, my_rank = await _async_today_data(appid, user_id)
        lines = ["**📅 今日签到排行 (前 10)**"]
        if not top:
            lines.append("暂无签到记录")
        else:
            for i, row in enumerate(top, 1):
                lines.append(f"**{i}.** {_avatar_md(appid, row['user_id'])} `{_hhmmss(row['last_signin_time'])}`")
        lines.append("")  # 空行分隔
        if my_rank > 0:
            lines.append(f"**我:** {_avatar_md(appid, user_id)} `{_hhmmss(my_time)}` · 第 **{my_rank}** 名")
        else:
            lines.append("**我:** 今日尚未签到")
        await event.reply('\n'.join(lines), _SIGNIN_BUTTONS)
    else:
        top, my_points, my_rank = await _async_points_data(appid, user_id)
        lines = ["**🏆 积分排行 (前 10)**"]
        if not top:
            lines.append("暂无签到记录")
        else:
            for i, row in enumerate(top, 1):
                lines.append(f"**{i}.** {_avatar_md(appid, row['user_id'])} **{row['points']}** 分")
        lines.append("")
        if my_rank > 0:
            lines.append(f"**我:** {_avatar_md(appid, user_id)} **{my_points}** 分 · 第 **{my_rank}** 名")
        else:
            lines.append("**我:** 尚未签到过")
        await event.reply('\n'.join(lines), _SIGNIN_BUTTONS)
