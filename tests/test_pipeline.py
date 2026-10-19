"""
单元测试 —— 覆盖六条可靠性规则里能用代码断言的部分

测试框架只用标准库 unittest；Excel 相关用例需要 openpyxl（见 requirements.txt）：
    python -m unittest discover tests/ -v
"""

import json
import os
import subprocess
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


def run_script(args):
    """跨平台跑子进程：显式 UTF-8，避免 Windows 默认 GBK 解码失败"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable] + args, cwd=ROOT,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


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

    def test_reply_count_is_preserved(self):
        """★ 回归：schema 曾丢掉 reply_count，Excel 的「跟帖数」永远是 0"""
        out = schema.normalize_row(
            {"reply_id": "1", "content": "c", "reply_count": "3"})
        self.assertEqual(out["reply_count"], 3)

    def test_timestamp_uses_beijing_time(self):
        """时间戳要固定按 +08:00 解释，否则 UTC 服务器会把月度边界算错"""
        self.assertEqual(schema._norm_date(1000000000),
                         "2001-09-09 09:46:40")


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

    def test_template_keeps_original_sample_text(self):
        """归一化文本用于匹配，展示要保留原始标点"""
        rows = mk(10, "非常优秀的奇域，推荐大家游玩~")
        _, stats = strip_templates.classify_templates(rows, verbose=False)
        tpl = stats["templates"][0]
        self.assertEqual(tpl["sample"], "非常优秀的奇域，推荐大家游玩~")
        self.assertNotEqual(tpl["text"], tpl["sample"])

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

    def test_unknown_month_sorts_first(self):
        """★ 缺日期的分组不能排到最后冒充「最近一个月」"""
        rows = [
            {"content": "a", "is_recommend": False, "date": ""},
            {"content": "b", "is_recommend": False, "date": "2026-05-01"},
        ]
        m = strip_templates.group_by_month(rows, real_only=True)
        self.assertEqual([r["month"] for r in m], ["unknown", "2026-05"])


# ------------------------------------------------------------ 完整性校验

class TestIntegrity(unittest.TestCase):

    def test_detects_missing_rows(self):
        res = CrawlResult(level_id="1", region="cn_gf01", total=100)
        res.rows = [{"reply_id": str(i), "floor_id": i, "is_recommend": True}
                    for i in range(50)]
        res.meta = {"goodRate": "80%"}
        res.passes = [Pass(sort_type=2, pages=5)]
        ok, checks = res.integrity()
        self.assertFalse(ok)
        self.assertFalse(checks["count_matches_total"])

    def test_detects_degraded_pass(self):
        res = CrawlResult(level_id="1", region="cn_gf01", total=10)
        res.rows = [{"reply_id": str(i), "floor_id": i, "is_recommend": True}
                    for i in range(10)]
        res.meta = {"goodRate": "100%"}
        res.passes = [Pass(sort_type=2, pages=3),
                      Pass(sort_type=4, pages=0, error="boom")]
        ok, _ = res.integrity()
        self.assertTrue(res.degraded)
        self.assertFalse(ok)

    def test_passes_on_clean_data(self):
        res = CrawlResult(level_id="1", region="cn_gf01", total=10)
        res.rows = [{"reply_id": str(i), "floor_id": i, "is_recommend": True}
                    for i in range(10)]
        res.meta = {"goodRate": "100%"}
        res.passes = [Pass(sort_type=2, pages=1), Pass(sort_type=4, pages=1)]
        ok, checks = res.integrity()
        self.assertTrue(ok, checks)

    def test_span_detects_gaps(self):
        res = CrawlResult(level_id="1", region="cn_gf01", total=10)
        res.rows = [{"reply_id": str(f), "floor_id": f, "is_recommend": True}
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

    def test_pseudonyms_use_excel_style_labels(self):
        """★ 回归：旧编号是「用户AA / 用户1A」，跳过了 用户A..用户Z"""
        rows = [{"user": f"name{i}"} for i in range(28)]
        seen = anonymize.pseudonymize(rows)[1]
        self.assertEqual(seen["name0"], "用户A")
        self.assertEqual(seen["name25"], "用户Z")
        self.assertEqual(seen["name26"], "用户AA")
        self.assertEqual(seen["name27"], "用户AB")

    def test_row_level_ip_leak_is_detected(self):
        """行级 IP（一条评论对应一个省）是泄漏，检查器必须抓到"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(ROOT, "fixtures", "_leak_probe.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"rows": [{"uid": "u_x", "user": "用户A", "ip": "广东"}]},
                          f, ensure_ascii=False)
            try:
                r = run_script([os.path.join(ROOT, "scripts", "check_leaks.py")])
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("IP属地", r.stdout)
            finally:
                os.remove(p)

    def test_aggregate_ip_distribution_is_allowed(self):
        """省份级聚合统计（广东 232 条）不指向个人，应当放行"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(ROOT, "fixtures", "_agg_probe.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"ip_distribution": [["广东", 232], ["江苏", 115]]},
                          f, ensure_ascii=False)
            try:
                r = run_script([os.path.join(ROOT, "scripts", "check_leaks.py")])
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


class TestCrawlOffline(unittest.TestCase):
    """
    ★ crawl() 本体的离线测试。

    之前只断言 CrawlResult(...) 能否构造，没真调用 crawl() ——
    坏在构造参数上被抓到了，坏在函数体里照样漏。
    用假 _post 把整条链路（双趟合并 / 尾部确认 / floor_id 兜底）跑一遍。
    """

    def _patch_post(self, pages_by_sort):
        """pages_by_sort: {sort_type: [page1_data, page2_data, ...]}"""
        import crawler

        state = {"calls": 0}

        def fake_post(body, timeout=25):
            st = body["cursor"]["sort_type"]
            nxt = body["cursor"]["next"]
            pages = pages_by_sort.get(st, [])
            idx = 0 if nxt == "" else int(nxt)
            data = pages[idx] if idx < len(pages) else {"reply_list": [],
                                                        "cursor": {"next": "",
                                                                   "has_more": False}}
            state["calls"] += 1
            return {"retcode": 0, "data": data}

        original = crawler._post
        crawler._post = fake_post
        self.addCleanup(lambda: setattr(crawler, "_post", original))
        return state

    @staticmethod
    def _page(rows, nxt, has_more=True):
        return {"reply_list": rows, "total": 999,
                "cursor": {"next": nxt, "has_more": has_more,
                           "sort_type": "SORT_TYPE_FLOOR_DESC"}}

    @staticmethod
    def _row(reply_id, floor_id, like=0, uid="1", nick="甲"):
        return {"reply_id": reply_id, "floor_id": floor_id, "content": "c",
                "created_at": 1792396503, "is_recommend": True,
                "client_ip": "广东",
                "reply_stat": {"like_count": str(like), "reply_count": "0"},
                "user_info": {"uid": uid, "nickname": nick}}

    def test_crawl_merges_two_passes(self):
        import crawler
        self._patch_post({
            crawler.SORT_FLOOR_DESC: [
                self._page([self._row("1", 1, like=5)], "1"),
                self._page([self._row("2", 2, like=7)], "2", has_more=False),
            ],
            crawler.SORT_HOT: [
                self._page([self._row("2", 2, like=3)], "1"),
            ],
        })
        r = crawler.crawl("123", delay=0)
        self.assertEqual(len(r.rows), 2, "两趟重复行应合并")
        by = {x["floor_id"]: x for x in r.rows}
        self.assertEqual(by[2]["like"], 7, "点赞数应取两趟最大值")

    def test_crawl_falls_back_to_reply_id_when_floor_missing(self):
        """
        ★ floor_id 缺失时，合并主键必须退回 reply_id。
        否则所有缺 floor_id 的行会合并成同一个 key（None），数据静默丢失。
        """
        import crawler
        self._patch_post({
            crawler.SORT_FLOOR_DESC: [
                self._page([self._row("r1", None), self._row("r2", None),
                            self._row("r3", None)], "1"),
                self._page([], "", has_more=False),
            ],
            crawler.SORT_HOT: [],
        })
        r = crawler.crawl("123", delay=0)
        self.assertEqual(len(r.rows), 3,
                         f"缺 floor_id 的行不该合并成 1 条，实得 {len(r.rows)}")
        self.assertEqual(r.missing_floor, 3, "缺失数应被记录")

    def test_integrity_does_not_crash_on_missing_floor(self):
        """
        ★ 校验器不能先于被校验对象崩溃。
        floor_id 全为 None 时 span 计算曾直接 TypeError。
        """
        import crawler
        res = crawler.CrawlResult(level_id="1", region="cn_gf01", total=3)
        res.rows = [{"reply_id": "1", "floor_id": None, "is_recommend": True},
                    {"reply_id": "2", "floor_id": None, "is_recommend": True},
                    {"reply_id": "3", "floor_id": None, "is_recommend": True}]
        res.meta = {"goodRate": "100%"}
        res.passes = [crawler.Pass(sort_type=crawler.SORT_FLOOR_DESC, pages=1,
                                   missing_floor=3)]
        ok, checks = res.integrity()          # 不能抛异常
        self.assertFalse(checks["floor_id_present"], "缺失 floor_id 应判不通过")
        self.assertFalse(ok)

    def test_crawl_reports_truncation(self):
        """max_pages 用尽时要标记 truncated（否则静默停止，不报错）"""
        import crawler
        pages = []
        for i in range(10):                       # 游标递增，不会提前 break
            pages.append(self._page([self._row(str(i), i)], str(i + 1)))
        self._patch_post({crawler.SORT_FLOOR_DESC: pages, crawler.SORT_HOT: []})
        r = crawler.crawl("123", delay=0, max_pages=3)
        self.assertTrue(any(p.truncated for p in r.passes),
                        "max_pages 用尽应标记 truncated")
        self.assertTrue(r.degraded, "截断的抓取必须判为 degraded")
        _, checks = r.integrity()
        self.assertFalse(checks["no_degraded_pass"])

    def test_empty_next_stops_immediately(self):
        """★ 回归：末尾 next 为空时曾回头重抓第一页，可能一路跑到 max_pages"""
        import crawler
        self._patch_post({
            crawler.SORT_FLOOR_DESC: [
                self._page([self._row("1", 1)], "", has_more=False)],
            crawler.SORT_HOT: [],
        })
        r = crawler.crawl("123", delay=0, max_pages=5)
        p = [x for x in r.passes
             if x.sort_type == crawler.SORT_FLOOR_DESC][0]
        self.assertEqual(p.pages, 1, "next 为空时不应重新抓第一页")

    def test_parse_rows_reads_user_info(self):
        """
        ★ uid / 昵称在 user_info 里，不在顶层 —— 读错会全是空值。
        """
        import crawler
        rows = crawler._parse_rows(
            {"reply_list": [self._row("9", 9, uid="12345", nick="某人")]})
        self.assertEqual(rows[0]["uid"], "12345")
        self.assertEqual(rows[0]["user"], "某人")
        self.assertEqual(rows[0]["ip"], "广东")
        self.assertTrue(rows[0]["date"].startswith("2026-"))

    def test_timestamp_is_beijing_time(self):
        """★ 回归：时间戳曾按运行机器的本地时区解释"""
        import crawler
        self.assertEqual(crawler._norm_ts(0), "1970-01-01 08:00:00")

    def test_floor_id_string_is_cast_to_int(self):
        """★ 回归：字符串楼层号会让 span 计算 max-min 抛 TypeError"""
        import crawler
        rows = crawler._parse_rows({"reply_list": [self._row("1", "7")]})
        self.assertEqual(rows[0]["floor_id"], 7)

    def test_integrity_reports_seven_checks(self):
        """完整性校验是 7 项，主键检查针对 reply_id"""
        res = CrawlResult(level_id="1", region="cn_gf01", total=1)
        res.rows = [{"reply_id": "1", "floor_id": 1, "is_recommend": True}]
        res.passes = [Pass(sort_type="x", pages=1)]
        res.meta = {"goodRate": "100%"}
        ok, checks = res.integrity()
        self.assertEqual(len(checks), 7, checks)
        self.assertIn("reply_id_unique", checks)
        self.assertIn("floor_id_present", checks)
        self.assertTrue(ok, checks)


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
            wb.close()
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
            wb.close()
            hits = _scan_output_for_identities(td, self._rows())
            self.assertTrue(any(k == "昵称" for _, k, _ in hits), hits)


class TestLeakCheckerNicknameLength(unittest.TestCase):
    """★ 回归：2 字昵称（"张三"）曾完全检不出"""

    def _run(self):
        return run_script([os.path.join(ROOT, "scripts", "check_leaks.py")])

    def test_two_char_nickname_is_collected(self):
        """基准数据里的 2 字昵称必须被收进真实身份集合"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "check_leaks", os.path.join(ROOT, "scripts", "check_leaks.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "ref.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"rows": [{"uid": "111222333", "user": "张三",
                                     "ip": "广东", "content": "c"}]},
                          f, ensure_ascii=False)
            uids, names = mod.load_reference(p)
            self.assertIn("张三", names, "2 字昵称必须被收集")

    def test_anonymized_reference_ignores_pseudonyms(self):
        """★ 回归：用已脱敏数据当基准时，伪名/哈希曾导致假泄漏"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "check_leaks_pseudo", os.path.join(ROOT, "scripts", "check_leaks.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "ref.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"rows": [{"uid": "u_0123456789",
                                     "user": "用户AA"}]}, f,
                          ensure_ascii=False)
            uids, names = mod.load_reference(p)
            self.assertEqual(uids, set())
            self.assertEqual(names, set())

    def test_two_char_nickname_leak_is_reported(self):
        """2 字昵称出现在 user 字段位置 → 必须报泄漏"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ref = os.path.join(td, "ref.json")
            with open(ref, "w", encoding="utf-8") as f:
                json.dump({"rows": [{"uid": "111222333", "user": "张三",
                                     "ip": "广东", "content": "c"}]},
                          f, ensure_ascii=False)
            probe = os.path.join(ROOT, "fixtures", "_probe_2char.json")
            with open(probe, "w", encoding="utf-8") as f:
                json.dump({"rows": [{"uid": "111222333", "user": "张三",
                                     "ip": "", "content": "c"}]},
                          f, ensure_ascii=False)
            try:
                r = run_script([os.path.join(ROOT, "scripts", "check_leaks.py"),
                                ref])
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("张三", r.stdout)
            finally:
                os.remove(probe)


