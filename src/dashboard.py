"""
自包含 HTML 看板生成

硬性要求（methodology/06-delivery-checklist.md）：
  · 单文件，无外部 CSS/JS/字体依赖，断网可开
  · file:// 协议直接双击可开
  · 图表用纯 SVG，不引第三方库
"""

from datetime import datetime

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:"Microsoft YaHei","PingFang SC",
 -apple-system,"Segoe UI",sans-serif;padding:28px 20px 60px;line-height:1.6}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:26px;font-weight:700;letter-spacing:.5px}
.sub{color:#7d8590;font-size:13px;margin-top:6px}
h2{font-size:16px;font-weight:600;margin:34px 0 14px;padding-left:10px;
 border-left:3px solid #388bfd;display:flex;align-items:center;gap:8px}
.card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:18px 20px;margin-bottom:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:20px 0}
.kpi{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:16px 18px}
.kpl{color:#7d8590;font-size:12px;margin-bottom:6px}
.kpv{font-size:27px;font-weight:700;letter-spacing:-.5px}
.kvd{color:#7d8590;font-size:11px;margin-top:5px}
.up{color:#3fb950}.down{color:#f85149}.warn{color:#d29922}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#1c2128;color:#7d8590;font-weight:600;text-align:left;
 padding:9px 12px;font-size:12px;white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid #21262d;vertical-align:top}
tr:hover td{background:#1c2128}
.mono{font-family:"SF Mono",Consolas,Monaco,monospace;font-size:12px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:5px;background:#21262d;border-radius:3px;overflow:hidden;min-width:70px}
.bar i{display:block;height:100%;background:#388bfd;border-radius:3px}
.bar i.neg{background:#f85149}.bar i.tpl{background:#6e7681}
.note{background:#1c2128;border-left:3px solid #d29922;border-radius:0 7px 7px 0;
 padding:13px 16px;font-size:13px;color:#e6edf3;margin:14px 0}
.note b{color:#d29922}
.insight{background:#0f1b2d;border:1px solid #1f3a5f;border-radius:9px;padding:15px 18px;margin:11px 0}
.insight .t{color:#58a6ff;font-weight:600;font-size:13px;margin-bottom:6px}
.insight .b{font-size:13px;color:#c9d1d9}
.tags{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0}
.tag{background:#21262d;border:1px solid #30363d;border-radius:14px;padding:4px 12px;font-size:12px}
.tag .n{color:#58a6ff;font-weight:600;margin-left:5px}
.chart{width:100%;height:auto;display:block}
.legend{display:flex;gap:18px;font-size:12px;color:#7d8590;margin-bottom:10px;
 flex-wrap:wrap}
.legend span{display:flex;align-items:center;gap:6px}
.legend i{width:11px;height:11px;border-radius:2px;display:inline-block}
.q{display:flex;flex-direction:column;gap:8px}
.qi{border-left:2px solid #30363d;padding:7px 0 7px 13px}
.qi .d{font-size:11px;color:#6e7681;margin-top:3px}
.foot{color:#6e7681;font-size:12px;margin-top:40px;padding-top:18px;
 border-top:1px solid #21262d;line-height:1.9}
@media(max-width:640px){body{padding:16px 12px 40px}.kpv{font-size:22px}
 table{font-size:12px}th,td{padding:7px 8px}}
"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _trend_chart(rows, width=1100, height=240, pad_l=54, pad_b=42, pad_t=16):
    """双轴趋势图：柱=差评绝对量，折线=占比。纯 SVG。"""
    if not rows:
        return "<p class='note'>无趋势数据</p>"

    n = len(rows)
    max_abs = max(r["bad"] for r in rows) or 1
    max_pct = max(r["bad_pct"] for r in rows) or 1
    iw = width - pad_l - 18
    ih = height - pad_t - pad_b
    slot = iw / n
    bw = min(46, slot * 0.52)

    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" '
             f'preserveAspectRatio="xMidYMid meet">']

    # 网格
    for i in range(5):
        y = pad_t + ih * i / 4
        v = max_abs * (1 - i / 4)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-18}" y2="{y:.1f}" '
                     f'stroke="#21262d" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" fill="#6e7681" font-size="10" '
                     f'text-anchor="end">{int(v)}</text>')
        pv = max_pct * (1 - i / 4)
        parts.append(f'<text x="{width-14}" y="{y+4:.1f}" fill="#6e7681" font-size="10" '
                     f'text-anchor="end">{pv:.0f}%</text>')

    pts = []
    for i, r in enumerate(rows):
        cx = pad_l + slot * i + slot / 2
        bh = ih * (r["bad"] / max_abs)
        y = pad_t + ih - bh
        color = "#f85149"
        parts.append(f'<rect x="{cx-bw/2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                     f'height="{bh:.1f}" fill="{color}" rx="3" opacity="0.78"/>')
        # 占比折线点
        py = pad_t + ih - ih * (r["bad_pct"] / max_pct)
        pts.append((cx, py))
        if r.get("sample_note"):
            parts.append(f'<text x="{cx:.1f}" y="{pad_t+ih+16}" fill="#d29922" '
                         f'font-size="9" text-anchor="middle">*</text>')
        parts.append(f'<text x="{cx:.1f}" y="{pad_t+ih+30}" fill="#7d8590" '
                     f'font-size="10" text-anchor="middle">{_esc(r["month"])}</text>')

    d = " ".join(f"{'M' if i==0 else 'L'}{x:.1f},{y:.1f}"
                 for i, (x, y) in enumerate(pts))
    parts.append(f'<path d="{d}" fill="none" stroke="#58a6ff" stroke-width="2"/>')
    for x, y in pts:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="#58a6ff"/>')

    parts.append("</svg>")
    return "".join(parts)


def build_dashboard(analysis, details, out_path, level_id="", title="",
                    meta=None, subtitle=""):
    r = analysis["meta"]
    rates = r["rates"]
    ts = r["template_stats"]
    ap, rl = rates["apparent"], rates["real"]
    cov = analysis.get("coverage", {})

    html = [f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>千星奇域 {_esc(title or level_id)} 数据看板</title>
<style>{CSS}</style>
</head><body><div class="wrap">
<h1>千星奇域 · {_esc(title or level_id)}</h1>
<div class="sub">{_esc(subtitle)} ID <span class="mono">{_esc(level_id)}</span> ·
生成于 {datetime.now():%Y-%m-%d %H:%M}</div>"""]

    # KPI
    html.append('<div class="kpis">')
    html.append(f'''<div class="kpi"><div class="kpl">总评论数</div>
<div class="kpv">{ap['total']}</div>
<div class="kvd">含 {ts['template_count']} 条系统模板</div></div>''')
    html.append(f'''<div class="kpi"><div class="kpl">表观好评率</div>
<div class="kpv up">{ap['good_rate']:.1f}%</div>
<div class="kvd">与官方公布值口径一致</div></div>''')
    html.append(f'''<div class="kpi"><div class="kpl">真实好评率</div>
<div class="kpv warn">{rl['good_rate']:.1f}%</div>
<div class="kvd">已剥离 {ts['template_count']} 条模板</div></div>''')
    html.append(f'''<div class="kpi"><div class="kpl">真实差评率</div>
<div class="kpv down">{rl['bad_rate']:.1f}%</div>
<div class="kvd">表观仅 {ap['bad_rate']:.1f}%（低估 {rates['delta_bad_rate']:.1f}pt）</div></div>''')
    html.append(f'''<div class="kpi"><div class="kpl">模板语占比</div>
<div class="kpv" style="color:#6e7681">{ts['template_pct']:.1f}%</div>
<div class="kvd">{ts['real_count']} 条真实评论</div></div>''')
    html.append('</div>')

    # 核心洞察
    html.append('<h2>核心发现</h2>')
    if ap["bad_rate"] < rl["bad_rate"]:
        tpl_title = "① 表面好评率被系统模板撑起来"
        shift = (f'把表观差评率从真实的 {rl["bad_rate"]:.1f}% '
                 f'拉低到 {ap["bad_rate"]:.1f}%')
        bias = f'<b>只看官方公布的 {ap["good_rate"]:.1f}% 会高估体验。</b>'
    elif ap["bad_rate"] > rl["bad_rate"]:
        tpl_title = "① 系统模板掩盖了真实好评"
        shift = (f'把表观差评率从真实的 {rl["bad_rate"]:.1f}% '
                 f'抬高到 {ap["bad_rate"]:.1f}%')
        bias = f'<b>只看官方公布的 {ap["good_rate"]:.1f}% 会低估体验。</b>'
    else:
        tpl_title = "① 系统模板没有改变总体评价"
        shift = f'表观与真实差评率同为 {ap["bad_rate"]:.1f}%'
        bias = ""
    html.append(f'<div class="insight"><div class="t">{tpl_title}</div>'
                f'<div class="b">{ts["template_count"]} 条系统默认文案占全部评论的 '
                f'{ts["template_pct"]:.1f}%，{shift}。{bias}</div></div>')
    cov_note = ("；覆盖率偏低，类别之外可能还有未识别问题"
                if cov.get("coverage", 0) < 70 else "")
    html.append('<div class="insight"><div class="t">② 差评的主要类别</div>'
                f'<div class="b">真实差评 {cov.get("negative_total", 0)} 条，'
                f'词典覆盖率 {cov.get("coverage", 0):.1f}%{cov_note}。'
                f'Top 类别：{"、".join(t["category"] for t in analysis.get("topics", [])[:3]) or "—"}。</div></div>')
    recent = [m for m in analysis.get("monthly_real", [])
              if m.get("month") != "unknown"][-1:]
    if recent:
        m = recent[0]
        stale = "" if m["month"] == f"{datetime.now():%Y-%m}" else "（该月不是当前月）"
        html.append('<div class="insight"><div class="t">③ 最近一个有评论的月份</div>'
                    f'<div class="b">{_esc(m["month"])} 真实差评 {m["bad"]}/{m["total"]} 条 '
                    f'= {m["bad_pct"]:.1f}%{stale}。'
                    f'{"⚠️ 样本量不足 30，结论仅供参考。" if m["total"] < 30 else "样本量充足。"}</div></div>')

    # 模板剥离
    html.append('<h2>模板语剥离</h2>')

    # 模板清单
    html.append('<div class="card"><div class="tags">')
    templates = ts.get("templates") or ts.get("top_templates", [])
    for t in templates[:12]:
        label = t.get("sample") or t["text"]
        html.append(f'<span class="tag">{_esc(label)}<span class="n">×{t["count"]}</span></span>')
    if len(templates) > 12:
        html.append(f'<span class="tag">其余 {len(templates) - 12} 种…</span>')
    html.append('</div>')
    html.append(f'<div class="sub">剥离后验证：{ts["real_count"]} 条真实评论 → '
                f'{ts["real_unique"]} 种唯一文本（重复率 {ts["real_dup_rate"]:.1f}%，'
                f'{"正常" if ts["real_dup_rate"] < 15 else "偏高，可能仍有变体未探测到"}）</div></div>')

    # 差评趋势 —— 独立成节。趋势图讲的是"差评怎么随时间变化"，
    # 放在"模板语剥离"标题下会让读者以为图里画的是模板，标题与内容错位。
    html.append('<h2>差评趋势</h2>')
    html.append('<div class="card">')
    html.append('<div class="legend">'
                '<span><i style="background:#f85149"></i>差评绝对量</span>'
                '<span><i style="background:#58a6ff"></i>差评占比</span>'
                '<span><i style="background:#d29922"></i>* 样本量 &lt; 30</span></div>')
    html.append(_trend_chart(analysis.get("monthly_real", [])))
    html.append('<div class="note"><b>提示：</b>占比会被分母稀释 —— 绝对量与占比必须并排看。'
                '整体差评率下降可能只是没人玩了，不是体验变好。</div>')
    html.append('</div>')


    # 痛点
    html.append('<h2>痛点聚类</h2><div class="card"><table>')
    html.append('<tr><th>痛点类别</th><th class="num">条数</th><th class="num">占差评比</th>'
                '<th style="width:180px">分布</th><th>代表评论</th></tr>')
    for t in analysis.get("topics", []):
        pct = t["pct_of_negative"]
        s = t.get("samples", [])
        sample = s[0]["text"] if s else ""
        html.append(f'''<tr><td><b>{_esc(t["category"])}</b></td>
<td class="num">{t["count"]}</td><td class="num">{pct:.1f}%</td>
<td><div class="bar"><i class="neg" style="width:{min(100, pct*2.5):.1f}%"></i></div></td>
<td>{_esc(sample)}</td></tr>''')
    html.append('</table>')
    if cov.get("unmatched"):
        html.append(f'<div class="note"><b>未命中 {cov["unmatched"]} 条</b>'
                    f'（覆盖率 {cov.get("coverage", 0):.1f}%）—— 需人工复核补词典。</div>')
    html.append('</div>')

    # 高赞
    html.append('<h2>高赞评论 TOP（已排除模板）</h2><div class="card"><div class="q">')
    for t in analysis.get("top_liked", [])[:10]:
        html.append(f'''<div class="qi">{_esc(t["text"])}
<div class="d">{_esc(t["date"])} · 👍 {t["like"]}</div></div>''')
    html.append('</div></div>')

    # 地域
    ips = analysis.get("ip_distribution", [])
    if ips:
        html.append('<h2>IP 属地分布</h2><div class="card"><div class="tags">')
        tot = sum(x[1] for x in ips) or 1
        for name, cnt in ips[:12]:
            html.append(f'<span class="tag">{_esc(name)}<span class="n">'
                        f'{cnt} ({cnt/tot*100:.1f}%)</span></span>')
        html.append('</div></div>')

    # 方法论
    html.append('<h2>分析方法</h2><div class="card"><table>')
    html.append('<tr><th>环节</th><th>做法</th><th>为什么</th></tr>')
    for a, b, c in [
        ("完整性校验", "双趟排序合并 + 七项校验", "单趟排序会漏约 10%，且不报错"),
        ("模板剥离", "高频完全重复自动探测", "不剥离会让体验结论失真"),
        ("痛点聚类", "词典召回 + 否定剔除 + 人工复核", "纯自动分类必然有误差"),
        ("趋势分析", "绝对量与占比并排", "占比会被分母稀释"),
        ("结论纪律", "先验证再下结论", "每个结论写明推翻条件"),
    ]:
        html.append(f'<tr><td><b>{_esc(a)}</b></td><td>{_esc(b)}</td><td>{_esc(c)}</td></tr>')
    html.append('</table></div>')

    html.append(f'''<div class="foot">
<b>数据来源</b>：米游社公开评论接口 · 区域 {html_escape_region(meta)}<br>
<b>方法论</b>：见项目 methodology/ 目录（六份文档）<br>
<b>免责</b>：本看板为技术研究用途，不隶属于也不代表上海米哈游影铁科技有限公司。
评论内容版权归原作者所有，引用数据仅作分析演示。<br>
<b>口径说明</b>：所有结论基于剥离系统模板后的真实评论（{ts["real_count"]} 条）；
表观口径（{ap["total"]} 条）仅用于与官方好评率对齐校验。
</div>''')

    html.append("</div></body></html>")

    out = "".join(html)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    return out_path


def html_escape_region(meta):
    import html as _h
    return _h.escape(str((meta or {}).get("region", "cn_gf01")))


def verify_dashboard(path):
    """自包含检查"""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    issues = []
    if "<script src=" in content:
        issues.append("含外部 JS 引用")
    if '<link rel="stylesheet"' in content:
        issues.append("含外部 CSS 引用")
    if "http://" in content or "https://" in content:
        # 允许注释/纯文本里的链接，但要检查有没有资源引用
        for bad in ['src="http', 'href="http']:
            if bad in content:
                issues.append(f"含外部资源引用 {bad}")
    if "�" in content:
        issues.append("含乱码字符")
    if len(content) < 5000:
        issues.append("内容过短，疑似生成失败")
    return issues
