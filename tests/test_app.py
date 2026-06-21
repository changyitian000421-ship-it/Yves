import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class LiquidCalciumAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_paths = (app.DATA_DIR, app.DATABASE_PATH, app.BACKUP_DIR)
        app.DATA_DIR = Path(self.temp_dir.name)
        app.DATABASE_PATH = app.DATA_DIR / "leads.db"
        app.BACKUP_DIR = app.DATA_DIR / "backups"
        app.initialize_database()

    def tearDown(self):
        app.MONITOR_RUNNING.clear()
        app.DATA_DIR, app.DATABASE_PATH, app.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    def test_liquid_supplier_is_scored_and_deduplicated(self):
        lead = {
            "company": "测试液钙副产企业",
            "direction": "upstream",
            "region": "山东省",
            "sector": "环氧氯丙烷",
            "phone": "0536-1234567",
            "match_reason": "盐酸+石灰中和产生液体氯化钙",
            "liquid_concentration": "30%",
            "monthly_volume": "1000吨/月",
        }

        first = app.save_leads([lead])
        second = app.save_leads([{**lead, "commercial_value": "重点"}])

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["updated"], 1)
        with app.database_connection() as connection:
            row = connection.execute("SELECT * FROM leads").fetchone()
            count = connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(row["opportunity_role"], "supplier")
        self.assertEqual(row["liquid_concentration"], "30%")
        self.assertGreaterEqual(row["score"], 20)

    def test_system_events_and_database_backup(self):
        app.log_system_event(
            "warning",
            "collection",
            "测试数据源异常",
            source="回归测试源",
        )
        overview = app.system_overview()

        self.assertEqual(len(overview["events"]), 1)
        self.assertEqual(overview["sources"][0]["warnings"], 1)

        backup = app.create_database_backup()
        self.assertTrue(backup.exists())
        with sqlite3.connect(backup) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM system_events"
                ).fetchone()[0],
                1,
            )
        app.ensure_daily_backup()
        self.assertEqual(len(list(app.BACKUP_DIR.glob("*.db"))), 1)

    def test_monitor_duplicate_run_is_rejected(self):
        app.MONITOR_RUNNING.add(77)
        with self.assertRaisesRegex(RuntimeError, "正在运行"):
            app.run_monitor(77)

    def test_background_monitor_duplicate_start_is_rejected(self):
        app.MONITOR_RUNNING.add(88)
        with self.assertRaisesRegex(RuntimeError, "正在运行"):
            app.start_monitor_background(88)

    def test_monitor_summary_keeps_direction_group(self):
        monitor_id = app.save_monitor(
            "含氟企业每日监控",
            {
                "direction": "environmental",
                "regions": ["east"],
                "sectors": ["fluorochemicals", "rare_earth"],
                "environmentalSources": ["permit", "company_website"],
            },
            24,
        )

        monitors = app.list_monitors()
        monitor = next(item for item in monitors if item["id"] == monitor_id)

        self.assertEqual(monitor["direction"], "environmental")
        self.assertEqual(monitor["directionLabel"], "含氟废水企业")
        self.assertEqual(monitor["sectorCount"], 2)
        self.assertIn("来源：2 个", monitor["summary"])

    def test_new_lead_notification_can_open_saved_detail(self):
        result = app.save_leads(
            [
                {
                    "company": "测试新线索有限公司",
                    "direction": "downstream",
                    "region": "山东",
                    "sector": "水处理",
                    "phone": "0531-12345678",
                }
            ]
        )

        self.assertEqual(result["created"], 1)
        with app.database_connection() as connection:
            notification = connection.execute(
                "SELECT * FROM notifications WHERE type = 'new_lead'"
            ).fetchone()
        lead = app.get_saved_lead(notification["lead_id"])

        self.assertIsNotNone(lead)
        self.assertEqual(lead["company"], "测试新线索有限公司")
        self.assertEqual(lead["phone"], "0531-12345678")

    @patch("app.fetch_html")
    def test_competitor_intelligence_builds_reverse_profile(self, fetch_html):
        fetch_html.return_value = """
        <ul>
          <li class="res-list">
            <h3><a>液体氯化钙厂家_山东同行化工有限公司</a></h3>
            <a data-mdurl="https://detail.1688.com/offer/123.html">产品页</a>
            <p class="res-desc">
              山东同行化工有限公司供应液体氯化钙、工业级氯化钙，
              用于道路融雪、水处理和集装箱干燥剂，支持槽车运输。
            </p>
          </li>
        </ul>
        """

        leads, errors, request_count = app.collect_competitor_intelligence(
            ["山东"],
            {"liquid": app.COMPETITOR_SECTOR_LIBRARY["liquid"]},
            ["1688"],
            [],
            False,
        )

        self.assertEqual(errors, [])
        self.assertEqual(request_count, 1)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].company, "山东同行化工有限公司")
        self.assertIn("融雪剂/道路除冰", leads[0].competitor_industries)
        self.assertIn("水处理", leads[0].competitor_industries)
        self.assertIn("液体氯化钙", leads[0].competitor_keywords)
        self.assertIn("反向开发山东", leads[0].pitch)

    def test_competitor_company_extraction_splits_cooperation_title(self):
        company = app.extract_competitor_company(
            "潍坊海之源化工与中国石油化工集团有限公司签署氯化钙合作协议"
        )
        self.assertEqual(company, "中国石油化工集团有限公司")


if __name__ == "__main__":
    unittest.main()