class TestAggregateSheetNotFlagged(unittest.TestCase):
    """★ 回归：地域分布表（省份级计数）曾被误判为行级 IP 泄漏"""

    def test_aggregate_sheet_is_skipped(self):
        import shutil
        import tempfile
        from openpyxl import Workbook
        from analyze import _scan_output_for_identities
        rows = [{"uid": "111222333", "user": "真实昵称甲", "ip": "广东",
                 "content": "c", "is_recommend": False, "date": "2026-05-01",
                 "floor_id": 1, "reply_id": "1", "like": 0, "is_template": False}]
        with tempfile.TemporaryDirectory() as td:
            wb = Workbook()
            ws = wb.active
            ws.title = "地域分布"
            ws.cell(row=3, column=1, value="IP属地")
            ws.cell(row=3, column=2, value="评论数")
            ws.cell(row=4, column=1, value="广东")
            ws.cell(row=4, column=2, value=232)
            wb.save(os.path.join(td, "r.xlsx"))
            wb.close()
            hits = _scan_output_for_identities(td, rows)
            self.assertFalse(hits, f"聚合分布表不应被判为泄漏，实得 {hits}")


class TestLeakCheckerXlsx(unittest.TestCase):
    """★ 回归：check_leaks 曾先按 UTF-8 读 xlsx，解码失败后直接跳过整张表"""

    def test_xlsx_identity_columns_are_checked(self):
        import importlib.util
        from openpyxl import Workbook
        spec = importlib.util.spec_from_file_location(
            "check_leaks_xlsx", os.path.join(ROOT, "scripts", "check_leaks.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.title = "评论明细"
            ws.cell(row=1, column=1, value="标题")
            ws.cell(row=2, column=1, value="副标题")
            ws.cell(row=3, column=1, value="楼层")
            ws.cell(row=3, column=4, value="用户")
            ws.cell(row=3, column=5, value="UID")
            ws.cell(row=3, column=6, value="IP属地")
            ws.cell(row=4, column=4, value="张三")
            ws.cell(row=4, column=5, value="111222333")
            ws.cell(row=4, column=6, value="广东")
            wb.save(p)
            wb.close()
            leaks = []
            mod.check_xlsx(p, "r.xlsx", {"111222333"}, {"张三"}, leaks)
            self.assertEqual({k for _, k, _ in leaks},
                             {"昵称", "UID", "IP属地(行级)"}, leaks)


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

class TestClustering(unittest.TestCase):
    """★ 回归：泛词抢先命中、整句否定误删、重叠信息丢失"""

    def _rows(self, *texts):
        return [{"content": t, "is_recommend": False, "date": "2026-10-01",
                 "like": 0, "uid": str(i), "user": f"u{i}", "ip": "",
                 "floor_id": i + 1, "reply_id": str(i)}
                for i, t in enumerate(texts)]

    def test_specific_keyword_beats_generic_one(self):
        """「卡顿」必须归入性能；不能因为「卡」先匹配就永远归操作手感"""
        import analyze as mod
        stats, _ = mod.cluster_topics(self._rows("卡顿得没法玩"))
        by = {s["category"]: s["count"] for s in stats}
        self.assertEqual(by.get("性能与稳定性"), 1, stats)
        self.assertNotIn("操作手感", by, stats)

    def test_negated_defect_is_not_a_complaint(self):
        """「一点都不卡」是好评，不应算成操作手感"""
        import analyze as mod
        stats, cov = mod.cluster_topics(self._rows("一点都不卡"))
        self.assertEqual(sum(s["count"] for s in stats), 0, stats)
        self.assertEqual(cov["unmatched"], 1)

    def test_mixed_sentiment_is_kept(self):
        """★ 回归：整句含「不错」曾把「但是太卡」一起丢掉"""
        import analyze as mod
        stats, _ = mod.cluster_topics(self._rows("画面不错，但是太卡"))
        by = {s["category"]: s["count"] for s in stats}
        self.assertEqual(by.get("操作手感"), 1, stats)

    def test_no_difficulty_is_still_a_complaint(self):
        """「没难度」是差评（太简单），不能被当成正面否定剔除"""
        import analyze as mod
        stats, _ = mod.cluster_topics(self._rows("太简单了，没难度"))
        by = {s["category"]: s["count"] for s in stats}
        self.assertEqual(by.get("关卡设计"), 1, stats)

    def test_overlap_is_recorded(self):
        """多类别命中时保留主类别，同时记录重叠类别"""
        import analyze as mod
        stats, cov = mod.cluster_topics(self._rows("卡顿而且按钮点不动"))
        self.assertEqual(cov["multi_label"], 1)
        self.assertEqual(stats[0]["category"], "性能与稳定性", stats)
        self.assertIn("ui与交互", stats[0]["overlap"])


class TestIpDistribution(unittest.TestCase):

    def test_templates_are_excluded(self):
        """★ 回归：地域分布曾把 54% 的模板评论算进去"""
        import analyze as mod
        rows = [{"ip": "广东"}, {"ip": "广东"}, {"ip": "江苏"}]
        out = mod._ip_distribution(rows, [True, False, False])
        self.assertEqual(out, [("广东", 1), ("江苏", 1)])


class TestPseudonymIsNotIdentity(unittest.TestCase):

    def test_anonymized_input_does_not_raise_false_leak(self):
        """★ 回归：用已脱敏数据复现时，伪名曾被当成真实昵称稳定误报"""
        import analyze as mod
        rows = [{"uid": "u_0123456789", "user": "用户AA", "ip": ""}]
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "a.json"), "w", encoding="utf-8") as f:
                json.dump({"rows": [{"uid": "u_0123456789",
                                     "user": "用户AA"}]}, f,
                          ensure_ascii=False)
            hits = mod._scan_output_for_identities(td, rows)
            self.assertEqual(hits, [])


