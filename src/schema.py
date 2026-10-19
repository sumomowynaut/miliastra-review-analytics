"""
字段映射层 —— 把不同来源的原始字段统一成标准 schema

为什么需要：米游社接口返回 is_recommend / created_at / reply_stat.like_count，
而本地缓存的 detail.json 用 rec / dt / like。直接硬编码字段名会让脚本
在换数据源时静默出错（取到 None 而不报错）。

标准化 schema：
  reply_id / floor_id / uid / user / content / date / like / ip / is_recommend
"""

from datetime import datetime

# 候选字段名（按优先级）
ALIASES = {
    "reply_id":  ["reply_id", "replyId", "id"],
    "floor_id":  ["floor_id", "floorId", "floor"],
    "uid":       ["uid", "user_id", "userId"],
    "user":      ["user", "username", "nickname"],
    "content":   ["content", "text", "body"],
    "date":      ["date", "created_at", "createdAt", "dt", "time"],
    "like":      ["like", "like_count", "likeCount"],
    "ip":        ["ip", "ip_location", "ipLocation"],
    "is_recommend": ["is_recommend", "rec", "recommend", "good"],
}


def _first(row, names, default=None):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return default


def _to_int(v, default=0):
    try:
        return int(str(v).replace(",", "").strip() or default)
    except (ValueError, TypeError):
        return default


def _to_bool(v):
    """rec 可能是 1/0、"1"/"0"、True/False"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "good", "recommend")


def _norm_date(v):
    """统一成 'YYYY-MM-DD HH:MM:SS' 或原样"""
    s = str(v or "").strip()
    if not s:
        return ""
    # unix 时间戳
    if s.isdigit() and len(s) in (10, 13):
        ts = int(s)
        if len(s) == 13:
            ts //= 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    # 毫秒字符串
    if "T" in s:
        return s.replace("T", " ")[:19]
    return s


def normalize_row(row):
    """把一条原始记录映射到标准 schema"""
    out = {
        "reply_id": str(_first(row, ALIASES["reply_id"], "")),
        "floor_id": _to_int(_first(row, ALIASES["floor_id"], 0)),
        "uid": str(_first(row, ALIASES["uid"], "")),
        "user": str(_first(row, ALIASES["user"], "")),
        "content": str(_first(row, ALIASES["content"], "")),
        "date": _norm_date(_first(row, ALIASES["date"], "")),
        "like": _to_int(_first(row, ALIASES["like"], 0)),
        "ip": str(_first(row, ALIASES["ip"], "")),
        "is_recommend": _to_bool(_first(row, ALIASES["is_recommend"], False)),
    }
    return out


def normalize_all(rows):
    return [normalize_row(r) for r in rows]


def validate_schema(details):
    """校验标准化后的数据是否可用"""
    issues = []
    if not details:
        return ["数据为空"]
    if any(not d["date"] for d in details):
        issues.append(f"{sum(1 for d in details if not d['date'])} 条缺日期")
    if any(not d["content"] for d in details):
        issues.append(f"{sum(1 for d in details if not d['content'])} 条缺正文")
    if len({d["reply_id"] for d in details}) != len(details):
        issues.append("reply_id 不唯一")
    if any(d["floor_id"] == 0 for d in details):
        issues.append(f"{sum(1 for d in details if d['floor_id']==0)} 条缺 floor_id")
    return issues
