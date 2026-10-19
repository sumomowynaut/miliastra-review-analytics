"""
模板语探测与剥离 —— methodology/02-template-stripping.md 的实现

核心问题：UGC 评论区存在"用户勾选即自动附带"的默认文案。
不剥离会让所有体验结论失真（实测差评率被低估 9.9pt）。

设计要点：
  · 模板从数据里自动探测，不硬编码任何平台文案
  · 支持用户补充（同一平台不同内容模板可能不同）
  · 剥离后自动验证，未命中率与重复率都会报告
"""

import re
from collections import Counter

# 归一化时剥离的标点与空白（抓"带/不带 ~"的变体）
_PUNCT = re.compile(r"""[~～。，,！!？?、；;：:""''（）()\[\]【】\s·…—\-]""")


def normalize(text):
    """归一化：去掉标点空白，用于识别同一句话的变体"""
    return _PUNCT.sub("", text or "")


def detect_templates(details, min_count=8, max_len=60, min_len=6, top_n=50):
    """
    自动探测疑似模板文案。

    启发式依据：
      · 模板重复率接近 100%（实测 729 条同一句）
      · 真实评论重复率很低（703 条 → 675 种唯一，重复率 4%）

    返回 [(归一化文本, 命中次数), ...]，按次数降序
    """
    counter = Counter(normalize(d.get("content", "")) for d in details)
    templates = []
    for text, n in counter.most_common(top_n):
        if n < min_count:
            break
        if not (min_len <= len(text) <= max_len):
            continue
        templates.append((text, n))
    return templates


def classify_templates(details, extra_templates=None, min_count=8,
                       min_len=6, max_len=60, verbose=True):
    """
    把评论分为「模板」和「真实」两类。

    extra_templates : 用户手动补充的模板（补充而非替换自动探测结果）
    返回 (template_set, stats)
    """
    auto = detect_templates(details, min_count=min_count,
                            min_len=min_len, max_len=max_len)
    template_set = {t for t, _ in auto}
    auto_counts = dict(auto)

    user_added = []
    for t in (extra_templates or []):
        key = normalize(t)
        if key and key not in template_set:
            template_set.add(key)
            user_added.append(key)

    for d in details:
        d["is_template"] = normalize(d.get("content", "")) in template_set

    real = [d for d in details if not d["is_template"]]
    template_rows = [d for d in details if d["is_template"]]

    # 验证：真实评论里应该几乎没有重复了
    uniq = len({normalize(d.get("content", "")) for d in real})
    dup_rate = 100 - (uniq / len(real) * 100) if real else 0.0

    stats = {
        "total": len(details),
        "template_count": len(template_rows),
        "template_pct": len(template_rows) / len(details) * 100 if details else 0,
        "real_count": len(real),
        "real_unique": uniq,
        "real_dup_rate": dup_rate,
        "auto_detected": auto_counts,
        "user_added": user_added,
        "top_templates": [
            {"text": t, "count": n,
             "pct_of_templates": n / len(template_rows) * 100 if template_rows else 0}
            for t, n in auto[:5]
        ],
    }

    if verbose:
        print(f"模板 {stats['template_count']}/{stats['total']} "
              f"({stats['template_pct']:.1f}%) | "
              f"真实 {stats['real_count']} → {uniq} 种唯一, 重复率 {dup_rate:.1f}%")
        for t in stats["top_templates"]:
            print(f"  · [{t['count']}次, {t['pct_of_templates']:.1f}%] {t['text']}")
        if user_added:
            print(f"  用户补充 {len(user_added)} 条")
        if dup_rate > 15:
            print(f"  [WARN] 真实评论重复率 {dup_rate:.1f}% 偏高，可能还有模板变体未探测到")

    return template_set, stats


def dual_rates(details):
    """
    双口径统计 —— 表观（对齐官方）与真实（用于结论）。
    报告时两个都要给。
    """
    def rate(rows, key):
        if not rows:
            return 0.0
        return sum(1 for r in rows if r.get(key)) / len(rows) * 100

    apparent_good = rate(details, "is_recommend")
    real = [d for d in details if not d.get("is_template")]
    real_good = rate(real, "is_recommend")

    return {
        "apparent": {
            "total": len(details),
            "good_rate": apparent_good,
            "bad_rate": 100 - apparent_good,
        },
        "real": {
            "total": len(real),
            "good_rate": real_good,
            "bad_rate": 100 - real_good,
        },
        "delta_bad_rate": (100 - real_good) - (100 - apparent_good),
    }


def group_by_month(details, real_only=True):
    """
    按月分组，返回双口径的绝对量与占比。
    绝对量与占比必须并排 —— 占比会被分母稀释（见 04-trend-pitfalls.md）
    """
    buckets = {}
    for d in details:
        if real_only and d.get("is_template"):
            continue
        date = d.get("date", "")
        month = date[:7] if len(date) >= 7 else "unknown"
        b = buckets.setdefault(month, {"total": 0, "good": 0, "bad": 0})
        b["total"] += 1
        if d.get("is_recommend"):
            b["good"] += 1
        else:
            b["bad"] += 1

    rows = []
    for month in sorted(buckets):
        b = buckets[month]
        rows.append({
            "month": month,
            "total": b["total"],
            "good": b["good"],
            "bad": b["bad"],
            "bad_pct": b["bad"] / b["total"] * 100 if b["total"] else 0,
            "good_pct": b["good"] / b["total"] * 100 if b["total"] else 0,
            "sample_note": "" if b["total"] >= 30 else f"样本不足({b['total']})",
        })
    return rows
