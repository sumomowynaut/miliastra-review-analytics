"""
千星奇域评论抓取器 —— 公开接口封装

★ 接口契约（2026-10-19 实测确认，勿凭印象改动）：
    URL    https://bbs-api.miyoushe.com/community/ugc_community/web/api/reply/list?lang=zh-cn
    方法   POST，body 是 **JSON**（不是 form-urlencoded）
    body   {"uid":"", "region":..., "level_id":..., 
            "cursor":{"next":"", "size":20, "sort_type":"SORT_TYPE_FLOOR_DESC"}}
    必要头 X-Rpc-Language / Origin / Referer / x-rpc-client_type / x-rpc-device_id
    响应   data.reply_list[] · data.total · data.cursor{next, has_more, sort_type}

    行字段：reply_id · floor_id · content · created_at(unix 秒) · is_recommend(bool)
            client_ip(省级) · reply_stat.like_count(字符串) 
            user_info.uid · user_info.nickname
    ⚠️ uid / 昵称在 user_info 里，不在顶层。

设计原则（来自 methodology/01-data-integrity.md）：
  · 双趟排序合并（FLOOR_DESC + HOT），单趟会漏 10%
  · 每页重试 3 次，失败不 break
  · 合并时点赞数取 max，不覆盖
  · has_more 提前变 false 时多跑 2 页确认游标成环
  · floor_id 缺失时合并主键退回 reply_id，并计数上报
  · 每趟独立记录 pages / error / missing_floor，最终判定 degraded

仅供技术研究与学习使用。请遵守米游社服务条款，控制请求频率。
"""

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

# ---------------------------------------------------------------- 配置

API_URL = ("https://bbs-api.miyoushe.com/community/ugc_community/web/api/"
           "reply/list?lang=zh-cn")

# 元信息接口（热度 / 官方好评率 / 关卡名）—— 评论列表接口里没有
META_URL = ("https://bbs-api.miyoushe.com/community/ugc_community/web/api/"
            "level/full/info?lang=zh-cn")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

# 公开接口要求的 RPC 头 —— 缺一个就可能 -502
HEADERS = {
    "User-Agent": UA,
    "Content-Type": "application/json;charset=UTF-8",
    "X-Rpc-Language": "zh-cn",
    "Origin": "https://act.miyoushe.com",
    "Referer": "https://act.miyoushe.com/",
    "x-rpc-client_type": "4",
    "x-rpc-device_id": "6ae990dd-03b4-40a0-b0c5-021e747c7037",
    "x-rpc-game_env": "",
    "Accept": "application/json, text/plain, */*",
}

# 排序枚举是**字符串**，不是数字
SORT_FLOOR_DESC = "SORT_TYPE_FLOOR_DESC"   # 主体
SORT_HOT = "SORT_TYPE_HOT"                 # 补充（覆盖 DESC 漏掉的那批）
SORT_FLOOR_ASC = "SORT_TYPE_FLOOR_ASC"     # 服务端降级为 DESC，无额外收益

# 速率控制：两次请求之间的最小间隔（秒）
DEFAULT_DELAY = 0.35
MAX_RETRIES = 3
RETRY_BACKOFF = 0.8
TAIL_CONFIRM = 2      # has_more=false 后额外确认页数
PAGE_SIZE = 20

# 米游社时间戳按北京时间解释；固定偏移，避免在 UTC 服务器上把月度边界算错
CN_TZ = timezone(timedelta(hours=8))


@dataclass
class Pass:
    """单趟抓取的结果"""
    sort_type: str
    pages: int = 0
    error: Optional[str] = None
    missing_floor: int = 0      # 缺失 floor_id 的行数 —— 需上报
    truncated: bool = False     # 是否因 max_pages 用尽而提前停止
    rows: list = field(default_factory=list)

    @property
    def ok(self):
        # 达到 max_pages 而被截断，也算这一趟没有完整跑完
        return self.error is None and self.pages > 0 and not self.truncated


