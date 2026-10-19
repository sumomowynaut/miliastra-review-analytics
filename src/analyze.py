"""
主分析流程 —— 输入一个奇域 ID，产出可信的体验分析

用法：
  python analyze.py --level-id 50105539813          # 真实抓取（需网络）
  python analyze.py --fixture fixtures/sample_chess_30.json  # 用脱敏样例跑通
  python analyze.py --level-id xxx --outdir out/xxx

产出：
  out/<id>/raw.json            原始数据（不进 git）
  out/<id>/analysis.json       分析结果
  out/<id>/report.xlsx        Excel 报告
  out/<id>/dashboard.html      自包含看板
"""

import argparse
import json
import os
import sys
import random
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler import crawl
from strip_templates import classify_templates, dual_rates, group_by_month
import anonymize
import schema as schema_mod
import domain_lexicon
from excel_report import build_excel, verify_excel
from dashboard import build_dashboard, verify_dashboard

# 痛点词典 —— 允许重叠，最终由人工裁决（见 03-topic-clustering.md）
# 词典必须按实际数据扩充 —— 象棋案例里最大的盲区是「玩法与规则」
# （超时判负、将军次数限制、开始键缺失），初版完全没有覆盖。
CATEGORIES = {
    "操作手感": ["卡", "延迟", "掉帧", "失灵", "判定", "碰撞", "操作", "转向", "手感"],
    "性能与稳定性": ["卡顿", "卡死", "闪退", "加载", "崩溃", "黑屏", "闪退"],
    "内容与深度": ["单调", "无聊", "简单", "重复", "缺少", "太少", "内容", "敷衍", "没做完"],
    "关卡设计": ["难度", "太难", "太简单", "死", "卡关", "地图", "路线", "解法", "无解"],
    "音画体验": ["音", "画质", "贴图", "模型", "特效", "music", "ugly", "贴图"],
    "ui与交互": ["ui", "界面", "按钮", "菜单", "交互", "开始键", "开始"],
    # ★ 规则与玩法缺陷 —— 竞智/策略类关卡的核心痛点
    "规则与玩法": [
        "规则", "机制", "bug", "错误", "异常", "失效", "不能", "无法", "没法",
        "超时", "判负", "判赢", "自动判", "限制", "禁止", "不允许", "强制",
        "将军", "次数", "计时", "分钟", "退出", "重开", "重连",
        "匹配", "联机", "单人", "多人", "对手", "公平", "作弊", "耍赖",
        "没有", "缺少", "取消", "设置", "选项",
    ],
    "合规与审核": ["过审", "审核", "违规", "下架", "封"],
    # ★ 纯情绪吐槽 —— 单独归类，不混入功能类别
    #   这类评论没有可行动的产品信息，混进去会污染痛点排序。
    #   单列出来是为了让"未命中"里剩下的确实是产品问题。
    "情绪化吐槽": ["垃圾", "恶心", "白痴", "脑瘫", "离谱", "依托", "答辩", "答辩",
                    "rz", "屑", "难看", "烂", "有病", "什么玩意", "做的啥"],
}

# 否定语义 —— 命中关键词但语义为正面，必须剔除（否则好评会被误判为差评）
NEGATION = ["不卡", "不好玩", "没难度", "不无聊", "不错", "还行", "不卡顿",
            "没有bug", "没bug", "不简单", "不难", "可以", "满意", "推荐"]