class TestDomainLexicon(unittest.TestCase):

    def test_no_path_returns_empty(self):
        """★ 回归：不传 --domain-lexicon 时曾默认套用象棋示例词典"""
        d = domain_lexicon.load_domain_lexicon()
        self.assertEqual(d["categories"], {})
        self.assertEqual(d["ignore"], [])

    def test_missing_path_raises(self):
        """指定了词典但文件不存在 → 报错，不能静默回退"""
        with self.assertRaises(FileNotFoundError):
            domain_lexicon.load_domain_lexicon("no_such_domain.json")

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

    def test_excel_does_not_hardcode_case_rate(self):
        """★ 回归：Excel 曾无条件写死「官方公布的 79.2%」"""
        from excel_report import build_excel
        from openpyxl import load_workbook
        rows = mk(20, "系统模板", rec=True) + mk(10, "真实评论", rec=False)
        strip_templates.classify_templates(rows, verbose=False)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.xlsx")
            build_excel(_analysis_stub(), rows, p, level_id="1", title="t",
                        meta={})
            wb = load_workbook(p)
            try:
                values = [str(c.value or "") for c in wb["概览"]["A"]]
            finally:
                wb.close()
            text = "\n".join(values)
            self.assertNotIn("79.2", text)
            self.assertIn("66.7", text)

    def test_excel_weekday_sample_and_reply_count(self):
        """★ 回归：星期列永远为空；模板样本原句永远为空；reply_count 丢失"""
        from datetime import datetime
        from excel_report import build_excel
        from openpyxl import load_workbook
        rows = mk(20, "系统模板，带标点~", rec=True)
        rows.append({"content": "真实差评", "is_recommend": False,
                     "date": "2026-05-18 10:30:00", "like": 0,
                     "uid": "1", "floor_id": 1, "user": "u", "ip": "",
                     "reply_id": "1", "reply_count": 3})
        _, tstats = strip_templates.classify_templates(rows, verbose=False)
        analysis = _analysis_stub()
        analysis["meta"]["template_stats"] = tstats
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.xlsx")
            build_excel(analysis, rows, p, level_id="1", title="t", meta={})
            wb = load_workbook(p)
            try:
                ws = wb["评论明细"]
                target = None
                for r in ws.iter_rows(min_row=4, values_only=True):
                    if r[9] == "真实差评":
                        target = r
                        break
                self.assertIsNotNone(target)
                expected = "周" + "一二三四五六日"[datetime(2026, 5, 18).weekday()]
                self.assertEqual(target[11], expected)
                self.assertEqual(target[13], 3)
                ws2 = wb["模板剥离分析"]
                self.assertEqual(ws2.cell(row=4, column=1).value,
                                 "系统模板，带标点~")
                self.assertEqual(ws2.cell(row=4, column=6).value,
                                 "系统模板，带标点~")
            finally:
                wb.close()

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
