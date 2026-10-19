"""
脱敏工具 —— 公开分享前的强制步骤

处理对象：
  · uid      → 稳定哈希（可复现，不可反推）
  · user     → 替换为「用户A」「用户B」…
  · ip       → 移除或粗粒度化到省级
  · content  → 视需要保留（见 mask_content）

为什么：即使数据来自公开接口，把 1532 个真实 UID + 昵称 + IP 属地
聚合成可下载文件并发布，属于个人信息处理。Git 历史不可撤回。
"""

import hashlib
import re


def _h(uid, salt="miliastra-analytics"):
    """稳定哈希 —— 同一 uid 始终得到同一假 id，可做一致性分析但不可反推"""
    return "u_" + hashlib.sha256(f"{salt}:{uid}".encode()).hexdigest()[:10]


def mask_users(details, keep_ip=False, keep_content=True, salt="miliastra-analytics"):
    """
    脱敏评论列表（原地修改并返回）。

    keep_ip     : 是否保留 IP 属地（建议 False）
    keep_content: 是否保留评论正文（建议 True —— 分析结论依赖原话）
    """
    uid_map = {}
    for d in details:
        uid = str(d.get("uid", ""))
        if uid and uid not in uid_map:
            uid_map[uid] = _h(uid, salt)
        d["uid"] = uid_map.get(uid, "")
        d["user"] = ""
        if not keep_ip:
            d["ip"] = ""
        if not keep_content:
            d["content"] = ""
    return details, uid_map


def pseudonymize(details):
    """把昵称替换成稳定的「用户A/B/C」，保留排名可读性"""
    seen = {}
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for d in details:
        name = d.get("user") or ""
        if name not in seen:
            idx = len(seen)
            prefix, rest = divmod(idx, 26)
            seen[name] = f"用户{letters[prefix]}{letters[rest] if prefix == 0 else ''}" \
                if prefix == 0 else f"用户{prefix}{letters[rest]}"
        d["user"] = seen[name]
    return details, seen


def mask_text(text, keep_ratio=0.3):
    """
    轻度打码评论正文：保留部分实义字符，便于展示方法论但降低可识别性。
    生产环境建议 keep_ratio=1.0（保留原话）配合 uid/昵称/IP 脱敏。
    """
    if keep_ratio >= 1.0:
        return text
    if not text:
        return text
    n = max(1, int(len(text) * keep_ratio))
    return text[:n] + "…" * max(1, (len(text) - n) // 2)


def sample_for_fixture(details, n=30, seed=42, salt="miliastra-analytics"):
    """
    抽取一个可公开的示例集。
    保留时间分布的多样性 —— 按月分层抽样，而不是纯随机。
    """
    import random
    rng = random.Random(seed)

    by_month = {}
    for d in details:
        month = (d.get("date") or "")[:7]
        by_month.setdefault(month, []).append(d)

    months = sorted(by_month)
    picked = []
    per_month = max(1, n // max(1, len(months)))

    for m in months:
        pool = by_month[m]
        picked.extend(rng.sample(pool, min(per_month, len(pool))))

    # 补齐
    remaining = [d for d in details if d not in picked]
    while len(picked) < n and remaining:
        picked.append(rng.choice(remaining))
        remaining = [d for d in remaining if d not in picked]

    picked = picked[:n]
    # 深拷贝，避免污染原数据
    import copy
    out = copy.deepcopy(picked)
    mask_users(out, keep_ip=False, keep_content=True, salt=salt)
    return out


def scrub_text_for_git(text, patterns=None):
    """从任意文本（README/HTML）中移除疑似真实 ID"""
    patterns = patterns or [
        (r'\b\d{9,11}\b', '********'),          # 9-11 位数字（uid）
        (r'(?i)(uid["\s:=]+)\d+', r'\1********'),
    ]
    for pat, rep in patterns:
        text = re.sub(pat, rep, text)
    return text
