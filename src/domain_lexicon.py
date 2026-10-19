"""
领域词典扩展机制

问题：通用痛点词典必然漏掉领域特有词汇。
      象棋案例的「炮能吃我的马」「老将照脸」这类规则细节，
      塞进通用词典会让项目失去可迁移性。

解法：通用词典保持稳定，领域词通过外部文件按需扩展。
      这是"可复用"与"够准确"之间的正确权衡。

用法：
  1. 复制 templates/domain_example.json
  2. 填入你的领域的关键词
  3. analyze.py --domain-lexicon path/to/your.json
"""

import json
import os

# 随项目提供的示例 —— 象棋/策略类关卡
DOMAIN_EXAMPLE = {
    "_comment": "示例：策略/棋类类关卡。复制此文件并按需修改。",
    "category": "规则与玩法",
    "keywords": [
        # 棋类专属术语
        "将军", "照脸", "将", "帅", "炮", "马", "卒", "象", "车",
        "吃子", "兑", "捉", "步", "回合", "长将", "捉子",
        # 规则细节措辞
        "怎么玩", "怎么开始", "怎么过", "什么玩意", "哪方赢",
    ],
    "ignore": [
        # 纯辱骂/屏蔽词 —— 无产品信息量，单独归类
        "**", "***", "傻", "智障", "滚",
    ],
}


def load_domain_lexicon(path=None):
    """
    加载领域词典。返回 {category: [keywords], ...} 与 ignore 列表。

    ★ 未提供路径时返回空领域词典 —— 不会把随项目附带的象棋示例
      自动套到其他奇域上（否则「将军」「炮」会误伤无关内容）。
      指定了路径但文件不存在时直接报错，避免静默回退到示例。
    """
    if not path:
        return {"categories": {}, "ignore": []}
    if not os.path.exists(path):
        raise FileNotFoundError(f"领域词典不存在: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    categories = {}
    ignore = []
    for item in (data if isinstance(data, list) else [data]):
        if not isinstance(item, dict):
            continue
        cat = item.get("category")
        kws = item.get("keywords", [])
        if cat and kws:
            categories[cat] = list(kws)
        ignore.extend(item.get("ignore", []))

    return {"categories": categories, "ignore": sorted(set(ignore))}


def merge_categories(base, domain):
    """把领域词典合并进通用词典（领域优先，追加关键词）"""
    merged = {k: list(v) for k, v in base.items()}
    for cat, kws in domain.get("categories", {}).items():
        if cat in merged:
            for k in kws:
                if k not in merged[cat]:
                    merged[cat].append(k)
        else:
            merged[cat] = list(kws)
    return merged
