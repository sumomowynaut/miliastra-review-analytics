#!/usr/bin/env python3
"""
生成 GitHub 推送载荷 —— 把本地 git 仓库的文件转成 push_files 的 JSON

设计要点：
  · 二进制文件（xlsx 等）无法安全传输，单独排除并在报告中列出
  · 所有文件以 UTF-8 文本读取
  · 直接传 UTF-8 文本，避免调用方自行解码出错

用法：
  python scripts/make_push_payload.py            # 输出统计
  python scripts/make_push_payload.py --write    # 写出 payload.json
"""

import json
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BINARY_EXT = {".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".gif", ".pdf",
              ".zip", ".ico", ".woff", ".woff2", ".ttf"}


def git_files():
    """从 git 索引取文件列表（不含 .gitignore 的）"""
    # core.quotepath=false —— 否则中文路径会被转义成 \xxx 形式
    out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    return [f for f in out.stdout.split("\n") if f.strip()]


def build():
    files, skipped = [], []
    for rel in git_files():
        abs_path = os.path.join(ROOT, rel)
        ext = os.path.splitext(rel)[1].lower()
        if ext in BINARY_EXT or not os.path.exists(abs_path):
            skipped.append(rel)
            continue
        with open(abs_path, encoding="utf-8") as f:
            content = f.read()
        files.append({"path": rel, "content": content})
    return files, skipped


def main():
    files, skipped = build()
    total = sum(len(f["content"].encode("utf-8")) for f in files)

    print(f"可推送文件: {len(files)} 个，{total/1024:.0f}KB")
    for f in sorted(files, key=lambda x: -len(x["content"]))[:6]:
        print(f"   {len(f['content'])/1024:7.1f}KB  {f['path']}")

    if skipped:
        print(f"\n跳过（二进制/不存在）: {len(skipped)} 个")
        for s in skipped:
            print(f"   {s}")

    # 泄漏自检：确认没有任何真实用户数据
    print("\n--- 载荷自检 ---")
    blob = json.dumps(files, ensure_ascii=False)
    uids = set(re.findall(r"(?<![0-9a-zA-Z_])([0-9]{9,11})(?![0-9a-zA-Z])", blob))
    uids = {u for u in uids if not u.startswith(("2026", "2025", "2024", "1567", "5010"))}
    print(f"载荷中疑似裸 UID: {len(uids)} 个 {list(uids)[:5]}")

    if "--write" in sys.argv:
        out = os.path.join(ROOT, "push_payload.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"files": files, "skipped": skipped}, f, ensure_ascii=False)
        print(f"\n已写出 {out}（该文件需加入 .gitignore）")


if __name__ == "__main__":
    main()
