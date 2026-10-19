# NOTICE · 第三方组件与归属说明

本项目为原创实现，但使用了第三方开源项目与公开接口。逐项说明如下。

---

## 第三方开源项目

### Octavia

- **项目**：[Octavia — 千星奇域社区数据服务](https://github.com/kj415j45/octavia)
- **作者**：kj415j45
- **协议**：MIT License
- **版权声明**：

  ```
  Copyright (c) Octavia contributors
  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:
  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.
  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
  ```

- **使用方式**：本项目在「搜索发现」与「补充元数据」环节参考了 Octavia 的接口思路
  与其 MIT 协议下的源码。**未直接打包其代码**；`src/crawler.py` 为独立实现。
- **衍生说明**：`src/` 中关于 GUID 编解码的三项工具函数
  （`guidToUid` / `uidToRegion` / `guidToRegion`）系依据该 MIT 项目源码推导得出，
  并在本项目内独立实现与验证（220/220 样本零误差）。

---

## 本项目原创部分

以下内容为本项目原创，不来自任何第三方：

| 模块 | 说明 |
|---|---|
| `methodology/` 全部六份文档 | 可靠性规则与调查过程的沉淀 |
| `src/strip_templates.py` | 模板语自动探测与剥离机制（含启发式验证） |
| `src/domain_lexicon.py` | 领域词典扩展机制（保持通用性与准确性的权衡方案） |
| `src/schema.py` | 多数据源字段映射层 |
| `src/anonymize.py` | 脱敏流水线（稳定哈希 + 昵称伪名化） |
| `src/excel_report.py` | 7 表 Excel 报告生成 + 交付前自动校验 |
| `src/dashboard.py` | 纯 SVG 自包含看板 + 自包含性校验 |
| `src/crawler.py` | 双趟合并 / 重试 / 完整性六项校验的抓取器 |
| `scripts/check_leaks.py` | 提交前的脱敏泄漏检查关卡 |
| `examples/` | 完整案例的发现与结论 |

---

## 数据来源与归属

- 评论数据来自 **米游社公开接口**（`bbs-api.miyoushe.com`），仅获取公开可见内容。
- 评论正文版权归**原作者**所有。
- 奇域相关美术、文本与商标版权归 **上海米哈游影铁科技有限公司** 所有。
- 本仓库中的一切数据产出均已脱敏，不包含任何真实用户 UID、昵称或 IP 属地。

---

## 免责声明

本项目为**技术研究与学习用途**，不隶属于、不代表、也不获得
上海米哈游影铁科技有限公司 / miHoYo / HoYoverse 的认可或背书。

使用者需自行确保其使用行为符合[米游社用户协议](https://www.miyoushe.com/agreement)
及所在地法律法规。项目作者不对因使用本项目产生的任何后果负责。

---

## 本项目协议

MIT License，详见 [LICENSE](LICENSE)。
