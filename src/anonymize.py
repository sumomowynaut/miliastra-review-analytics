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
    """
    稳定哈希 —— 同一 uid 始终得到同一假 id，可做一致性分析。

    ⚠️ salt 固定在源码里，低熵数字 UID 仍可能被字典/暴力枚举反查；
    这是「去标识化」，不是强匿名化。对外发布前请评估是否需要随机 salt。
    """
    return "u_" + hashlib.sha256(f"{salt}:{uid}".encode()).hexdigest()[:10]


def mask_users(details, keep_ip=False, keep_content=True, salt="miliastra-analytics",
               clear_user=True):
    """
    脱敏评论列表：哈希 UID、清除 IP 属地。

    ★ 不修改传入的原始数据 —— 返回的是副本。
      原地修改会毁掉调用方的原始数据，导致"拿已脱敏的值当真实值去校验"这类
      自我欺骗式的误判（真实身份集合会随之被污染）。

    keep_ip     : 是否保留 IP 属地（建议 False）
    keep_content: 是否保留评论正文（建议 True —— 分析结论依赖原话）
    clear_user  : 是否清空昵称（默认 True，保持"调用即安全"）

    ⚠️ 如果你打算先做伪名化（pseudonymize），必须传 clear_user=False ——
      否则昵称在伪名化之前就被清空了，所有人会被折叠成同一个「用户AA」。
      正常情况下请直接用 anonymize_rows()，它保证顺序正确。
    """
    uid_map = {}
    out = []
    for src in details:
        d = dict(src)                       # 浅拷贝：字段都是标量，足够
        uid = str(d.get("uid", ""))
        if uid and uid not in uid_map:
            uid_map[uid] = _h(uid, salt)
        d["uid"] = uid_map.get(uid, "")
        if clear_user:
            d["user"] = ""
        if not keep_ip:
            d["ip"] = ""
        if not keep_content:
            d["content"] = ""
        out.append(d)
    return out, uid_map


def _label(idx):
    """0 → A, 25 → Z, 26 → AA（Excel 列名式编号）"""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    s = ""
    idx += 1
    while idx:
        idx, rest = divmod(idx - 1, 26)
        s = letters[rest] + s
    return s


def pseudonymize(details):
    """
    把昵称替换成稳定的「用户A/B/C」，保留排名可读性。

    同样不修改传入的原始数据。
    """
    seen = {}
    out = []
    for src in details:
        d = dict(src)
        name = d.get("user") or ""
        if name not in seen:
            seen[name] = f"用户{_label(len(seen))}"
        d["user"] = seen[name]
        out.append(d)
    return out, seen


def mask_text(text, keep_ratio=0.3):
    """
    对评论正文做轻度遮蔽：保留部分实义字符，便于展示方法论，同时降低可识别性。
    生产环境建议 keep_ratio=1.0（保留原话）配合 uid/昵称/IP 脱敏。
    """
    if keep_ratio >= 1.0:
        return text
    if not text:
        return text
    n = max(1, int(len(text) * keep_ratio))
    return text[:n] + "…" * max(1, (len(text) - n) // 2)


def anonymize_rows(details, keep_ip=False, keep_content=True,
                    salt="miliastra-analytics"):
    """
    ★ 推荐入口：一次性完成「昵称伪名化 + UID 哈希 + IP 清除」。

    为什么要有这个函数：两个操作有先后依赖。
      pseudonymize 需要真实昵称才能区分不同用户；
      mask_users 会清空昵称。
    顺序反了 → 所有人被折叠成同一个「用户AA」，等于没脱敏。

    把顺序封装在这里，调用方不会再犯同类错误。
    """
    step1 = pseudonymize(details)[0]                    # 真实昵称 → 用户X
    step2 = mask_users(step1, keep_ip=keep_ip,
                       keep_content=keep_content, salt=salt,
                       clear_user=False)[0]             # uid 哈希 + ip 清除，昵称保留
    return step2, {}


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
    # ★ 用 anonymize_rows() 走完整流程。
    #   只调 mask_users 会把伪名也清掉（clear_user 默认 True）——
    #   那样抽样集就退化成了「全员无名」。
    out = anonymize_rows(out, keep_ip=False, keep_content=True, salt=salt)[0]
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
