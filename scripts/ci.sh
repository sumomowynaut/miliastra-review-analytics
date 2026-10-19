#!/usr/bin/env bash
# 本地 CI —— 与 .github/workflows/ci.yml 逻辑一致
#
# GitHub Actions 的 workflow 文件位于 .github/workflows/，
# 部分推送通道出于安全策略不允许写入该目录。
# 因此这里提供等价的本地执行入口：
#   bash scripts/ci.sh
#
# 全部检查均为零网络请求，可在离线环境运行。

set -uo pipefail
cd "$(dirname "$0")/.."

FAIL=0
step() { echo ""; echo "── $1 ──"; }
ok()   { echo "✅ $1"; }
bad()  { echo "❌ $1"; FAIL=1; }

step "1/6 单元测试"
if python3 -m unittest discover tests/ 2>&1 | tail -3; then
  ok "单元测试通过"
else
  bad "单元测试失败"
fi

step "2/6 端到端（脱敏样例，零网络请求）"
if python3 src/analyze.py \
      --fixture fixtures/sample_chess_30.json \
      --outdir out \
      --domain-lexicon templates/domain_example.json; then
  ok "端到端流程通过"
else
  bad "端到端流程失败"
fi

step "3/6 产出文件校验"
EXPECT_XLSX="out/50105539813/千星奇域50105539813_数据报告.xlsx"
EXPECT_HTML="out/50105539813/千星奇域50105539813_看板.html"
[ -f "$EXPECT_XLSX" ] && ok "Excel 已生成" || bad "Excel 缺失"
[ -f "$EXPECT_HTML" ] && ok "看板已生成" || bad "看板缺失"

step "4/6 看板自包含检查"
python3 - <<'PY' && ok "看板自包含" || bad "看板存在外部依赖"
import sys, glob
sys.path.insert(0, "src")
from dashboard import verify_dashboard
fails = 0
for p in glob.glob("out/**/*.html", recursive=True):
    issues = verify_dashboard(p)
    print(("✅" if not issues else "❌"), p, issues or "")
    fails += len(issues)
sys.exit(1 if fails else 0)
PY

step "5/6 Excel 校验"
python3 - <<'PY' && ok "Excel 校验通过" || bad "Excel 校验失败"
import sys, glob
sys.path.insert(0, "src")
from excel_report import verify_excel
fails = 0
for p in glob.glob("out/**/*.xlsx", recursive=True):
    issues = verify_excel(p)
    print(("✅" if not issues else "❌"), p, issues or "")
    fails += len(issues)
sys.exit(1 if fails else 0)
PY

step "6/6 脱敏泄漏检查"
if python3 scripts/check_leaks.py; then
  ok "未发现真实用户信息泄漏"
else
  bad "检测到潜在泄漏 —— 请勿提交"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "🎉 全部检查通过"
else
  echo "⚠️  有检查未通过"
fi
exit $FAIL