@dataclass
class CrawlResult:
    level_id: str
    region: str
    target_id: str = ""          # 评论所属的 target_id（默认与 level_id 相同）
    total: int = 0
    meta: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)
    passes: list = field(default_factory=list)

    @property
    def degraded(self):
        """任一趟失败 → 降级，必须排查后才能交付"""
        return not all(p.ok for p in self.passes)

    @property
    def missing_floor(self):
        """
        合并后最终数据里缺失 floor_id 的行数。

        ★ 数最终结果，不数各趟原始行 —— 尾部确认会重复拉到同一批数据，
          按原始行统计会把同一个缺失重复计数多次。
        """
        return sum(1 for x in self.rows if x.get("floor_id") is None)

    def integrity(self):
        """
        七项完整性校验，返回 (通过?, 明细)。

        顺序：条数 / reply_id 唯一 / 楼层跨度 / 无降级趟 / 每趟有产出 /
        floor_id 完整 / 好评率对齐。
        """
        checks = {}
        r = self.rows

        checks["count_matches_total"] = (len(r) == self.total)
        # ★ 主键是 reply_id；校验器不能先于被校验对象崩溃 —— 一律用 .get()
        checks["reply_id_unique"] = (
            len({x.get("reply_id") for x in r}) == len(r) if r else False)

        # ★ span 只算非空楼层 —— 缺失的楼层若参与 max/min 会直接 TypeError
        floors = [x.get("floor_id") for x in r if x.get("floor_id") is not None]
        if floors:
            span = max(floors) - min(floors)
            checks["span_reasonable"] = span < len(r) * 1.1
        else:
            checks["span_reasonable"] = False

        checks["no_degraded_pass"] = not self.degraded
        checks["all_passes_produced"] = all(p.pages > 0 for p in self.passes)
        # floor_id 缺失会削弱合并去重能力，必须显式暴露
        checks["floor_id_present"] = (self.missing_floor == 0)

        # 好评率与官方对齐（最强的一致性证据）
        if r:
            rec = sum(1 for x in r if x.get("is_recommend"))
            local = rec / len(r) * 100
            try:
                official = float(str(self.meta.get("goodRate", "")).rstrip("%"))
                checks["goodrate_aligned"] = abs(local - official) < 0.5
            except (ValueError, TypeError):
                checks["goodrate_aligned"] = None
        else:
            checks["goodrate_aligned"] = False

        return all(v is not False for v in checks.values()), checks


# ---------------------------------------------------------------- 底层请求

def _post(body, timeout=25, url=None):
    """POST JSON 到公开接口"""
    req = urllib.request.Request(
        url or API_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_page(level_id, region, sort_type, cursor_next, delay, size=PAGE_SIZE):
    """抓单页，带重试。cursor_next 是字符串游标，首页传 ''。"""
    last_err = None
    body = {
        "uid": "",
        "region": region,
        "level_id": str(level_id),
        "cursor": {"next": str(cursor_next or ""), "size": size,
                   "sort_type": sort_type},
    }
    for attempt in range(MAX_RETRIES):
        try:
            j = _post(body)
            if j.get("retcode") != 0:
                raise RuntimeError(
                    f"retcode={j.get('retcode')} msg={j.get('message')}")
            if delay:
                time.sleep(delay)
            return j.get("data") or {}
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError(f"page failed after {MAX_RETRIES} tries: {last_err}")


# ---------------------------------------------------------------- 解析

def _to_int(v, default=0):
    try:
        return int(str(v).replace(",", "").strip() or default)
    except (ValueError, TypeError):
        return default


def _norm_ts(v):
    """created_at 是 unix 秒 → 'YYYY-MM-DD HH:MM:SS'"""
    s = "" if v is None else str(v).strip()
    if not s:
        return ""
    if s.isdigit():
        ts = int(s)
        if len(s) == 13:      # 毫秒
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts, CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, OverflowError):
            return ""
    return s


def _parse_rows(data):
    """把 reply_list 标准化成统一 schema"""
    out = []
    for r in data.get("reply_list", []) or []:
        reply_stat = r.get("reply_stat") or {}
        user_info = r.get("user_info") or {}
        floor_raw = r.get("floor_id")
        out.append({
            "reply_id": str(r.get("reply_id") or ""),
            # 可能为 None；字符串楼层号统一转 int，避免 span 计算时 TypeError
            "floor_id": (None if floor_raw in (None, "")
                         else _to_int(floor_raw, None)),
            "uid": str(user_info.get("uid") or ""),  # ★ 在 user_info 里
            "user": user_info.get("nickname") or "",
            "content": r.get("content") or "",
            "date": _norm_ts(r.get("created_at")),
            "like": _to_int(reply_stat.get("like_count", 0)),
            "reply_count": _to_int(reply_stat.get("reply_count", 0)),
            "ip": r.get("client_ip") or "",           # ★ 省级
            "is_recommend": bool(r.get("is_recommend")),
        })
    return out


def _merge_key(row):
    """
    合并主键。

    ★ floor_id 缺失时必须退回 reply_id ——
      否则所有缺 floor_id 的行都会塌成同一个 key（None），
      两趟 120 条会静默合并成 1 条。
    """
    if row.get("floor_id") is not None:
        return ("f", row["floor_id"])
    return ("r", row.get("reply_id") or "")


# ---------------------------------------------------------------- 单趟抓取

