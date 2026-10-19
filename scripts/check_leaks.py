#!/usr/bin/env python3
"""
脱敏泄漏检查 —— 提交前的强制关卡

检查可入库文件中是否残留真实用户信息：
  · 真实 UID（9-11 位数字）
  · 真实昵称
  · 真实 IP 属地（仅在数据上下文中判定，避免误报）

用法：
  python scripts/check_leaks.py [raw_reference.json]

退出码：0 = 通过，1 = 发现泄漏
"""

import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# 排除目录 —— 数据产出目录不在版本控制内，无需检查
EXCLUDE_DIRS = {".git", "out", "out_test", "out_full", "out_raw_test", "out2",
                 "data", "__pycache__", ".venv", "node_modules"}
# 目录名以 out_ / out 开头的都视为产出目录
EXCLUDE_PREFIX = ("out_", "out")
INCLUDE_EXT = (".json", ".md", ".html", ".py", ".txt", ".csv")

# 地域名 —— 只在"数据上下文"里算泄漏（出现 IP 属地相关关键词时）
REGIONS = {
    "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "广西", "海南", "四川", "贵州", "云南", "西藏", "陕西", "甘肃",
    "青海", "宁夏", "新疆", "内蒙古", "中国香港", "中国台湾", "中国澳门",
    "香港", "台湾", "澳门",
}
REGION_CONTEXT = re.compile(r"(IP属地|ip_location|ipLocation|\"ip\"|属地)")


def load_reference(path):
    """载入真实数据作为比对基准"""
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)
    rows = blob["rows"] if isinstance(blob, dict) and "rows" in blob else blob
    uids, names = set(), set()
    for r in rows:
        u = str(r.get("uid") or r.get("user_id") or "")
        if u:
            uids.add(u)
        n = r.get("user") or r.get("username") or ""
        # ★ 阈值 2 —— 2 字昵称（"张三"）也是真实身份，不能漏。
        #   之前收集阈值 >=3、检查阈值 <4，两头都把 2 字昵称漏掉了。
        if n and len(n) >= 2:
            names.add(n)
    return uids, names


def scan_files():
    """
    扫描「会被提交的文件」。

    用 git ls-files 取已跟踪文件 + 未跟踪但未被 .gitignore 排除的文件。
    这样既不会漏掉将要提交的新文件，也不会误报被 gitignore 的本地产出。
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=false", "ls-files",
             "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True)
        git_files = [f for f in out.stdout.split("\n") if f.strip()]
        if git_files:
            return [os.path.join(ROOT, f) for f in git_files]
    except Exception:
        pass

    # 非 git 环境：退化为全目录扫描
    found = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs
                   if d not in EXCLUDE_DIRS and not d.startswith(EXCLUDE_PREFIX)]
        for fn in files:
            if fn.endswith(INCLUDE_EXT):
                found.append(os.path.join(root, fn))
    return found


def main():
    ref_path = sys.argv[1] if len(sys.argv) > 1 else None
    uids, names = set(), set()
    if ref_path and os.path.exists(ref_path):
        uids, names = load_reference(ref_path)
        print(f"基准数据: {len(uids)} 个 UID, {len(names)} 个昵称")
    else:
        print("[WARN] 未提供基准数据，仅做通用模式检查")

    leaks = []
    checked = 0

    for path in scan_files():
        rel = os.path.relpath(path, ROOT)
        # 测试目录里的地域/昵称是构造的假数据，不是泄漏
        if rel.startswith("tests" + os.sep):
            continue
        fn = os.path.basename(path)
        try:
            content = open(path, encoding="utf-8").read()
        except Exception:
            continue
        checked += 1

        # 1. 真实 UID
        for uid in uids:
            if uid in content:
                leaks.append((rel, "UID", uid))
                break

        # 2. 真实昵称（长度 >= 4，避免"123""emmm"这类噪声误报）
        #    只在"昵称字段"语境下判定，避免命中评论正文
        for nm in names:
            # 只跳过 1 个字符的昵称（几乎必然是噪声）。
            # 检查是按字段位置做的（"user": "X" / Excel 用户列），
            # 出现在这些位置就说明它确实是昵称字段，不会像全文搜子串那样误报。
            if len(nm) < 2:
                continue
            if nm in content:
                # 出现在 user/nickname 字段值里才算泄漏
                if re.search(rf'"(user|username|nickname)"\s*:\s*"{re.escape(nm)}"', content):
                    leaks.append((rel, "昵称", nm))
                    break
                # 或出现在"用户X"式的表格单元格里
                if f"| {nm} |" in content:
                    leaks.append((rel, "昵称", nm))
                    break

        # 3. IP 属地 —— ★ 只判「行级 IP」，不判聚合统计
        #
        #    行级 IP（这条评论来自广东）= 个人身份 → 泄漏
        #    聚合统计（广东 232 条）    = 统计结果，不指向个人 → 合法
        #
        #    之前用"文件里出现地域名 + IP 上下文"来判，把合法的聚合分布
        #    也当成泄漏了 —— 会误报的检查等于没有检查。
        for m in re.finditer(r'"ip"\s*:\s*"([^"]{1,16})"', content):
            val = m.group(1)
            if val in REGIONS:
                leaks.append((rel, "IP属地(行级)", val))
                break

        # 3b. Excel 里的行级 IP 属地（明细表的 IP属地 列）
        #     地域分布表是聚合统计 → 合法，不查
        if fn.endswith(".xlsx"):
            try:
                from openpyxl import load_workbook
                wb = load_workbook(path, read_only=True)
                for ws in wb.worksheets:
                    if "地域" in (ws.title or ""):
                        continue          # 聚合分布表，合法
                    rows_iter = ws.iter_rows(values_only=True)
                    header = next(rows_iter, None)
                    if not header:
                        continue
                    idx = [i for i, h in enumerate(header or [])
                           if h and str(h).strip() == "IP属地"]
                    if not idx:
                        continue
                    for row in rows_iter:
                        for i in idx:
                            if i < len(row) and row[i] in REGIONS:
                                leaks.append((rel, "IP属地(行级)", row[i]))
                                break
            except Exception:
                pass

        # 4. 通用模式：裸的 9-11 位数字（可能是 uid）
        #    排除哈希前缀（u_xxxxxxxxxx）与常见年份/ID 前缀
        for m in re.finditer(r"(?<![0-9a-zA-Z_])([0-9]{9,11})(?![0-9a-zA-Z])", content):
            v = m.group(1)
            if v.startswith(("2026", "2025", "2024", "2023", "1567", "5010")):
                continue
            leaks.append((rel, "疑似UID(9-11位)", v))
            break

    print(f"检查 {checked} 个文件\n")
    if leaks:
        print("[FAIL] 发现潜在泄漏：")
        seen = set()
        for rel, kind, val in leaks:
            key = (rel, kind, val)
            if key in seen:
                continue
            seen.add(key)
            print(f"   [{kind}] '{val}'  →  {rel}")
        print("\n处理：把该文件加入 .gitignore，或重新脱敏。")
        print("[WARN] 注意 —— Git 历史不可撤回，误提交后需用 git filter-repo 重写。")
        return 1

    print("[OK] 未发现真实用户信息泄漏")
    return 0


if __name__ == "__main__":
    sys.exit(main())
