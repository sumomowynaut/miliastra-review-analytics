#!/usr/bin/env python3
"""
抓取命令行入口

用法：
  python src/crawl.py 50105539813                    # 抓取并写入 data/raw.json
  python src/crawl.py 50105539813 --region os_usa    # 国际服
  python src/crawl.py 50105539813 --delay 0.5        # 放慢速度（推荐）

抓取完成后会打印完整性校验结果。**校验未通过时不要直接用于分析** ——
残缺数据比没有数据更危险，因为它看起来是完整的。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler import crawl, DEFAULT_DELAY

# 完整性校验的通过门槛
PASS_RULES = [
    ("count_matches_total", "条数与接口声明一致"),
    ("reply_id_unique", "回复 ID 唯一"),
    ("span_reasonable", "楼层跨度合理（无漏页）"),
    ("no_degraded_pass", "所有抓取趟均正常"),
    ("all_passes_produced", "每一趟都有产出"),
    ("floor_id_present", "楼层号完整"),
    ("goodrate_aligned", "好评率与官方对齐"),
]


def main():
    ap = argparse.ArgumentParser(description="抓取千星奇域公开评论")
    ap.add_argument("level_id", help="奇域 ID")
    ap.add_argument("--region", default="cn_gf01", help="cn_gf01=国服, os_usa=国际服")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help="请求间隔秒数（默认 0.35，请勿低于 0.2）")
    ap.add_argument("--out", default="data/raw.json")
    ap.add_argument("--max-pages", type=int, default=200)
    args = ap.parse_args()

    if args.delay < 0.2:
        print("[WARN] delay < 0.2s 可能触发风控，已强制提升至 0.2s")
        args.delay = 0.2

    print(f"[CRAWL] 抓取 {args.level_id} (region={args.region}, delay={args.delay}s)")
    print("   预计 1-3 分钟，请勿并发运行\n")

    res = crawl(args.level_id, args.region, args.delay,
                max_pages=args.max_pages)

    print(f"抓取 {len(res.rows)}/{res.total} 条 | degraded={res.degraded}\n")
    ok, checks = res.integrity()

    print("完整性校验：")
    for key, desc in PASS_RULES:
        val = checks.get(key)
        mark = "[OK]" if val else ("[-]" if val is None else "[FAIL]")
        print(f"  {mark} {desc}")

    print("\n各趟明细：")
    for p in res.passes:
        err = f"  ERROR: {p.error}" if p.error else ""
        print(f"  sort_type={p.sort_type:<3} pages={p.pages:<4} rows={len(p.rows)}{err}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({
            "level_id": res.level_id,
            "region": res.region,
            "total": res.total,
            "meta": res.meta,
            "rows": res.rows,
            "degraded": res.degraded,
            "checks": checks,
            "passes": [{"sort_type": p.sort_type, "pages": p.pages,
                        "error": p.error, "row_count": len(p.rows),
                        "missing_floor": p.missing_floor,
                        "truncated": p.truncated}
                       for p in res.passes],
        }, f, ensure_ascii=False, indent=2)

    print(f"\n已写入 {args.out}")
    if not ok:
        print("\n[WARN] 完整性校验未通过 —— 请先排查翻页问题，不要直接用于分析")
        return 1
    print("[OK] 完整性校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
