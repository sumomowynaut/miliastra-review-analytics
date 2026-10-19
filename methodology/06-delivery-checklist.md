# 方法论 · 06 交付物检查

> 数据交付 ≠ 文件生成。**文件生成了不等于能交付。**

## 坑：交付前才发现文件坏了

出现过两次：
- Excel 生成后中文表头变成乱码（编码问题）
- 看板 HTML 里引用的数据路径写死，拿到别的机器打不开

**规则：交付前必须"实际打开验证"，不能只看文件存在。**

## Excel 检查清单

- [ ] 能正常打开（用 openpyxl 重新 load 一次）
- [ ] 中文无乱码（表头抽检）
- [ ] 工作表数量与预期一致
- [ ] 行数与数据条数一致
- [ ] 关键指标与源数据交叉核对
- [ ] 列宽自适应、不截断
- [ ] 日期是真正的 date 类型而非字符串
- [ ] 百分比格式正确（0.79 不是 79.00%）

```python
from openpyxl import load_workbook
wb = load_workbook(path)
assert len(wb.sheetnames) == 7
for ws in wb.worksheets:
    assert ws.max_row > 1
```

## HTML 看板检查清单

- [ ] **单文件自包含**（无外部 CSS/JS/字体依赖）
- [ ] 断网能打开
- [ ] 双击能开（file:// 协议）
- [ ] 无 console error
- [ ] 移动端/窄屏不塌
- [ ] 深色背景对比度足够
- [ ] 每个图表都标了数据来源和生成时间

```python
# 自包含检查：不应有外部引用
assert "http://" not in html or "<script src" not in html
assert "<link rel=\"stylesheet\"" not in html
```

## 脱敏检查（如果对外发布）

- [ ] 无真实 UID
- [ ] 无真实昵称
- [ ] 无 IP 属地
- [ ] 无原始评论全文
- [ ] `.gitignore` 排除了数据产出目录

**注意：Git 历史不可撤回。** 一旦 commit 过，删文件也没用 —— 必须用 `filter-repo` 重写历史。

```bash
git filter-repo --path data/ --invert-paths
```

## 报告自检

- [ ] 显式说明了模板语已剥离
- [ ] 双口径都给了（表观 + 真实）
- [ ] 绝对量与占比并排
- [ ] 样本不足的分组已标注
- [ ] 官方事实 / 玩家陈述 / 推断分开
- [ ] 被推翻的结论记录在案
- [ ] 写清了数据来源、时间范围、局限
