# 千星奇域评论分析工具

> 输入一个奇域 ID，自动产出可信的玩家体验分析报告（Excel + 自包含 HTML 看板）。

[![CI](https://github.com/sumomowynaut/miliastra-review-analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/sumomowynaut/miliastra-review-analytics/actions/workflows/ci.yml)

**核心观点**：UGC 评论区的原始数据不能直接用于下结论。
本项目将实际遇到的问题、验证过程与判断规则整理为六份方法论文档，代码是这些规则的实现。

---

## 项目背景

先看一个真实案例（奇域 `50105539813`，中国象棋纯 UI，1532 条评论）：

| 指标 | 表观（含系统模板） | 真实（已剥离） | 差异 |
|---|---|---|---|
| 好评率 | 79.2% | **69.3%** | −9.9pt |
| 差评率 | 20.8% | **30.7%** | +9.9pt |
| 近 30 天差评率 | 27.4% | **42.0%** | **+14.6pt** |

**829 条（54.1%）是系统自动附带的默认文案**，玩家未输入任何文字。
这些模板把表观差评率从真实的 30.7% 拉低到 20.8%。

更关键的是，官方公布的 79.2% 采用的是含模板口径。它与本地计算结果一致，可用于校验数据完整性，但**不能用于判断真实体验**。

剥离模板后进行痛点聚类，主要问题随之显现（以下为玩家原话）：

```
规则与玩法 · 98 条 (45.4%)
  · "能不能加一个长将犯规啊，本来我必赢的，但是对面一直将"
  · "点进去之后一脸懵逼，怎么开始游戏啊开始按钮都没有"
```

这说明问题不在操作手感，而在**规则设计**。通用词典难以得到这一结论，需要领域词典与人工复核共同完成。

---

## 快速开始

### 使用样例数据运行（无需网络）

```bash
git clone https://github.com/sumomowynaut/miliastra-review-analytics.git
cd miliastra-review-analytics
pip install -r requirements.txt

python src/analyze.py --fixture fixtures/sample_chess_30.json --outdir out
```

产出 `out/50105539813/` 下的 Excel 报告与 HTML 看板。

### 抓真实数据

```bash
python src/crawl.py <LEVEL_ID> --out data/raw.json
python src/analyze.py --raw data/raw.json --outdir out
```

产出的 Excel 与看板**默认移除 UID / 昵称 / 行级 IP**；
评论正文仍为玩家原话，对外分享前请按场景评估。

> ⚠️ 请求数随评论量增长（1532 条的案例约 90 次），**请勿并发或调高频率**。
> 详见下方「使用约束」。

### 扩展领域词典

通用词典通常会漏掉领域词汇（实测覆盖率仅 31.5%）。可复制示例并按需补充：

```bash
cp templates/domain_example.json my_domain.json
# 编辑 my_domain.json，加入领域关键词
python src/analyze.py --level-id <LEVEL_ID> --domain-lexicon my_domain.json
```

象棋案例加入领域词典后，覆盖率 **31.5% → 75.5%**。

> 不传 `--domain-lexicon` 时只使用通用词典，**不会**自动加载象棋示例；
> 指定的文件不存在会直接报错，避免误用不匹配的词典。

---

## 产出

| 文件 | 说明 |
|---|---|
| `千星奇域<ID>_数据报告.xlsx` | 7 个工作表：概览、模板剥离分析、评论明细、月度趋势、痛点聚类、地域分布、方法论与日志 |
| `千星奇域<ID>_看板.html` | 单文件自包含看板，断网可用，支持通过 file:// 直接打开 |
| `analysis.json` | 结构化分析结果，便于二次处理 |
| `raw_public.json` | 脱敏后的完整数据（**默认生成**） |
| `raw.json` | 原始数据（`crawl.py` 产出，已加入 `.gitignore`） |

> **产出默认脱敏。** Excel、看板、analysis.json 里的 UID / 昵称 / 行级 IP
> 会被哈希或移除，并按 [06 交付物检查](methodology/06-delivery-checklist.md) 做自检。
> 评论正文仍是玩家原话，对外分享前请确认场景。
> 确需保留真实身份（本地排查、回复某个用户）时加 `--keep-identities`，
> 此时终端会给出警告，且产出**不可直接对外分享**。

看板包含：双口径 KPI、模板剥离前后对比、绝对量与占比双轴趋势图、痛点聚类、高赞评论、地域分布。

---

## 方法论

完整文档在 [`methodology/`](methodology/)，这是本项目的核心文档：

| 文档 | 解决的问题 |
|---|---|
| [01 数据完整性校验](methodology/01-data-integrity.md) | 单趟排序会漏约 10% 且不报错；单页失败会导致整趟中断；合并时可能丢数据 |
| [02 模板语剥离](methodology/02-template-stripping.md) | **54% 的评论是系统模板**，不剥离会使评价类结论出现系统性偏差 |
| [03 痛点聚类](methodology/03-topic-clustering.md) | 词典召回 + 否定剔除 + 领域词典扩展 + 人工复核 |
| [04 趋势陷阱](methodology/04-trend-pitfalls.md) | 占比被分母稀释；样本量不足；跨版本对比 |
| [05 先验证再下结论](methodology/05-verify-before-concluding.md) | 结论必须可推翻；三类陈述分开标注 |
| [06 交付物检查](methodology/06-delivery-checklist.md) | 文件生成不等于可交付；脱敏检查 |

### 六条可靠性规则（速览）

1. **双趟排序合并** —— `FLOOR_DESC` 与 `HOT` 各执行一趟。单趟会漏约 10%，且**不会报错**。
2. **每页重试 3 次** —— 单页失败不能直接中断循环，否则整趟抓取会失败。
3. **合并取最大值** —— 两趟数据不一致时取最大值，避免覆盖（实测修复了 37 条点赞数归零的问题）。
4. **自动探测模板** —— 不硬编码平台文案，识别高频完全重复文本，并验证剥离后的重复率。
5. **绝对量与占比并排** —— 占比会被分母稀释，差评占比下降 12pt 也可能只是评论总量减少。
6. **先验证再下结论** —— 每个结论写明推翻条件；官方事实 / 玩家陈述 / 推断分开标注。

---

## 项目结构

```
├── src/
│   ├── crawl.py            # 抓取命令行入口
│   ├── crawler.py          # 公开接口封装（重试/双趟合并/完整性校验）
│   ├── analyze.py          # 主流程：模板剥离 → 痛点聚类 → 趋势 → 产出
│   ├── strip_templates.py  # ★ 模板自动探测与剥离
│   ├── domain_lexicon.py   # 领域词典扩展机制
│   ├── schema.py           # 字段映射层（多数据源统一）
│   ├── anonymize.py        # 脱敏（哈希 UID / 昵称替换）
│   ├── excel_report.py     # Excel 生成 + 交付前校验
│   └── dashboard.py        # 自包含 HTML 看板 + 自包含校验
├── methodology/            # ★ 六份方法论文档（核心资产）
├── templates/              # 领域词典模板
├── fixtures/               # 脱敏样例数据
├── examples/               # 案例与 200 条公开样本（已脱敏）
├── scripts/
│   ├── build_example.py    # 生成可公开的案例
│   └── check_leaks.py      # ★ 提交前脱敏泄漏检查（CI 关卡）
├── tests/                  # 63 项单元测试（Excel 用例需 openpyxl）
├── .github/workflows/      # CI：测试 → 端到端 → 产出校验 → 泄漏检查
└── out/                    # 产出（已加入 .gitignore）
```

## 开发

```bash
# 单元测试（先安装 requirements.txt）
python -m unittest discover tests/ -v

# 端到端（零网络请求）
python src/analyze.py --fixture fixtures/sample_chess_30.json --outdir out

# 提交前脱敏检查（CI 会自动执行）
python scripts/check_leaks.py data/raw.json
```

CI 在每次 push 时自动执行：单元测试 → 端到端 → 产出文件校验 →
看板自包含检查 → 脱敏泄漏检查 → 确认数据产出未被提交。

本地执行同一套检查：

```bash
bash scripts/ci.sh
```

---

## 公开接口契约

抓取器使用米游社公开接口，接口契约如下（2026-10-19 实测确认）：

```
POST https://bbs-api.miyoushe.com/community/ugc_community/web/api/reply/list?lang=zh-cn
Content-Type: application/json;charset=UTF-8
必要头：X-Rpc-Language / Origin / Referer / x-rpc-client_type / x-rpc-device_id

body  {"uid":"", "region":"cn_gf01", "level_id":"<ID>",
        "cursor":{"next":"", "size":20, "sort_type":"SORT_TYPE_FLOOR_DESC"}}

响应  data.reply_list[] · data.total · data.cursor{next, has_more, sort_type}
```

响应行中，**`uid` 与昵称位于 `user_info` 内**（不在顶层），`created_at` 是 Unix 秒，
`client_ip` 是省级，`reply_stat.like_count` 是字符串。

元信息（热度 / 官方好评率 / 关卡名）走另一个接口
`/community/ugc_community/web/api/level/full/info`，请求体的键是 **`agg_req_list`**
（不是 `api_name_list`，键名错误会返回空 `resp_map`）。

> ⚠️ 接口契约变化时需要修改代码，但**方法论文档仍然适用**：
> 它们讨论的是如何判断数据是否可信，与具体接口无关。

## 使用约束与免责

- 本项目通过**米游社公开接口**获取公开评论数据，仅供技术研究与学习。
- 请遵守[米游社用户协议](https://www.miyoushe.com/agreement)与相关服务条款。
- **不要并发运行、不要提高请求频率。** 默认 0.35s 间隔是实测可用的值。
- 评论正文版权归原作者所有；奇域相关美术、文本与商标归上海米哈游影铁科技有限公司所有。
  本项目不隶属于也不代表 miHoYo。
- 仓库中**不含任何真实用户 UID、昵称、IP 属地**。所有数据产出目录均已加入 `.gitignore`。
  Git 历史不可撤回 —— 一旦误提交真实数据，需要用 `git filter-repo` 重写历史。
- 案例数据**仅对身份信息脱敏**（UID 哈希、昵称替换、IP 移除）；
  **评论正文为玩家原话**，未做改写。原文版权归原作者所有。
- UID 哈希使用的是源码内固定 salt，属于**去标识化**而非强匿名化；
  低熵数字 UID 仍可能被枚举反查，对外发布前请评估是否需要随机 salt。

### 第三方组件

公开接口契约的整理参考了 [Octavia](https://github.com/kj415j45/octavia)（MIT License），
仓库内不包含其源码。
本项目原创部分：评论全量抓取方法、模板剥离方法论、痛点聚类框架、
领域词典机制、可视化方案、字段映射层与交付校验。

---

## License

[MIT](LICENSE)
