"""
单元测试 —— 覆盖六条可靠性规则里能用代码断言的部分

只用标准库 unittest，零额外依赖（clone 下来就能跑）：
    python -m unittest discover tests/ -v
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import schema
import strip_templates
import anonymize
import domain_lexicon
from crawler import CrawlResult, Pass


def mk(n, text, rec=True, date="2026-05-18", like=0, start=0):
    """构造 n 条同文本评论"""
    return [{"content": text, "is_recommend": rec, "date": date,
             "like": like, "uid": str(start + i), "floor_id": start + i + 1,
             "user": f"u{start+i}", "ip": "", "reply_id": str(start + i)}
            for i in range(n)]


# ------------------------------------------------------------ 字段映射

class TestSchema(unittest.TestCase):

    def test_normalizes_alternative_field_names(self):
        """不同数据源字段名不同，必须能统一"""
        rows = [
            {"reply_id": "1", "floor_id": 10, "user_id": 123, "username": "a",
             "text": "hi", "created_at": "2026-05-18 10:00:00",
             "like_count": "1,234", "ip_location": "上海", "rec": 1},
            {"reply_id": "2", "floor_id": 11, "uid": 456, "user": "b",
             "content": "yo", "date": "2026-05-19", "like": 5,
             "ip": "北京", "is_recommend": False},
        ]
        out = schema.normalize_all(rows)
        self.assertEqual(out[0]["content"], "hi")
        self.assertEqual(out[0]["like"], 1234)      # 千分位要能解析
        self.assertIs(out[0]["is_recommend"], True)
        self.assertEqual(out[0]["ip"], "上海")
        self.assertEqual(out[1]["content"], "yo")
        self.assertIs(out[1]["is_recommend"], False)

    def test_validate_detects_problems(self):
        rows = schema.normalize_all([
            {"reply_id": "1", "floor_id": 1, "uid": 1, "user": "a",
             "content": "x", "date": "2026-01-01", "like": 0, "rec": 1},
            {"reply_id": "1", "floor_id": 0, "uid": 2, "user": "b",
             "content": "", "date": "", "like": 0, "rec": 0},
        ])
        issues = schema.validate_schema(rows)
        self.assertTrue(any("不唯一" in i for i in issues), issues)
        self.assertTrue(any("日期" in i for i in issues), issues)

    def test_to_bool_variants(self):
        for v in (1, "1", True, "true"):
            self.assertIs(schema._to_bool(v), True)
        for v in (0, "0", False, "false", None, ""):
            self.assertIs(schema._to_bool(v), False)


# ------------------------------------------------------------ 模板剥离

class TestTemplateStripping(unittest.TestCase):

    def test_detect_templates_finds_repeated_text(self):
        # 注意：真实评论必须文本各不相同，否则会被正确地判为模板
        real = [{"content": f"不同的真实评论内容{i}", "is_recommend": True,
                 "date": "2026-05-18", "like": 0, "uid": str(100 + i),
                 "floor_id": 100 + i, "user": f"u{i}", "ip": "",
                 "reply_id": str(100 + i)} for i in range(20)]
        rows = mk(10, "非常优秀的奇域，推荐大家游玩~") + real
        tpl, stats = strip_templates.classify_templates(rows, verbose=False)
        self.assertEqual(stats["template_count"], 10)
        self.assertEqual(stats["real_count"], 20)
        self.assertAlmostEqual(stats["template_pct"], 33.3, delta=0.2)

    def test_normalize_strips_punctuation_variants(self):
        self.assertEqual(strip_templates.normalize("好~"),
                         strip_templates.normalize("好"))

    def test_short_words_are_not_templates(self):
        """短评（'好玩'）是真评论，不应被剥离"""
        rows = mk(9, "好玩")
        tpl, stats = strip_templates.classify_templates(rows, verbose=False)
        self.assertEqual(stats["template_count"], 0)
        self.assertEqual(stats["real_count"], 9)

    def test_dual_rates_show_the_gap(self):
        """核心断言：模板让差评率被低估"""
        rows = mk(60, "非常优秀的奇域，推荐大家游玩~", rec=True)
        rows += mk(40, "这游戏做得很差劲，完全不行", rec=False)
        strip_templates.classify_templates(rows, verbose=False)
        rates = strip_templates.dual_rates(rows)
        self.assertAlmostEqual(rates["apparent"]["bad_rate"], 40.0, delta=0.1)
        self.assertAlmostEqual(rates["real"]["bad_rate"], 100.0, delta=0.1)
        self.assertAlmostEqual(rates["delta_bad_rate"], 60.0, delta=0.2)

    def test_group_by_month_keeps_absolute_and_pct(self):
        rows = mk(10, "真实评论内容abc", rec=False, date="2026-05-01")
        rows += mk(30, "另一批真实评论xyz", rec=False, date="2026-06-01")
        m = strip_templates.group_by_month(rows, real_only=True)
        by = {r["month"]: r for r in m}
        self.assertEqual(by["2026-05"]["total"], 10)
        self.assertEqual(by["2026-06"]["total"], 30)
        self.assertAlmostEqual(by["2026-05"]["bad_pct"], 100.0, delta=0.1)
        self.assertIn("样本不足", by["2026-05"]["sample_note"])
        self.assertEqual(by["2026-06"]["sample_note"], "")


# ------------------------------------------------------------ 完整性校验

class TestIntegrity(unittest.TestCase):

    def test_detects_missing_rows(self):
        res = CrawlResult(level_id="1", region="cn_gf01", total=100)
        res.rows = [{"floor_id": i, "is_recommend": True} for i in range(50)]
        res.meta = {"goodRate": "80%"}
        res.passes = [Pass(sort_type=2, pages=5)]
        ok, checks = res.integrity()
        self.assertFalse(ok)
        self.assertFalse(checks["count_matches_total"])

    def test_detects_degraded_pass(self):
        res = CrawlResult(level_id="1", region="cn_gf01", total=10)
        res.rows = [{"floor_id": i, "is_recommend": True} for i in range(10)]
        res.meta = {"goodRate": "100%"}
        res.passes = [Pass(sort_type=2, pages=3),
                      Pass(sort_type=4, pages=0, error="boom")]
        ok, _ = res.integrity()
        self.assertTrue(res.degraded)
        self.assertFalse(ok)

    def test_passes_on_clean_data(self):
        res = CrawlResult(level_id="1", region="cn_gf01", total=10)
        res.rows = [{"floor_id": i, "is_recommend": True} for i in range(10)]
        res.meta = {"goodRate": "100%"}
        res.passes = [Pass(sort_type=2, pages=1), Pass(sort_type=4, pages=1)]
        ok, checks = res.integrity()
        self.assertTrue(ok, checks)

    def test_span_detects_gaps(self):
        res = CrawlResult(level_id="1", region="cn_gf01", total=10)
        res.rows = [{"floor_id": f, "is_recommend": True}
                    for f in (1, 2, 900)]        # 中间漏了 890 层
        res.meta = {"goodRate": "100%"}
        res.passes = [Pass(sort_type=2, pages=1)]
        ok, checks = res.integrity()
        self.assertFalse(checks["span_reasonable"])


# ------------------------------------------------------------ 脱敏

class TestAnonymize(unittest.TestCase):

    def test_mask_users_hashes_and_clears(self):
        rows = mk(5, "内容")
        rows[0]["user"] = "真实昵称"
        rows[0]["ip"] = "上海"
        out, _ = anonymize.mask_users(rows, keep_ip=False, keep_content=True)
        self.assertTrue(out[0]["uid"].startswith("u_"))
        self.assertEqual(out[0]["user"], "")
        self.assertEqual(out[0]["ip"], "")
        self.assertEqual(out[0]["content"], "内容")

    def test_mask_is_deterministic(self):
        """同一 uid 两次脱敏结果必须一致"""
        a, _ = anonymize.mask_users([{"uid": "12345", "user": "x", "content": "c"}])
        b, _ = anonymize.mask_users([{"uid": "12345", "user": "x", "content": "c"}])
        self.assertEqual(a[0]["uid"], b[0]["uid"])

    def test_mask_differs_by_salt(self):
        a, _ = anonymize.mask_users([{"uid": "12345"}], salt="s1")
        b, _ = anonymize.mask_users([{"uid": "12345"}], salt="s2")
        self.assertNotEqual(a[0]["uid"], b[0]["uid"])

    def test_pseudonymize_replaces_names(self):
        rows = [{"user": "张三"}, {"user": "李四"}, {"user": "张三"}]
        out, _ = anonymize.pseudonymize(rows)
        self.assertTrue(out[0]["user"].startswith("用户"))
        self.assertEqual(out[0]["user"], out[2]["user"])
        self.assertNotEqual(out[0]["user"], out[1]["user"])

    def test_mask_does_not_mutate_input(self):
        """
        ★ 脱敏不得修改调用方的原始数据。
        否则"用真实身份集合校验产出"会变成拿已脱敏的值去比对 —— 自我欺骗。
        """
        rows = [{"uid": "12345", "user": "真实昵称", "ip": "上海",
                 "content": "c", "floor_id": 1}]
        out, _ = anonymize.mask_users(rows, keep_ip=False)
        self.assertEqual(rows[0]["uid"], "12345")
        self.assertEqual(rows[0]["user"], "真实昵称")
        self.assertEqual(rows[0]["ip"], "上海")
        self.assertEqual(out[0]["uid"], "u_" + out[0]["uid"][2:])
        self.assertNotEqual(out[0]["uid"], "12345")

    def test_pseudonymize_does_not_mutate_input(self):
        rows = [{"user": "张三", "uid": "1"}]
        out, _ = anonymize.pseudonymize(rows)
        self.assertEqual(rows[0]["user"], "张三")
        self.assertTrue(out[0]["user"].startswith("用户"))

    def test_pseudonymize_return_must_be_used(self):
        """回归：analyze.py 曾丢弃 pseudonymize 的返回值，导致昵称没被替换"""
        rows = [{"user": "张三", "uid": "1"}]
        anon = anonymize.mask_users(rows, keep_ip=False)[0]
        anon2 = anonymize.pseudonymize(anon)[0]
        self.assertTrue(anon2[0]["user"].startswith("用户"))

    def test_row_level_ip_leak_is_detected(self):
        """行级 IP（一条评论对应一个省）是泄漏，检查器必须抓到"""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(ROOT, "fixtures", "_leak_probe.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"rows": [{"uid": "u_x", "user": "用户A", "ip": "广东"}]},
                          f, ensure_ascii=False)
            try:
                r = subprocess.run(
                    [sys.executable, os.path.join(ROOT, "scripts", "check_leaks.py")],
                    cwd=ROOT, capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("IP属地", r.stdout)
            finally:
                os.remove(p)

    def test_aggregate_ip_distribution_is_allowed(self):
        """省份级聚合统计（广东 232 条）不指向个人，应当放行"""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(ROOT, "fixtures", "_agg_probe.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"ip_distribution": [["广东", 232], ["江苏", 115]]},
                          f, ensure_ascii=False)
            try:
                r = subprocess.run(
                    [sys.executable, os.path.join(ROOT, "scripts", "check_leaks.py")],
                    cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout)
            finally:
                os.remove(p)

    def test_anonymize_rows_keeps_users_distinguishable(self):
        """
        ★ 回归：曾先 mask_users 清空昵称再 pseudonymize，
        导致所有用户被折叠成同一个「用户AA」。
        """
        rows = [{"uid": "1", "user": "张三", "ip": "上海", "content": "a"},
                {"uid": "2", "user": "李四", "ip": "北京", "content": "b"},
                {"uid": "1", "user": "张三", "ip": "上海", "content": "c"}]
        out, _ = anonymize.anonymize_rows(rows)
        names = {r["user"] for r in out}
        self.assertEqual(len(names), 2, f"不同用户应得到不同伪名，实得 {names}")
        self.assertEqual(out[0]["user"], out[2]["user"], "同一用户应保持同一伪名")
        self.assertTrue(all(r["uid"].startswith("u_") for r in out))
        self.assertTrue(all(r["ip"] == "" for r in out))

    def test_sample_for_fixture_actually_masks(self):
        """★ 回归：曾丢弃 mask_users 的返回值，抽样集实际没脱敏"""
        rows = [{"uid": str(i), "user": f"u{i}", "ip": "上海",
                 "content": f"c{i}", "date": "2026-05-01",
                 "floor_id": i, "reply_id": str(i)} for i in range(40)]
        out = anonymize.sample_for_fixture(rows, n=10)
        for r in out:
            self.assertTrue(r["uid"].startswith("u_"), r)
            self.assertEqual(r["ip"], "", r)


class TestCrawlerConstruction(unittest.TestCase):

    def test_crawl_result_accepts_target_id(self):
        """
        ★ 回归：CrawlResult 缺少 target_id 字段，
        crawl() 构造时会直接 TypeError —— 主抓取入口根本跑不起来。
        CI 全程零网络请求，所以这个 bug 不会被自动发现。
        """
        from crawler import CrawlResult
        r = CrawlResult(level_id="1", region="cn_gf01", target_id="1")
        self.assertEqual(r.target_id, "1")

    def test_crawl_result_target_id_defaults(self):
        from crawler import CrawlResult
        r = CrawlResult(level_id="1", region="cn_gf01")
        self.assertEqual(r.target_id, "")


class TestOutputSelfCheck(unittest.TestCase):

    def _rows(self):
        return [{"uid": "111222333", "user": "真实昵称甲", "ip": "广东",
                 "content": "c", "is_recommend": False, "date": "2026-05-01",
                 "floor_id": 1, "reply_id": "1", "like": 0, "is_template": False}]

    def test_excel_nickname_column_leak_is_detected(self):
        """
        ★ 回归：自检按表头取列，但 Excel 表头在第 3 行（前两行是标题），
        旧代码取第 1 行当表头 → 整个 Excel 身份检查从未生效。
        """
        import shutil
        import tempfile
        from openpyxl import load_workbook
        from analyze import _scan_output_for_identities

        with tempfile.TemporaryDirectory() as td:
            wb = load_workbook(os.path.join(ROOT, "fixtures", "dummy.xlsx")) \
                if os.path.exists(os.path.join(ROOT, "fixtures", "dummy.xlsx")) else None
            if wb is None:
                from openpyxl import Workbook
                wb = Workbook()
                ws = wb.active
                ws.title = "评论明细"
                ws.cell(row=1, column=1, value="评论明细（含模板标记）")
                ws.cell(row=3, column=1, value="楼层")
                ws.cell(row=3, column=4, value="用户")
                ws.cell(row=3, column=5, value="UID")
                ws.cell(row=3, column=6, value="IP属地")
                ws.cell(row=4, column=4, value="真实昵称甲")
                ws.cell(row=4, column=5, value="111222333")
                ws.cell(row=4, column=6, value="广东")
            p = os.path.join(td, "r.xlsx")
            wb.save(p)
            hits = _scan_output_for_identities(td, self._rows())
            self.assertTrue(hits, "Excel 中的真实昵称/UID/IP 应被抓到")

    def test_kind_alias_handles_user_column_name(self):
        """Excel 列名是「用户」，判定分支只认「昵称」→ 需归一化"""
        import shutil
        import tempfile
        from openpyxl import Workbook
        from analyze import _scan_output_for_identities
        with tempfile.TemporaryDirectory() as td:
            wb = Workbook()
            ws = wb.active
            ws.title = "评论明细"
            ws.cell(row=3, column=4, value="用户")
            ws.cell(row=4, column=4, value="真实昵称甲")
            wb.save(os.path.join(td, "r.xlsx"))
            hits = _scan_output_for_identities(td, self._rows())
            self.assertTrue(any(k == "昵称" for _, k, _ in hits), hits)


class TestDashboardSections(unittest.TestCase):

    def test_trend_chart_not_under_template_heading(self):
        """★ 回归：趋势图曾被放在「模板语剥离」标题下，标题与内容错位"""
        from dashboard import build_dashboard
        import tempfile
        rows = [{"content": "系统模板", "is_recommend": True,
                 "date": "2026-05-01", "like": 0, "uid": "1", "floor_id": 1,
                 "user": "用户A", "ip": "", "reply_id": "1", "is_template": True}]
        strip_templates.classify_templates(rows, verbose=False)
        analysis = {
            "meta": {"total_comments": 1,
                     "template_stats": {"template_count": 1, "template_pct": 100.0,
                                        "real_count": 0, "real_unique": 0,
                                        "real_dup_rate": 0, "top_templates": [],
                                        "user_added": []},
                     "rates": {"apparent": {"total": 1, "good_rate": 100.0,
                                            "bad_rate": 0},
                               "real": {"total": 0, "good_rate": 0, "bad_rate": 0},
                               "delta_bad_rate": 0}},
            "topics": [], "coverage": {"negative_total": 0, "unmatched": 0,
                                       "emotional": 0, "classifiable": 0,
                                       "coverage": 0, "coverage_note": ""},
            "monthly_real": [{"month": "2026-05", "total": 1, "bad": 0,
                              "bad_pct": 0, "sample_note": ""}],
            "monthly_apparent": [], "top_liked": [], "ip_distribution": [],
        }
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "d.html")
            build_dashboard(analysis, rows, p, level_id="1", title="t")
            with open(p, encoding="utf-8") as fh:
                content = fh.read()
            i_trend = content.find("差评趋势")
            i_tpl = content.find("模板语剥离")
            i_chart = content.find("<svg")
            self.assertNotEqual(i_trend, -1, "缺少「差评趋势」小节")
            self.assertLess(i_trend, i_chart, "趋势图应位于「差评趋势」小节内")
            self.assertGreater(i_trend, i_tpl,
                               "「差评趋势」应在「模板语剥离」之后 —— 先讲方法再看结果")
            self.assertLess(i_trend, i_chart)

    def test_public_fixture_has_no_real_identity(self):
        path = os.path.join(ROOT, "fixtures", "sample_chess_30.json")
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        for r in blob["rows"]:
            self.assertTrue(r["uid"].startswith("u_"), r)
            self.assertTrue(r["user"] == "" or r["user"].startswith("用户"), r)
            self.assertEqual(r["ip"], "")


# ------------------------------------------------------------ 领域词典

class TestDomainLexicon(unittest.TestCase):

    def test_merges_without_overwriting(self):
        base = {"A": ["x"], "B": ["y"]}
        domain = {"categories": {"A": ["z"], "C": ["w"]}, "ignore": ["q"]}
        merged = domain_lexicon.merge_categories(base, domain)
        self.assertEqual(set(merged["A"]), {"x", "z"})
        self.assertEqual(set(merged["B"]), {"y"})
        self.assertEqual(merged["C"], ["w"])

    def test_example_loads(self):
        d = domain_lexicon.load_domain_lexicon(
            os.path.join(ROOT, "templates", "domain_example.json"))
        self.assertTrue(d["categories"])
        self.assertTrue(d["ignore"])


# ------------------------------------------------------------ 产出物

def _analysis_stub():
    return {
        "meta": {"total_comments": 30,
                 "template_stats": {"template_count": 20, "template_pct": 66.7,
                                    "real_count": 10, "real_unique": 10,
                                    "real_dup_rate": 0, "top_templates": [],
                                    "user_added": []},
                 "rates": {"apparent": {"total": 30, "good_rate": 66.7,
                                        "bad_rate": 33.3},
                           "real": {"total": 10, "good_rate": 0, "bad_rate": 100},
                           "delta_bad_rate": 66.7}},
        "topics": [], "coverage": {"negative_total": 10, "unmatched": 0,
                                   "emotional": 0, "classifiable": 10,
                                   "coverage": 100.0, "coverage_note": "✅"},
        "monthly_real": [], "monthly_apparent": [], "top_liked": [],
        "ip_distribution": [],
    }


class TestDeliverables(unittest.TestCase):

    def test_dashboard_is_self_contained(self):
        from dashboard import build_dashboard, verify_dashboard
        rows = mk(20, "系统模板", rec=True) + mk(10, "真实评论", rec=False)
        strip_templates.classify_templates(rows, verbose=False)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "d.html")
            build_dashboard(_analysis_stub(), rows, p, level_id="1", title="t")
            issues = verify_dashboard(p)
            self.assertFalse(issues, issues)
            with open(p, encoding="utf-8") as fh:
                content = fh.read()
            self.assertNotIn("<script src=", content)
            self.assertNotIn('<link rel="stylesheet"', content)

    def test_excel_verification(self):
        from excel_report import build_excel, verify_excel
        rows = mk(10, "评论")
        strip_templates.classify_templates(rows, verbose=False)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.xlsx")
            build_excel(_analysis_stub(), rows, p, level_id="1", title="t")
            issues = verify_excel(p)
            self.assertFalse(issues, issues)


if __name__ == "__main__":
    unittest.main(verbosity=2)
