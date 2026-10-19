"""
千星奇域评论抓取器 — 公开接口封装

设计原则（来自 methodology/01-data-integrity.md）：
  · 双趟排序合并（FLOOR_DESC + HOT），单趟会漏 10%
  · 每页重试 3 次，失败不 break
  · 合并时点赞数取 max，不覆盖
  · has_more 提前变 false 时多跑 2 页确认游标成环
  · 每趟独立记录 pages / error，最终判定 degraded

仅供技术研究与学习使用。请遵守米游社服务条款，控制请求频率。
"""

import json
import time
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field, asdict
from typing import Optional

# ---------------------------------------------------------------- 配置

API_HOST = "https://bbs-api.miyoushe.com"
API_PATH = "/app/ugc/ugc_ext/app/v1/comment/list"

# 浏览器 UA —— Octavia 侧用默认 python UA 会被 Cloudflare 403
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SORT_FLOOR_DESC = 2   # 实测唯一有效的完整排序
SORT_HOT = 4          # 实测可用的补充排序（仅覆盖 1-15 条）
SORT_FLOOR_ASC = 1   # 服务端降级为 DESC，无额外收益

# 速率控制：两次请求之间的最小间隔（秒）
DEFAULT_DELAY = 0.35
MAX_RETRIES = 3
RETRY_BACKOFF = 0.8
TAIL_CONFIRM = 2      # has_more=false 后额外确认页数


@dataclass
class Pass:
    """单趟抓取的结果"""
    sort_type: int
    pages: int = 0
    error: Optional[str] = None
    rows: list = field(default_factory=list)

    @property
    def ok(self):
        return self.error is None and self.pages > 0


@dataclass
class CrawlResult:
    level_id: str
    region: str
    total: int = 0
    meta: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)
    passes: list = field(default_factory=list)

    @property
    def degraded(self):
        """任一趟失败 → 降级，必须排查后才能交付"""
        return not all(p.ok for p in self.passes)

    def integrity(self):
        """六项完整性校验，返回 (通过?, 明细)"""
        checks = {}
        r = self.rows

        checks["count_matches_total"] = (len(r) == self.total)
        checks["floor_id_unique"] = (
            len({x["floor_id"] for x in r}) == len(r) if r else False)
        if r:
            floors = [x["floor_id"] for x in r]
            span = max(floors) - min(floors)
            checks["span_reasonable"] = span < len(r) * 1.1
        else:
            checks["span_reasonable"] = False
        checks["no_degraded_pass"] = not self.degraded
        checks["all_passes_produced"] = all(p.pages > 0 for p in self.passes)

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

def _post(params, timeout=20):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        API_HOST + API_PATH, data=data, method="POST",
        headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.miyoushe.com/",
        })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_page(level_id, region, sort_type, cursor, target_id, delay):
    """抓单页，带重试"""
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            params = {
                "target_id": target_id,
                "region": region,
                "sort_type": sort_type,
                "cursor": cursor,
                "page_size": 20,
                "is_asc": 0,
            }
            body = _post(params)
            if body.get("retcode") != 0:
                raise RuntimeError(f"retcode={body.get('retcode')} msg={body.get('message')}")
            if delay:
                time.sleep(delay)
            return body.get("data", {})
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError(f"page failed after {MAX_RETRIES} tries: {last_err}")


# ---------------------------------------------------------------- 解析

def _parse_rows(data):
    """把接口返回的行标准化"""
    out = []
    for r in data.get("list", []) or []:
        reply_stat = r.get("reply_stat") or {}
        try:
            like = int(str(reply_stat.get("like_count", 0)).replace(",", "") or 0)
        except (ValueError, TypeError):
            like = 0
        out.append({
            "reply_id": r.get("reply_id"),
            "floor_id": r.get("floor_id"),
            "uid": str(r.get("uid", "")),
            "user": r.get("user") or "",
            "content": r.get("content") or "",
            "date": r.get("created_at") or "",
            "like": like,
            "ip": r.get("ip_location") or "",
            "is_recommend": r.get("is_recommend"),
        })
    return out


# ---------------------------------------------------------------- 单趟抓取

def _crawl_pass(level_id, region, sort_type, target_id, delay, max_pages=200):
    p = Pass(sort_type=sort_type)
    cursor = "0"
    seen_cursors = set()
    tail = 0

    try:
        for _ in range(max_pages):
            data = _fetch_page(level_id, region, sort_type, cursor, target_id, delay)
            p.pages += 1
            p.rows.extend(_parse_rows(data))

            has_more = data.get("has_more")
            nxt = data.get("cursor")

            # has_more 提前变 false：多跑几页确认游标成环
            if not has_more:
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
    except Exception as e:  # noqa: BLE001
        p.error = str(e)
    return p


# ---------------------------------------------------------------- 主入口

def crawl(level_id, region="cn_gf01", delay=DEFAULT_DELAY, target_id=None,
          sorts=None, max_pages=200):
    """
    抓取一个奇域的全部公开评论。

    level_id : 奇域 ID（米游社 UGC 短链 id）
    region   : 站点，cn_gf01=国服，os_usa=国际服
    delay    : 请求间隔秒数，默认 0.35（请勿调至 0.1 以下）
    target_id: 评论所属的 target_id，默认与 level_id 相同
    """
    if sorts is None:
        sorts = [SORT_FLOOR_DESC, SORT_HOT]

    res = CrawlResult(level_id=str(level_id), region=region, target_id=target_id)
    res.target_id = target_id or str(level_id)

    # 元信息
    try:
        meta_body = _post({"target_id": res.target_id, "region": region})
        res.meta = meta_body.get("data", {}) or {}
        res.total = res.meta.get("total", 0) or 0
    except Exception:
        res.meta = {}

    for st in sorts:
        p = _crawl_pass(level_id, region, st, res.target_id, delay, max_pages)
        res.passes.append(p)

    # 合并：floor_id 去重，点赞数取 max
    merged = {}
    for p in res.passes:
        for row in p.rows:
            key = row["floor_id"]
            if key in merged:
                merged[key]["like"] = max(merged[key]["like"], row["like"])
            else:
                merged[key] = row
    res.rows = list(merged.values())

    return res


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

    print(f"抓取 {len(r.rows)}/{r.total} 条 | degraded={r.degraded}")
    for k, v in checks.items():
        print(f"  {'✅' if v else '❌'} {k}")
    for p in r.passes:
        print(f"  pass sort={p.sort_type} pages={p.pages} error={p.error}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({
            "level_id": r.level_id, "region": r.region, "total": r.total,
            "meta": r.meta, "rows": r.rows,
            "passes": [asdict(p) | {"rows": []} for p in r.passes],
            "degraded": r.degraded, "checks": checks,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {args.out}")
    if not ok:
        print("⚠️  完整性校验未通过 —— 请先排查翻页问题，不要直接交付")
