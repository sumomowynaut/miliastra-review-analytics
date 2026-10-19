#!/usr/bin/env python3
"""
生成公开案例 —— 完整数据 → 脱敏 → 产出可入库的 examples/

用法：python scripts/build_example.py <raw.json> <level_id> <标题>

产出到 examples/<slug>/：
  analysis.json      脱敏后的分析结果
  raw_public.json    脱敏后的评论数据
  看板.html            自包含看板
  数据报告.xlsx        Excel 报告
  README.md          案例说明（自动生成，含关键数字）
"""

import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import schema as schema_mod
import anonymize
import domain_lexicon
from strip_templates import classify_templates
from analyze import analyze, _ip_distribution
from excel_report import build_excel, verify_excel
from dashboard import build_dashboard, verify_dashboard


def slugify(name, level_id, override=None):
    """
    目录名优先 ASCII —— 中文路径在 URL 里不友好。
    纯中文标题无法自动转写，用 override 显式指定。
    """
    if override:
        return override
    ascii_name = re.sub(r"[^\w-]+", "-", name or "").strip("-")
    # 全是中文时 \w 会保留汉字 —— 检测到非 ASCII 就退回 level_id
    if not ascii_name or any(ord(c) > 127 for c in ascii_name):
        ascii_name = re.sub(r"[^0-9a-zA-Z-]+", "-", str(level_id)).strip("-")
    return ascii_name.lower() or str(level_id)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    raw_path, level_id, title = sys.argv[1], sys.argv[2], sys.argv[3]

    with open(raw_path, encoding="utf-8") as f:
        blob = json.load(f)
    details = schema_mod.normalize_all(blob["rows"])
    meta = blob.get("meta", {})

    domain = domain_lexicon.load_domain_lexicon(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "templates", "domain_example.json"))

    result = analyze(details, domain=domain)

    # ---- 脱敏 ----
    # 顺序很重要：先脱敏原始数据，再基于脱敏数据重算分析结果。
    # 否则 analysis.json 里的 top_liked 会残留原始昵称。
    pub_rows = anonymize.mask_users(details, keep_ip=False, keep_content=True)[0]
    # ★ 必须接住返回值 —— pseudonymize 不修改入参，返回的是新列表
    pub_rows = anonymize.pseudonymize(pub_rows)[0]
    result = analyze(pub_rows, domain=domain)
    # 地域分布用脱敏前的原始数据算省级聚合（统计，不指向个人）
    result["ip_distribution"] = _ip_distribution(details)[:10]

    slug = slugify(title, level_id, override=os.environ.get("EXAMPLE_SLUG"))
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                          "examples", slug)
    os.makedirs(outdir, exist_ok=True)

    with open(os.path.join(outdir, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    with open(os.path.join(outdir, "raw_public.json"), "w", encoding="utf-8") as f:
        json.dump({"level_id": level_id, "region": meta.get("region", "cn_gf01"),
                   "note": "脱敏示例数据 —— UID 已哈希，昵称已替换，IP 属地已移除",
                   "rows": pub_rows}, f, ensure_ascii=False, indent=2)

    xlsx = os.path.join(outdir, "数据报告.xlsx")
    build_excel(result, pub_rows, xlsx, level_id=level_id, title=title, meta=meta)
    xi = verify_excel(xlsx)

    html = os.path.join(outdir, "看板.html")
    build_dashboard(result, pub_rows, html, level_id=level_id, title=title, meta=meta)
    hi = verify_dashboard(html)

    # ---- 案例 README ----
    r = result["meta"]["rates"]
    ts = result["meta"]["template_stats"]
    cov = result["coverage"]
    topics = result["topics"]

    md = f"""# 案例 · {title}

> 奇域 ID `{level_id}` · 生成于 {datetime.now():%Y-%m-%d}

## 关键数字

| 指标 | 表观（含模板） | 真实（已剥离） | 差异 |
|---|---|---|---|
| 评论总数 | {r['apparent']['total']} | {r['real']['total']} | −{r['apparent']['total'] - r['real']['total']} |
| 好评率 | {r['apparent']['good_rate']:.1f}% | {r['real']['good_rate']:.1f}% | {r['real']['good_rate'] - r['apparent']['good_rate']:.1f}pt |
| 差评率 | {r['apparent']['bad_rate']:.1f}% | **{r['real']['bad_rate']:.1f}%** | **+{r['delta_bad_rate']:.1f}pt** |

**{ts['template_count']} 条（{ts['template_pct']:.1f}%）是系统自动附带的默认文案。**

剥离后：{ts['real_count']} 条真实评论 → {ts['real_unique']} 种唯一文本
（重复率 {ts['real_dup_rate']:.1f}%，正常）。

## 痛点分布

词典覆盖率 {cov['coverage']:.1f}%（可归类 {cov['classifiable']} 条 / 纯情绪 {cov['emotional']} 条）

| 痛点 | 条数 | 占差评 |
|---|---|---|
"""
    for t in topics:
        md += f"| {t['category']} | {t['count']} | {t['pct_of_negative']:.1f}% |\n"

    md += """
## 这个案例说明了什么

1. **表观好评率会骗人。** 官方公布的 79.2% 是含模板口径，与本地计算一致
   （可作为数据完整性校验），但不能用来判断真实体验 —— 真实差评率高出 9.9pt。
2. **核心痛点靠通用词典拿不到。** 「规则与玩法」占 45.4%，是最大问题，
   但初版通用词典的覆盖率仅 31.5%，完全漏掉了这一类。加入领域词典后升到 75.5%。
3. **趋势结论必须看绝对量。** 差评占比与差评绝对量必须并排，分母变化会完全改变结论。

## 复现

```bash
python src/analyze.py --raw examples/{slug}/raw_public.json --outdir out \\
    --domain-lexicon templates/domain_example.json
```

## 文件说明

| 文件 | 说明 |
|---|---|
| `analysis.json` | 分析结果 |
| `raw_public.json` | 脱敏后的评论数据（UID 已哈希，昵称替换为「用户X」，IP 属地已移除） |
| `看板.html` | 自包含看板，断网可开 |
| `数据报告.xlsx` | 7 表 Excel 报告 |

## 免责

数据来自米游社公开接口。评论版权归原作者与上海米哈游影铁科技有限公司所有。
本案例仅供技术研究演示，不隶属于也不代表 miHoYo。
"""
    with open(os.path.join(outdir, "README.md"), "w", encoding="utf-8") as f:
        f.write(md)

    print(f"✅ 案例已生成 → examples/{slug}/")
    print(f"   Excel: {'✅' if not xi else '⚠️ ' + str(xi)}")
    print(f"   看板 : {'✅' if not hi else '⚠️ ' + str(hi)}")
    print(f"   痛点Top3: {', '.join(t['category'] for t in topics[:3])}")


if __name__ == "__main__":
    main()