def cluster_topics(negative_rows, domain=None, categories=None):
    """痛点聚类：词典召回 + 否定剔除 + 未命中率报告"""
    cats = categories or CATEGORIES
    if domain:
        cats = domain_lexicon.merge_categories(cats, domain)
    ignore = set(domain.get("ignore", [])) if domain else set()

    matched = {c: [] for c in cats}
    unmatched = []
    emotional = []

    for d in negative_rows:
        text = (d.get("content") or "").lower()
        # 纯辱骂/屏蔽词 —— 无产品信息量，不混入功能类别
        if any(w.lower() in text for w in ignore):
            emotional.append(d)
            continue
        hit = None
        for cat, kws in cats.items():
            if any(k.lower() in text for k in kws):
                hit = cat
                break
        if hit:
            matched[hit].append(d)
        else:
            unmatched.append(d)

    # 否定剔除
    for cat in matched:
        matched[cat] = [
            d for d in matched[cat]
            if not any(n in (d.get("content") or "").lower() for n in NEGATION)
        ]

    # 计数与未命中率
    total = len(negative_rows)
    stats = []
    for cat, rows in matched.items():
        if not rows:
            continue
        stats.append({
            "category": cat,
            "count": len(rows),
            "pct_of_negative": len(rows) / total * 100 if total else 0,
            "samples": [
                {"date": r.get("date", "")[:10], "text": (r.get("content") or "")[:60]}
                for r in sorted(rows, key=lambda x: -x.get("like", 0))[:3]
            ],
        })
    stats.sort(key=lambda x: -x["count"])

    # 覆盖率分母排除纯情绪吐槽 —— 它们本来就不该被归类
    classifiable = total - len(emotional)
    coverage = ((classifiable - len(unmatched)) / classifiable * 100
                if classifiable else 0)
    return stats, {
        "negative_total": total,
        "unmatched": len(unmatched),
        "emotional": len(emotional),
        "classifiable": classifiable,
        "coverage": coverage,
        "coverage_note": ("⚠️ 覆盖率偏低，词典需补充"
                          if coverage < 70 else "✅ 覆盖率可接受"),
        "unmatched_samples": [
            {"date": u.get("date", "")[:10], "text": (u.get("content") or "")[:60]}
            for u in unmatched[:5]
        ],
    }


def analyze(details, extra_templates=None, domain=None):
    """核心分析流程"""
    template_set, tstats = classify_templates(details, extra_templates=extra_templates)
    rates = dual_rates(details)
    monthly = group_by_month(details, real_only=True)
    monthly_apparent = group_by_month(details, real_only=False)

    real_neg = [d for d in details
                if not d.get("is_template") and not d.get("is_recommend")]
    topics, coverage = cluster_topics(real_neg, domain=domain)

    # 高赞评论（排除模板）
    top_liked = sorted(
        [d for d in details if not d.get("is_template")],
        key=lambda x: -x.get("like", 0))[:20]

    # 地域分布
    ip_counter = Counter(d.get("ip") for d in details
                          if not d.get("is_template") and d.get("ip"))

    return {
        "meta": {
            "total_comments": len(details),
            "template_stats": tstats,
            "rates": rates,
        },
        "topics": topics,
        "coverage": coverage,
        "monthly_real": monthly,
        "monthly_apparent": monthly_apparent,
        "top_liked": [
            {"date": d.get("date", "")[:10], "user": d.get("user", ""),
             "like": d.get("like", 0), "text": (d.get("content") or "")[:80]}
            for d in top_liked
        ],
        "ip_distribution": ip_counter.most_common(15),
    }


def _ip_distribution(original_details, top=15):
    """
    省份级聚合分布 —— 从**脱敏前**的原始数据算。

    为什么要分开算：
      · 行级 IP（"这条评论来自广东"）= 个人身份，必须移除
      · 省份级聚合（"广东有 111 条评论"）= 统计结果，不指向个人，可以保留

    如果先脱敏再算，这个功能就整个消失了 —— 那是把"删个人信息"做成了"删功能"。
    """
    from collections import Counter
    c = Counter(d.get("ip") for d in original_details
                if d.get("ip"))
    return c.most_common(top)


