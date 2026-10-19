# 方法论 · 01 数据完整性校验

> 在做任何分析**之前**跑完这一步。残缺数据比没有数据更危险 —— 它看起来很完整。

## 为什么最危险

抓取脚本出问题时，**往往不会报错**，只会安静地少返回一部分。你以为在分析 1532 条，实际只有 178 条，而报告看起来一切正常。

真实案例：某次 `FLOOR_DESC` 首页瞬时失败，整趟只贡献 0 条，`reachable=178/1532`。而 `HOT` 趟仍有数据，所以 `complete=false` 之外没有任何显眼信号 —— 差一点就把残缺当全量交付了。

## 七项校验

```python
c = crawl(level_id, region)

# 1. 条数与接口声明一致
assert len(c.rows) == c.total

# 2. 回复 ID 唯一（真正的合并主键）
reply_ids = {r["reply_id"] for r in c.rows}
assert len(reply_ids) == len(c.rows)

# 3. 楼层跨度接近条数（跨度过大 = 漏页；缺失楼层不参与 max/min）
floors = [r["floor_id"] for r in c.rows if r["floor_id"] is not None]
assert floors, "没有任何楼层号"
span = max(floors) - min(floors)
assert span < len(c.rows) * 1.1

# 4. 各趟都正常（见坑 1c：整趟夭折）
assert not c.degraded

# 5. 每一趟都有产出
for p in c.passes:
    assert p.pages > 0 and not p.error

# 6. 楼层号完整（缺失会削弱合并去重能力）
assert c.missing_floor == 0

# 7. 好评率与官方一致（口径对齐的强证据）
rec = sum(1 for r in c.rows if r["is_recommend"])
assert abs(rec/len(c.rows)*100 - float(meta["goodRate"].rstrip("%"))) < 0.5
```

代码里直接调用 `ok, checks = c.integrity()` 即可，返回的 `checks` 就是这七项。

> **中间产物一致性**（`raw.json` → `analysis.json`）不在七项里，属于两步跑时的人工核对：
> `sum(d["like"] for d in detail) == sum(to_int(r["reply_stat"]["like_count"]) for r in raw["rows"])`。

## 坑 1b：必须跑两趟排序

单一排序会漏数据，且**漏的是互补的部分**。

实测（`total=1280`）：

| 排序 | 单独跑的结果 |
|---|---|
| `SORT_TYPE_FLOOR_DESC` | 1157 / 1280（漏 123） |
| `SORT_TYPE_HOT` | 143 / 1280 |
| **两者合并** | **1280 / 1280 ✅** |
| `SORT_TYPE_FLOOR_ASC` | 被服务端降级成 DESC，无额外收益 |
| `TIME` / `NEW` / `OLD` | `retcode -502`，不可用 |

**只跑单趟会把 90% 当 100%**，这是最难发现的错误。

## 坑 1c：单页失败不能直接 break

接口会偶发瞬时失败。原来的写法是 `catch → break`，一趟直接报废。

```python
# ✅ 每页重试 3 次
for attempt in range(3):
    try:
        d = fetch_page(...)
        break
    except Exception as e:
        if attempt < 2:
            time.sleep(0.8 * (attempt + 1))
```

## 坑 1d：合并时取 max，不要覆盖

```python
# ❌ 后到覆盖先到：两趟数据不一致时会随机丢数据
uniq[reply_id] = r

# ✅ 点赞数取两趟最大，正文保留首见
uniq[reply_id].like = max(uniq[reply_id].like, r.like)
```

实测修复前 71 条评论点赞在两次抓取间跳变、37 条直接归零。

## 坑 1e：has_more 会提前变 false

接近末尾时先给 `has_more=false`，但游标随后**往回扫**已看过的区间（1256 → 1232 → 1208…）。

**不要在第一次 `false` 就停** —— 多跑 2 页确认游标成环（`tailConfirm=2`）。

## 检查清单

- [ ] 七项校验全过
- [ ] 回复 ID 唯一、楼层号完整
- [ ] `passes` 中每趟 `pages > 0`
- [ ] `degraded == false`
- [ ] 好评率与官方对齐（±0.5pt）
- [ ] 楼层跨度 < 条数 × 1.1
- [ ] 中间产物逐条一致

任何一项不过，**先排查翻页，不要交付**。
