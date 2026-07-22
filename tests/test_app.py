import sqlite3
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

import app


class LiquidCalciumAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_paths = (
            app.DATA_DIR,
            app.DATABASE_PATH,
            app.TURSO_REPLICA_PATH,
            app.BACKUP_DIR,
        )
        app.DATA_DIR = Path(self.temp_dir.name)
        app.DATABASE_PATH = app.DATA_DIR / "leads.db"
        app.TURSO_REPLICA_PATH = app.DATA_DIR / "turso-replica.db"
        app.BACKUP_DIR = app.DATA_DIR / "backups"
        app.initialize_database()

    def tearDown(self):
        app.MONITOR_RUNNING.clear()
        (
            app.DATA_DIR,
            app.DATABASE_PATH,
            app.TURSO_REPLICA_PATH,
            app.BACKUP_DIR,
        ) = self.original_paths
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

    def test_repeated_collection_does_not_inflate_score(self):
        lead = {
            "company": "稳定评分水处理有限公司",
            "direction": "downstream",
            "region": "山东",
            "sector": "水处理",
            "source": "高德 POI",
            "score": 48,
            "phone": "0531-12345678",
            "match_reason": "水处理药剂行业关键词命中",
        }

        app.save_leads([lead])
        first = app.list_saved_leads({"q": "稳定评分"})[0]
        app.save_leads([lead])
        second = app.list_saved_leads({"q": "稳定评分"})[0]

        self.assertEqual(first["score"], second["score"])
        self.assertEqual(second["relevance_score"], 48)
        self.assertEqual(second["quality_grade"], "B")

    def test_repeated_sources_merge_contacts_and_upgrade_quality(self):
        company = "多来源副产液钙有限公司"
        app.save_leads(
            [
                {
                    "company": company,
                    "direction": "upstream",
                    "source": "高德 POI",
                    "score": 42,
                    "phone": "0536-1111111",
                    "match_reason": "化工生产企业",
                }
            ]
        )
        app.save_leads(
            [
                {
                    "company": company,
                    "direction": "upstream",
                    "source": "官方环评公示",
                    "score": 55,
                    "phone": "0536-2222222",
                    "match_reason": "环评明确副产液体氯化钙",
                    "process_basis": "盐酸与石灰中和产生液体氯化钙",
                    "search_url": "https://example.com/eia",
                }
            ]
        )

        lead = app.list_saved_leads({"q": "多来源副产"})[0]
        self.assertIn("0536-1111111", lead["phone"])
        self.assertIn("0536-2222222", lead["phone"])
        self.assertIn("高德 POI", lead["source"])
        self.assertIn("官方环评公示", lead["source"])
        self.assertEqual(lead["quality_grade"], "A")
        self.assertTrue(lead["actionable"])

    def test_inferred_website_lead_is_not_marked_verified(self):
        lead = app.prepare_lead_payload(
            {
                "company": "行业推断新能源有限公司",
                "direction": "environmental",
                "source": "企业官网",
                "score": 58,
                "confidence": "官网行业推断",
                "match_reason": "新能源材料行业推断",
                "process_basis": "行业可能产生含氟废水",
            }
        )

        self.assertEqual(lead["quality_grade"], "B")
        self.assertIn("需确认", lead["quality_issues"])

    def test_amap_combined_province_city_is_normalized_and_enforced(self):
        self.assertEqual(app.normalize_amap_city("山东省济宁市"), "济宁市")
        self.assertTrue(
            app.amap_region_matches("山东省济宁市", "山东省", "济宁市", "任城区")
        )
        self.assertFalse(
            app.amap_region_matches("山东省济宁市", "陕西省", "西安市", "莲湖区")
        )
        self.assertTrue(app.amap_region_matches("山东", "山东省", "济南市", "历下区"))

    def test_precision_mode_rejects_storefront_pois(self):
        self.assertFalse(
            app.likely_downstream_company(
                "水处理化工药剂",
                "购物服务;专卖店;专营店",
            )
        )
        self.assertTrue(
            app.likely_downstream_company(
                "山东清源水处理有限公司",
                "公司企业;公司;环保科技",
            )
        )

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
        connection = sqlite3.connect(backup)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM system_events"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()
        app.ensure_daily_backup()
        self.assertEqual(len(list(app.BACKUP_DIR.glob("*.db"))), 1)

    def test_turso_connection_failure_falls_back_to_sqlite(self):
        original_disabled = app.TURSO_RUNTIME_DISABLED
        original_error = app.TURSO_RUNTIME_ERROR
        app.TURSO_RUNTIME_DISABLED = False
        app.TURSO_RUNTIME_ERROR = ""
        try:
            with (
                patch.dict(
                    os.environ,
                    {
                        "TURSO_DATABASE_URL": "libsql://invalid.example",
                        "TURSO_AUTH_TOKEN": "invalid-token",
                    },
                ),
                patch.object(app, "turso_sync") as sync_driver,
            ):
                sync_driver.connect_sync.side_effect = RuntimeError("auth failed")
                with app.database_connection() as connection:
                    connection.execute("CREATE TABLE IF NOT EXISTS fallback_test(id INTEGER)")
                    connection.execute("INSERT INTO fallback_test(id) VALUES (1)")
                connection = sqlite3.connect(app.DATABASE_PATH)
                try:
                    count = connection.execute("SELECT COUNT(*) FROM fallback_test").fetchone()[0]
                finally:
                    connection.close()

            self.assertEqual(count, 1)
            self.assertTrue(app.TURSO_RUNTIME_DISABLED)
            self.assertIn("auth failed", app.TURSO_RUNTIME_ERROR)
        finally:
            app.TURSO_RUNTIME_DISABLED = original_disabled
            app.TURSO_RUNTIME_ERROR = original_error

    @patch("app.create_database_backup")
    @patch("app.turso_active", return_value=True)
    def test_daily_backup_is_skipped_for_turso(self, _turso_active, create_backup):
        app.ensure_daily_backup()
        create_backup.assert_not_called()

    @patch("app.log_activity")
    @patch("app.turso_active", return_value=True)
    def test_turso_manual_backup_uses_sqlite_replica(
        self,
        _turso_active,
        _log_activity,
    ):
        connection = sqlite3.connect(app.TURSO_REPLICA_PATH)
        try:
            connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO sample(value) VALUES ('ok')")
            connection.commit()
        finally:
            connection.close()

        backup = app.create_database_backup()

        connection = sqlite3.connect(backup)
        try:
            value = connection.execute("SELECT value FROM sample").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(value, "ok")

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

    def test_save_leads_discards_invalid_monitor_foreign_key(self):
        result = app.save_leads(
            [
                {
                    "company": "无效监控关联测试有限公司",
                    "direction": "downstream",
                    "region": "山东",
                    "sector": "水处理",
                }
            ],
            monitor_id=999999,
        )

        self.assertEqual(result["created"], 1)
        with app.database_connection() as connection:
            lead = connection.execute(
                "SELECT id, monitor_id FROM leads WHERE company = ?",
                ("无效监控关联测试有限公司",),
            ).fetchone()
            notification = connection.execute(
                "SELECT lead_id, monitor_id FROM notifications WHERE lead_id = ?",
                (lead["id"],),
            ).fetchone()

        self.assertIsNone(lead["monitor_id"])
        self.assertEqual(notification["lead_id"], lead["id"])
        self.assertIsNone(notification["monitor_id"])

    def test_bulk_save_notifications_reference_existing_leads(self):
        leads = [
            {
                "company": f"批量保存测试企业{i}有限公司",
                "direction": "downstream",
                "region": "山东",
                "sector": "水处理",
            }
            for i in range(120)
        ]

        result = app.save_leads(leads)

        self.assertEqual(result["created"], 120)
        with app.database_connection() as connection:
            orphan_count = connection.execute(
                """
                SELECT COUNT(*) FROM notifications AS notification
                LEFT JOIN leads AS lead ON lead.id = notification.lead_id
                WHERE notification.lead_id IS NOT NULL AND lead.id IS NULL
                """
            ).fetchone()[0]
        self.assertEqual(orphan_count, 0)

    def test_manual_profile_create_is_deduplicated_and_keeps_sales_fields(self):
        first = app.create_manual_lead(
            {
                "company": "手动档案化工有限公司",
                "direction": "upstream",
                "salesStatus": "qualified",
                "owner": "张三",
                "phone": "0532-88888888",
                "region": "山东 青岛",
                "sector": "环氧氯丙烷",
                "liquidConcentration": "32%",
                "monthlyVolume": "500吨/月",
                "notes": "已确认副产液钙，等待报价。",
            }
        )
        second = app.create_manual_lead(
            {
                "company": "手动档案化工有限公司",
                "direction": "upstream",
                "salesStatus": "quoted",
                "owner": "李四",
                "commercialValue": "80万元/年",
            }
        )

        self.assertEqual(first["persistence"]["created"], 1)
        self.assertEqual(second["persistence"]["updated"], 1)
        leads = app.list_saved_leads({"limit": "100"})

        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["sales_status"], "quoted")
        self.assertEqual(leads[0]["owner"], "李四")
        self.assertEqual(leads[0]["phone"], "0532-88888888")
        self.assertEqual(leads[0]["liquid_concentration"], "32%")
        self.assertEqual(leads[0]["commercial_value"], "80万元/年")

    @patch("app.collect_amap_leads")
    def test_upstream_collection_relaxes_when_strict_filter_is_empty(self, collect_amap):
        collect_amap.side_effect = [
            ([], []),
            (
                [
                    app.Lead(
                        company="山东候选化工有限公司",
                        region="山东",
                        sector="环氧氯丙烷",
                        source="高德 POI",
                        score=52,
                        direction="upstream",
                        confidence="待核验",
                    )
                ],
                [],
            ),
        ]

        result = app.collect_leads(
            {
                "direction": "upstream",
                "regions": ["山东"],
                "sectors": ["epichlorohydrin"],
                "amapKey": "test-key",
                "pages": 1,
                "fastMode": True,
                "strictUpstream": True,
            }
        )

        self.assertEqual(result["meta"]["mode"], "amap")
        self.assertEqual(result["leads"][0]["company"], "山东候选化工有限公司")
        self.assertIn("放宽", " ".join(result["errors"]))
        self.assertEqual(collect_amap.call_count, 2)

    @patch("app.collect_procurement_companies")
    def test_procurement_falls_back_to_official_search_entries(self, collect_procurement):
        collect_procurement.return_value = ([], ["公共资源平台暂时无结果"], 1)

        result = app.collect_leads(
            {
                "direction": "procurement",
                "regions": ["山东"],
                "sectors": ["liquid_calcium_chloride"],
                "noticeTypes": ["purchase"],
                "procurementSources": ["ggzy"],
                "dateWindow": "30d",
            }
        )

        self.assertGreater(len(result["leads"]), 0)
        self.assertIn("检索入口", result["errors"][0])
        self.assertEqual(result["leads"][0]["direction"], "procurement")

    @patch("app.fetch_permit_records")
    def test_environmental_permit_timeout_uses_verified_index(self, fetch_permit_records):
        fetch_permit_records.side_effect = TimeoutError("timed out")

        result = app.collect_leads(
            {
                "direction": "environmental",
                "regions": ["山东"],
                "sectors": ["fluorochemicals"],
                "environmentalSources": ["permit"],
                "pages": 1,
            }
        )

        self.assertEqual(result["meta"]["mode"], "environmental")
        self.assertGreaterEqual(len(result["leads"]), 1)
        self.assertIn("已核验官方许可索引", result["errors"][0])

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