def _scan_output_for_identities(outdir, original_details):
    """
    产出自检：确认输出目录里没有残留真实身份。

    ★ 关键：按「字段」检查，不做全文件子串搜索。
      昵称里混着 "404"、"。。。" 这类短串，它们天然会出现在评论正文里；
      全局搜子串会产出上千条误报，一个会误报的检查等于没有检查。

    判定标准：只有「与具体评论绑定的身份」才算泄漏。
      地域分布这类聚合结果（某省 111 条）不视为身份泄漏 —— 它不指向个人。
    """
    real_uids = {str(d.get("uid", "")) for d in original_details if d.get("uid")}
    real_names = {d.get("user", "") for d in original_details if d.get("user")}
    real_ips = {d.get("ip", "") for d in original_details if d.get("ip")}

    ID_KEYS = ("uid", "user", "ip")
    hits = []

    def check_value(value, kind, where):
        if not isinstance(value, str) or not value:
            return
        if kind == "UID" and value in real_uids:
            hits.append((where, kind, value))
        elif kind == "昵称" and value in real_names:
            hits.append((where, kind, value))
        elif kind == "IP属地" and value in real_ips:
            hits.append((where, kind, value))

    def walk_json(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ID_KEYS:
                    check_value(v, {"uid": "UID", "user": "昵称",
                                    "ip": "IP属地"}[k], where)
                else:
                    walk_json(v, where)
        elif isinstance(node, list):
            for item in node:
                walk_json(item, where)

    for root, _, files in os.walk(outdir):
        for fn in files:
            path = os.path.join(root, fn)

            if fn.endswith(".json"):
                try:
                    with open(path, encoding="utf-8") as f:
                        walk_json(json.load(f), path)
                except Exception:
                    continue

            elif fn.endswith(".xlsx"):
                try:
                    from openpyxl import load_workbook
                    wb = load_workbook(path, read_only=True)
                    for ws in wb.worksheets:
                        rows_iter = ws.iter_rows(values_only=True)
                        header = next(rows_iter, None)
                        if not header:
                            continue
                        # 只检查身份列：用户 / UID / IP属地
                        idx = {i: str(h).strip() for i, h in enumerate(header or [])
                               if h and str(h).strip() in ("用户", "UID", "IP属地")}
                        if not idx:
                            continue
                        for row in rows_iter:
                            for i, kind in idx.items():
                                if i < len(row):
                                    check_value(row[i], kind, path)
                except Exception:
                    continue

            elif fn.endswith(".html"):
                # 看板把数据嵌在 JS 里，按 "uid"/"user"/"ip" 字段模式定位
                import re
                try:
                    with open(path, encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    continue
                for key, kind in (("uid", "UID"), ("user", "昵称"), ("ip", "IP属地")):
                    for m in re.finditer(
                            r'"%s"\s*:\s*"([^"]{1,64})"' % key, content):
                        check_value(m.group(1), kind, path)

    # 去重
    seen = set()
    uniq = []
    for path, kind, value in hits:
        k = (os.path.basename(path), kind)
        if k not in seen:
            seen.add(k)
            uniq.append((path, kind, value))
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level-id")
    ap.add_argument("--fixture", help="使用脱敏样例数据（无需网络）")
    ap.add_argument("--raw", help="使用 crawl.py 抓取好的 raw.json（避免重复抓取）")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--region", default="cn_gf01")
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--extra-templates", help="逗号分隔的额外模板文案")
    ap.add_argument("--domain-lexicon", help="领域词典 JSON 路径")
    ap.add_argument("--keep-identities", action="store_true",
                    help="保留真实 UID/昵称/IP 属地（仅本地排查用，默认脱敏）")
    args = ap.parse_args()

    if not (args.level_id or args.fixture or args.raw):
        ap.error("需要 --level-id / --fixture / --raw 之一")

    extra = [t.strip() for t in (args.extra_templates or "").split(",") if t.strip()]

    if args.raw:
        with open(args.raw, encoding="utf-8") as f:
            blob = json.load(f)
        details = schema_mod.normalize_all(blob["rows"])
        meta = blob.get("meta", {})
        level_id = blob.get("level_id", "unknown")
        title = meta.get("title", "") or meta.get("name", "")
        print(f"📂 载入 {args.raw}: {len(details)} 条")
        if blob.get("degraded"):
            print("⚠️  该次抓取被标记为 degraded —— 数据可能缺失，结论需谨慎")
        if blob.get("checks"):
            bad = [k for k, v in blob["checks"].items() if v is False]
            if bad:
                print(f"⚠️  抓取时校验未通过: {bad}")
    elif args.fixture:
        with open(args.fixture, encoding="utf-8") as f:
            blob = json.load(f)
        raw = blob["rows"] if isinstance(blob, dict) and "rows" in blob else blob
        details = schema_mod.normalize_all(raw)
        issues = schema_mod.validate_schema(details)
        if issues:
            print("⚠️  schema 校验:", issues)
        meta = blob.get("meta", {}) if isinstance(blob, dict) else {}
        level_id = blob.get("level_id", "fixture") if isinstance(blob, dict) else "fixture"
        title = blob.get("title", "") if isinstance(blob, dict) else ""
        print(f"📦 使用 fixture: {len(details)} 条")
    else:
        print(f"🌐 抓取 {args.level_id} ...（约需 1-3 分钟）")
        res = crawl(args.level_id, args.region, args.delay)
        details = res.rows
        meta = res.meta
        level_id = args.level_id
        title = meta.get("title", "") or meta.get("name", "")
        ok, checks = res.integrity()
        print(f"抓取 {len(details)}/{res.total} 条 | degraded={res.degraded}")
        for k, v in checks.items():
            print(f"  {'✅' if v else '❌'} {k}")
        if not ok:
            print("⚠️  完整性校验未通过 —— 数据可能有缺失，分析结果不可信")

    domain = domain_lexicon.load_domain_lexicon(args.domain_lexicon)
    extra_templates = list(extra)
    if meta.get("goodRate"):
        print(f"官方好评率: {meta['goodRate']}（用于口径对齐校验）")

    # ---- 脱敏默认开启 ----
    # methodology/06 交付物检查要求：对外交付的产出不得含真实用户身份。
    # 工具必须强制执行自己的方法论，所以默认脱敏全部产出（Excel / 看板 / analysis.json）。
    # 确需保留身份（本地排查、回复某个用户）时用 --keep-identities 显式关闭。
    if args.keep_identities:
        print("⚠️  已指定 --keep-identities：产出将包含真实 UID / 昵称 / IP 属地，"
              "仅供本地使用，切勿直接对外分享")
        pub_details = details
    else:
        pub_details = anonymize.mask_users(details, keep_ip=False,
                                           keep_content=True)[0]
        # ★ 必须接住返回值 —— pseudonymize 不修改入参，返回的是新列表
        pub_details = anonymize.pseudonymize(pub_details)[0]

    result = analyze(pub_details, extra_templates=extra_templates, domain=domain)

    # 地域分布用脱敏前的原始数据算聚合统计（不指向个人），
    # 覆盖掉 analyze() 基于脱敏数据得到的空结果
    if not args.keep_identities:
        result["ip_distribution"] = _ip_distribution(details)

    outdir = os.path.join(args.outdir, str(level_id))
    os.makedirs(outdir, exist_ok=True)

    with open(os.path.join(outdir, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    # Excel
    xlsx_path = os.path.join(outdir, f"千星奇域{level_id}_数据报告.xlsx")
    build_excel(result, pub_details, xlsx_path, level_id=level_id, title=title,
                meta=meta)
    xissues = verify_excel(xlsx_path)
    print(f"📊 Excel → {xlsx_path}" + (f"  ⚠️ {xissues}" if xissues else "  ✅ 校验通过"))

    # 看板
    html_path = os.path.join(outdir, f"千星奇域{level_id}_看板.html")
    build_dashboard(result, pub_details, html_path, level_id=level_id, title=title,
                    meta=meta)
    hissues = verify_dashboard(html_path)
    print(f"📈 看板 → {html_path}" + (f"  ⚠️ {hissues}" if hissues else "  ✅ 自包含校验通过"))

    # 脱敏后的完整数据
    with open(os.path.join(outdir, "raw_public.json"), "w", encoding="utf-8") as f:
        json.dump({"level_id": level_id, "meta": meta,
                   "note": "UID 已哈希，昵称已替换，IP 属地已移除；评论正文为玩家原话",
                   "rows": pub_details}, f, ensure_ascii=False, indent=2)
    print(f"🔒 脱敏数据: {outdir}/raw_public.json")

    if not args.keep_identities:
        # 自检：产出目录里是否还残留真实身份
        leak = _scan_output_for_identities(outdir, details)
        if leak:
            print(f"⚠️  产出目录检测到 {len(leak)} 处身份残留 —— 请排查")
            for h in leak:
                print("   ", h)
        else:
            print("✅ 产出自检：未发现真实 UID / 昵称 / IP 属地")

    print(f"\n✅ 分析完成 → {outdir}/")

    r = result["meta"]["rates"]
    print(f"\n表观好评率 {r['apparent']['good_rate']:.1f}% "
          f"| 真实好评率 {r['real']['good_rate']:.1f}% "
          f"（差评率被低估 {r['delta_bad_rate']:.1f}pt）")
    print(f"痛点覆盖率 {result['coverage']['coverage']:.1f}% "
          f"{result['coverage']['coverage_note']}")


if __name__ == "__main__":
    main()