def _crawl_pass(level_id, region, sort_type, delay, max_pages=200):
    p = Pass(sort_type=sort_type)
    cursor = ""
    seen_cursors = set()
    tail = 0

    try:
        for _ in range(max_pages):
            data = _fetch_page(level_id, region, sort_type, cursor, delay)
            p.pages += 1
            rows = _parse_rows(data)
            p.missing_floor += sum(1 for r in rows if r["floor_id"] is None)
            p.rows.extend(rows)

            cur = data.get("cursor") or {}
            has_more = cur.get("has_more")
            nxt = cur.get("next")

            # has_more 提前变 false：多跑几页确认游标成环
            if not has_more:
                if not nxt:
                    break          # 没有下一页可确认，别回头重抓第一页
                tail += 1
                if tail >= TAIL_CONFIRM:
                    break
                cursor = nxt
                continue

            tail = 0
            if not nxt or nxt in seen_cursors:
                break
            seen_cursors.add(nxt)
            cursor = nxt
        else:
            # for 正常走完 = 达到 max_pages 上限
            p.truncated = True
    except Exception as e:  # noqa: BLE001
        p.error = str(e)
    return p


# ---------------------------------------------------------------- 主入口

def crawl(level_id, region="cn_gf01", delay=DEFAULT_DELAY, target_id=None,
          sorts=None, max_pages=200):
    """
    抓取一个奇域的全部公开评论。

    level_id : 奇域 ID（如 50105539813）
    region   : 站点，cn_gf01=国服，os_usa=国际服
    delay    : 请求间隔秒数，默认 0.35（请勿调至 0.1 以下）
    target_id: 评论所属的 target_id，默认与 level_id 相同
    """
    if sorts is None:
        sorts = [SORT_FLOOR_DESC, SORT_HOT]

    res = CrawlResult(level_id=str(level_id), region=region,
                      target_id=target_id or str(level_id))

    # 元信息（热度 / 官方好评率 / 关卡名）—— 拿不到不影响主流程
    try:
        res.meta = fetch_meta(level_id, region)
    except Exception:
        res.meta = {}
    res.meta.setdefault("region", region)

    # 评论列表的 total
    try:
        j = _post({
            "uid": "", "region": region, "level_id": str(level_id),
            "cursor": {"next": "", "size": PAGE_SIZE,
                       "sort_type": SORT_FLOOR_DESC},
        })
        data = j.get("data") or {}
        res.total = _to_int(data.get("total", 0))
        res.meta.setdefault("total", res.total)
    except Exception:
        pass

    for st in sorts:
        p = _crawl_pass(level_id, region, st, delay, max_pages)
        res.passes.append(p)

    # 合并：主键去重，点赞数取 max
    merged = {}
    for p in res.passes:
        for row in p.rows:
            key = _merge_key(row)
            if key in merged:
                merged[key]["like"] = max(merged[key]["like"], row["like"])
            else:
                merged[key] = row
    res.rows = list(merged.values())

    return res


def fetch_meta(level_id, region="cn_gf01"):
    """
    拉关卡元信息：热度、官方好评率、关卡名。

    ⚠️ 请求体的键是 agg_req_list（不是 api_name_list）—— 写错会返回空 resp_map。
    """
    body = {
        "region": region,
        "level_id": str(level_id),
        "agg_req_list": [
            {"api_name": "level_detail"},
            {"api_name": "developer_info"},
            {"api_name": "reply_card"},
            {"api_name": "config"},
        ],
    }
    j = _post(body, url=META_URL)
    if j.get("retcode") != 0:
        raise RuntimeError(f"retcode={j.get('retcode')} msg={j.get('message')}")
    rm = (j.get("data") or {}).get("resp_map") or {}
    ld = ((rm.get("level_detail") or {}).get("data") or {})
    li = ld.get("level_detail_response", {}).get("level_info", {}) or {}
    return {
        "region": region,
        "level_id": li.get("level_id"),
        "name": li.get("level_name"),
        "description": li.get("desc"),
        "hot_score": li.get("hot_score"),
        "goodRate": li.get("good_rate"),
        "play_type": li.get("play_type"),
        "play_cate": li.get("play_cate"),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="抓取千星奇域公开评论")
    ap.add_argument("level_id", help="奇域 ID，例如 50105539813")
    ap.add_argument("--region", default="cn_gf01")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    ap.add_argument("--out", default="data/raw.json")
    args = ap.parse_args()

    r = crawl(args.level_id, args.region, args.delay)
    ok, checks = r.integrity()

    print(f"抓取 {len(r.rows)}/{r.total} 条 | degraded={r.degraded} "
          f"| missing_floor={r.missing_floor}")
    for k, v in checks.items():
        print(f"  {'[OK]' if v else '[FAIL]'} {k}")
    for p in r.passes:
        print(f"  pass sort={p.sort_type} pages={p.pages} "
              f"missing_floor={p.missing_floor} error={p.error}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({
            "level_id": r.level_id, "region": r.region, "total": r.total,
            "meta": r.meta, "rows": r.rows,
            "degraded": r.degraded, "checks": checks,
            "missing_floor": r.missing_floor,
            "passes": [asdict(p) | {"rows": []} for p in r.passes],
        }, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {args.out}")
    if not ok:
        print("[WARN] 完整性校验未通过 —— 请先排查翻页问题，不要直接交付")
