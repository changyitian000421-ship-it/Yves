import sqlite3
import tempfile
import unittest
import os
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

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
        with app.API_USAGE_BUFFER_LOCK:
            app.API_USAGE_BUFFER.clear()
        app.initialize_database()

    def tearDown(self):
        app.MONITOR_RUNNING.clear()
        with app.API_USAGE_BUFFER_LOCK:
            app.API_USAGE_BUFFER.clear()
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

    def test_directory_province_expands_to_every_anhui_city(self):
        regions = app.directory_scan_regions("安徽省")

        self.assertEqual(app.directory_province_name("安徽省"), "安徽")
        self.assertEqual(len(regions), 16)
        self.assertIn("安徽省合肥市", regions)
        self.assertIn("安徽省宣城市", regions)
        self.assertEqual(len(set(regions)), len(regions))
        self.assertEqual(len(app.directory_scan_regions("海南")), 19)
        self.assertIn("台湾省台北市", app.directory_scan_regions("台湾"))

    def test_directory_coverage_contains_every_province_level_region(self):
        expected = {
            "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
            "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
            "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
            "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆", "台湾",
            "香港", "澳门",
        }
        self.assertEqual(set(app.PROVINCE_CITY_MAP), expected)
        self.assertEqual(len(app.PROVINCE_CITY_MAP), 34)
        self.assertEqual(sum(map(len, app.PROVINCE_CITY_MAP.values())), 393)

        grouped = [
            province
            for group in app.PROVINCE_GROUPS
            for province in group["provinces"]
        ]
        self.assertEqual(set(grouped), expected)
        self.assertEqual(len(grouped), len(set(grouped)))

        full_names = {
            "北京": "北京市",
            "天津": "天津市",
            "上海": "上海市",
            "重庆": "重庆市",
            "内蒙古": "内蒙古自治区",
            "广西": "广西壮族自治区",
            "西藏": "西藏自治区",
            "宁夏": "宁夏回族自治区",
            "新疆": "新疆维吾尔自治区",
            "香港": "香港特别行政区",
            "澳门": "澳门特别行政区",
        }
        for province, cities in app.PROVINCE_CITY_MAP.items():
            query = full_names.get(province, f"{province}省")
            regions = app.directory_scan_regions(query)
            self.assertEqual(len(regions), len(cities), province)
            self.assertEqual(len(regions), len(set(regions)), province)
            self.assertTrue(all(region for region in regions), province)

    def test_directory_match_keeps_operators_and_rejects_vendors(self):
        sector = app.DIRECTORY_SECTOR_LIBRARY["wastewater"]

        accepted, hits = app.directory_match_quality(
            "合肥市十五里河污水处理厂",
            "公司企业;公共设施",
            sector,
        )
        rejected, _ = app.directory_match_quality(
            "安徽污水处理设备有限公司",
            "公司企业;环保设备销售",
            sector,
        )
        project_office, _ = app.directory_match_quality(
            "某建工集团污水处理厂项目部",
            "公司企业;建设施工",
            sector,
        )

        self.assertTrue(accepted)
        self.assertIn("污水处理厂", hits)
        self.assertFalse(rejected)
        self.assertFalse(project_office)

    @patch("app.collect_amap_leads")
    def test_directory_collection_scans_cities_deduplicates_and_reports_stats(self, collect_amap):
        collect_amap.return_value = (
            [
                app.Lead(
                    company="合肥十五里河污水处理有限公司",
                    region="安徽省 合肥市 包河区",
                    sector="污水处理厂/水质净化厂",
                    source="高德 POI",
                    score=70,
                    phone="0551-11111111",
                    direction="directory",
                ),
                app.Lead(
                    company="合肥十五里河污水处理有限责任公司",
                    region="安徽省 合肥市 包河区",
                    sector="污水处理厂/水质净化厂",
                    source="高德 POI 补充",
                    score=68,
                    email="contact@example.com",
                    direction="directory",
                ),
                app.Lead(
                    company="芜湖市城南污水处理厂",
                    region="安徽省 芜湖市 弋江区",
                    sector="污水处理厂/水质净化厂",
                    source="高德 POI",
                    score=66,
                    direction="directory",
                ),
            ],
            [],
        )

        result = app.collect_leads(
            {
                "direction": "directory",
                "directoryProvince": "安徽",
                "sectors": ["wastewater"],
                "collectionStrategy": "balanced",
                "amapKey": "test-key",
                "disableBaiduMap": True,
                "disableTianditu": True,
                "requireMap": True,
            }
        )

        scanned_regions = collect_amap.call_args.args[1]
        self.assertEqual(len(scanned_regions), 16)
        self.assertEqual(collect_amap.call_args.args[4:7], (1, 4, "directory"))
        self.assertEqual(result["meta"]["mode"], "directory")
        self.assertEqual(result["meta"]["province"], "安徽")
        self.assertEqual(result["meta"]["citiesScanned"], 16)
        self.assertEqual(result["meta"]["citiesWithResults"], 2)
        self.assertEqual(result["meta"]["companyCount"], 2)
        self.assertEqual(result["meta"]["phoneCount"], 1)
        self.assertEqual(result["meta"]["contactCount"], 1)
        city_stats = {item["city"]: item for item in result["meta"]["cityStats"]}
        self.assertEqual(city_stats["合肥市"]["count"], 1)
        self.assertEqual(city_stats["芜湖市"]["count"], 1)

    @patch("app.collect_amap_leads")
    def test_directory_collection_supports_guangdong_and_its_city_stats(self, collect_amap):
        collect_amap.return_value = (
            [
                app.Lead(
                    company="广州市净水有限公司",
                    region="广东省 广州市 天河区",
                    sector="污水处理厂/水质净化厂",
                    source="高德 POI",
                    score=72,
                    phone="020-12345678",
                    direction="directory",
                ),
                app.Lead(
                    company="深圳市水质净化有限公司",
                    region="广东省 深圳市 南山区",
                    sector="污水处理厂/水质净化厂",
                    source="高德 POI",
                    score=70,
                    direction="directory",
                ),
            ],
            [],
        )

        result = app.collect_leads(
            {
                "direction": "directory",
                "directoryProvince": "广东省",
                "sectors": ["wastewater"],
                "collectionStrategy": "balanced",
                "amapKey": "test-key",
                "disableBaiduMap": True,
                "disableTianditu": True,
                "requireMap": True,
            }
        )

        scanned_regions = collect_amap.call_args.args[1]
        self.assertEqual(len(scanned_regions), 21)
        self.assertIn("广东省广州市", scanned_regions)
        self.assertIn("广东省深圳市", scanned_regions)
        self.assertEqual(result["meta"]["province"], "广东")
        self.assertEqual(result["meta"]["citiesScanned"], 21)
        self.assertEqual(result["meta"]["citiesWithResults"], 2)
        self.assertEqual(result["meta"]["companyCount"], 2)
        self.assertEqual(result["meta"]["contactCount"], 1)
        city_stats = {item["city"]: item for item in result["meta"]["cityStats"]}
        self.assertEqual(city_stats["广州市"]["count"], 1)
        self.assertEqual(city_stats["深圳市"]["count"], 1)

    @patch("app.collect_amap_leads", return_value=([], []))
    def test_directory_collection_never_fabricates_fallback_companies(self, _collect_amap):
        result = app.collect_leads(
            {
                "direction": "directory",
                "directoryProvince": "安徽",
                "sectors": ["wastewater"],
                "collectionStrategy": "precision",
                "amapKey": "test-key",
                "disableBaiduMap": True,
                "disableTianditu": True,
                "requireMap": True,
            }
        )

        self.assertEqual(result["leads"], [])
        self.assertEqual(result["meta"]["mode"], "directory")
        self.assertEqual(result["meta"]["companyCount"], 0)
        self.assertIn("未发现", " ".join(result["errors"]))

    def test_health_status_reports_provider_presence_without_secret_values(self):
        with patch.dict(
            os.environ,
            {
                "AMAP_KEY": "private-amap-key",
                "BAIDU_MAP_AK": "private-baidu-key",
                "TIANDITU_TK": "private-tianditu-key",
                "BAIDU_SEARCH_API_KEY": "private-search-key",
            },
            clear=False,
        ):
            status = app.health_status()

        self.assertEqual(status["status"], "ok")
        self.assertTrue(status["mapProviders"]["amap"])
        self.assertTrue(status["mapProviders"]["baidu"])
        self.assertTrue(status["mapProviders"]["tianditu"])
        self.assertTrue(status["webSearchProviders"]["baiduQianfan"])
        self.assertNotIn("private-amap-key", str(status))
        self.assertNotIn("private-baidu-key", str(status))
        self.assertNotIn("private-tianditu-key", str(status))
        self.assertNotIn("private-search-key", str(status))

    def test_api_usage_quota_warns_and_enforces_qianfan_limit(self):
        with patch.dict(
            os.environ,
            {
                "AMAP_KEY": "private-amap-key",
                "BAIDU_SEARCH_API_KEY": "private-search-key",
            },
            clear=False,
        ):
            app.set_api_quota_limits({"amap": 10, "baidu_qianfan": 2})
            app.record_api_request("amap", 7)
            overview = {
                item["provider"]: item
                for item in app.api_usage_overview()
            }
            self.assertEqual(overview["amap"]["used"], 7)
            self.assertEqual(overview["amap"]["remaining"], 3)
            self.assertEqual(overview["amap"]["status"], "warning")

            app.reserve_qianfan_search_request()
            app.reserve_qianfan_search_request()
            with self.assertRaisesRegex(RuntimeError, "每日保护上限 2 次"):
                app.reserve_qianfan_search_request()

    def test_api_usage_dashboard_lists_current_and_future_platforms(self):
        app.record_api_request("baidu_map", 3)
        usage = {
            item["provider"]: item
            for item in app.dashboard_summary()["apiUsage"]
        }

        self.assertEqual(usage["baidu_map"]["used"], 3)
        self.assertIn("baidu_qianfan", usage)
        self.assertIn("amap", usage)
        self.assertIn("tianditu", usage)
        self.assertIn("aliyun_sms", usage)

        app.flush_api_usage_buffer()
        with app.API_USAGE_BUFFER_LOCK:
            self.assertEqual(app.API_USAGE_BUFFER, {})
        persisted = {
            item["provider"]: item
            for item in app.api_usage_overview()
        }
        self.assertEqual(persisted["baidu_map"]["used"], 3)

    def test_api_quota_rejects_unknown_platform(self):
        with self.assertRaisesRegex(ValueError, "未知 API 平台"):
            app.set_api_quota_limits({"unknown_service": 100})

    @patch("app.urlopen")
    def test_qianfan_web_search_uses_bearer_auth_and_maps_references(self, urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "request_id": "request-1",
                "references": [
                    {
                        "id": 1,
                        "type": "web",
                        "title": "测试环保科技有限公司官网",
                        "url": "https://example.com/about",
                        "content": "公司从事工业废水处理并公开联系电话。",
                        "date": "2026-07-20 10:00:00",
                    },
                    {
                        "id": 2,
                        "type": "image",
                        "title": "跳过图片",
                        "url": "https://example.com/image.jpg",
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response

        results = app.qianfan_web_search(
            "private-qianfan-key",
            "山东 工业废水处理 企业 官网",
            8,
        )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, app.BAIDU_SEARCH_ENDPOINT)
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer private-qianfan-key",
        )
        self.assertEqual(payload["search_source"], "baidu_search_v2")
        self.assertEqual(payload["resource_type_filter"][0]["top_k"], 8)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider"], "百度千帆网页搜索")
        self.assertEqual(results[0]["description"], "公司从事工业废水处理并公开联系电话。")

    @patch("app.time.sleep")
    @patch("app.urlopen")
    def test_qianfan_web_search_retries_one_transient_timeout(
        self,
        urlopen,
        _sleep,
    ):
        response = MagicMock()
        response.read.return_value = b'{"request_id":"retry-1","references":[]}'
        successful_open = MagicMock()
        successful_open.__enter__.return_value = response
        urlopen.side_effect = [TimeoutError("timed out"), successful_open]

        results = app.qianfan_web_search(
            "private-qianfan-key",
            "山东 工业废水处理 企业 官网",
            8,
        )

        self.assertEqual(results, [])
        self.assertEqual(urlopen.call_count, 2)

    @patch("app.urlopen")
    def test_qianfan_web_search_maps_site_operator_to_structured_filter(self, urlopen):
        response = MagicMock()
        response.read.return_value = b'{"request_id":"request-2","references":[]}'
        urlopen.return_value.__enter__.return_value = response

        app.qianfan_web_search(
            "private-qianfan-key",
            "site:douyin.com 山东 液体氯化钙",
            10,
        )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            payload["messages"][0]["content"],
            "山东 液体氯化钙",
        )
        self.assertEqual(
            payload["search_filter"]["match"]["site"],
            ["douyin.com"],
        )

    @patch("app.qianfan_web_search")
    def test_public_web_search_caches_qianfan_results(self, qianfan_search):
        qianfan_search.return_value = [
            {
                "title": "缓存测试企业官网",
                "url": "https://cache.example.com/",
                "description": "液体氯化钙采购",
                "date": "",
                "website": "",
                "provider": "百度千帆网页搜索",
            }
        ]
        with patch.dict(
            os.environ,
            {"BAIDU_SEARCH_API_KEY": "private-search-key"},
            clear=False,
        ):
            first, first_url = app.search_public_web("缓存测试企业 官网", 10)
            second, second_url = app.search_public_web("缓存测试企业 官网", 10)

        self.assertEqual(first, second)
        self.assertIn("baidu.com/s", first_url)
        self.assertEqual(first_url, second_url)
        self.assertEqual(qianfan_search.call_count, 1)
        self.assertEqual(app.qianfan_search_usage_today(), 1)

    @patch("app.fetch_html")
    @patch("app.qianfan_web_search", side_effect=RuntimeError("临时不可用"))
    def test_public_web_search_falls_back_when_qianfan_fails(
        self,
        _qianfan_search,
        fetch_html,
    ):
        fetch_html.return_value = """
        <ul>
          <li class="res-list">
            <h3><a>回退企业官网</a></h3>
            <a data-mdurl="https://fallback.example.com/"></a>
            <p class="res-desc">公开联系电话 0531-12345678</p>
          </li>
        </ul>
        """
        with patch.dict(
            os.environ,
            {"BAIDU_SEARCH_API_KEY": "invalid-temporary-key"},
            clear=False,
        ):
            results, query_url = app.search_public_web("回退企业 官网", 10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider"], "360公开网页索引")
        self.assertIn("so.com/s", query_url)

    @patch("app.search_public_web")
    def test_company_website_discovery_rejects_directory_and_accepts_brand_site(
        self,
        public_search,
    ):
        public_search.return_value = (
            [
                {
                    "title": "山东东岳化工有限公司",
                    "description": "企业信息和产品黄页",
                    "url": "https://www.huangye88.com/company/example/",
                },
                {
                    "title": "东岳氟硅科技集团有限公司",
                    "description": "产品中心、公司简介和联系我们",
                    "url": "https://www.dongyuechem.com/about/index.html",
                },
            ],
            "https://www.baidu.com/s?wd=test",
        )

        website = app.discover_company_website("山东东岳化工有限公司")

        self.assertEqual(website, "https://www.dongyuechem.com/")

    @patch("app.urlopen")
    def test_tianditu_search_uses_admin_region_and_zero_based_start(self, urlopen):
        response = MagicMock()
        response.read.return_value = (
            b'{"resultType":1,"count":0,"pois":[],'
            b'"status":{"infocode":1000,"cndesc":"OK"}}'
        )
        urlopen.return_value.__enter__.return_value = response

        result = app.tianditu_search("test-tk", "山东省济南市", "水处理公司", 2)

        request = urlopen.call_args.args[0]
        params = parse_qs(urlparse(request.full_url).query)
        post_data = json.loads(params["postStr"][0])
        self.assertEqual(post_data["queryType"], 12)
        self.assertEqual(post_data["specify"], "山东省济南市")
        self.assertEqual(post_data["start"], 20)
        self.assertEqual(post_data["show"], 2)
        self.assertEqual(params["tk"], ["test-tk"])
        self.assertEqual(result["status"]["infocode"], 1000)

    @patch("app.tianditu_search")
    def test_tianditu_collection_maps_company_phone_and_location(self, search):
        search.return_value = {
            "resultType": 1,
            "status": {"infocode": 1000, "cndesc": "OK"},
            "pois": [
                {
                    "hotPointID": "tdt-001",
                    "name": "济南工业水处理有限公司",
                    "province": "山东省",
                    "city": "济南市",
                    "county": "历城区",
                    "address": "工业北路1号",
                    "phone": "0531-88888888",
                    "lonlat": "117.1,36.7",
                    "typeName": "公司企业",
                }
            ],
        }

        leads, errors = app.collect_tianditu_leads(
            "test-tk",
            ["山东省济南市"],
            {"water": app.SECTOR_LIBRARY["water"]},
            [],
            1,
            1,
            "downstream",
            True,
            True,
            precision_mode=True,
        )

        self.assertEqual(errors, [])
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].source, "天地图 POI")
        self.assertEqual(leads[0].phone, "0531-88888888")
        self.assertEqual(leads[0].poi_id, "tdt-001")
        self.assertEqual(leads[0].location, "117.1,36.7")

    @patch("app.urlopen")
    def test_baidu_map_search_uses_v3_region_and_zero_based_page(self, urlopen):
        response = MagicMock()
        response.read.return_value = b'{"status":0,"message":"ok","results":[]}'
        urlopen.return_value.__enter__.return_value = response

        result = app.baidu_map_search("test-ak", "山东省济南市", "水处理公司", 2)

        request = urlopen.call_args.args[0]
        self.assertIn("/place/v3/region?", request.full_url)
        self.assertIn("page_num=1", request.full_url)
        self.assertIn("region_limit=true", request.full_url)
        self.assertEqual(result["status"], 0)

    @patch("app.baidu_map_search")
    def test_baidu_map_collection_maps_company_phone_and_location(self, baidu_search):
        baidu_search.return_value = {
            "status": 0,
            "message": "ok",
            "results": [
                {
                    "uid": "baidu-001",
                    "name": "济南工业水处理有限公司",
                    "province": "山东省",
                    "city": "济南市",
                    "area": "历城区",
                    "address": "工业北路1号",
                    "telephone": "0531-88888888",
                    "location": {"lat": 36.7, "lng": 117.1},
                    "detail_info": {
                        "classified_poi_tag": "公司企业;水处理",
                        "new_alias": "济南工业水处理",
                    },
                }
            ],
        }

        leads, errors = app.collect_baidu_map_leads(
            "test-ak",
            ["山东省济南市"],
            {"water": app.SECTOR_LIBRARY["water"]},
            [],
            1,
            1,
            "downstream",
            True,
            True,
            precision_mode=True,
        )

        self.assertEqual(errors, [])
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].source, "百度地图 POI")
        self.assertEqual(leads[0].phone, "0531-88888888")
        self.assertEqual(leads[0].poi_id, "baidu-001")
        self.assertEqual(leads[0].location, "117.1,36.7")
        self.assertEqual(leads[0].alias, "济南工业水处理")

    def test_map_sources_merge_contacts_and_evidence(self):
        amap_lead = app.Lead(
            company="双源水处理有限公司",
            region="山东 济南",
            sector="工业水处理",
            source="高德 POI",
            score=50,
            phone="0531-11111111",
            address="工业路1号",
        )
        baidu_lead = app.Lead(
            company="双源水处理有限责任公司",
            region="山东省 济南市",
            sector="工业水处理",
            source="百度地图 POI",
            score=52,
            phone="0531-22222222",
            address="工业路1号",
        )

        merged = app.merge_map_leads([amap_lead], [baidu_lead])

        self.assertEqual(len(merged), 1)
        self.assertIn("高德 POI", merged[0].source)
        self.assertIn("百度地图 POI", merged[0].source)
        self.assertIn("0531-11111111", merged[0].phone)
        self.assertIn("0531-22222222", merged[0].phone)
        self.assertEqual(merged[0].evidence_count, 2)

    @patch("app.collect_baidu_map_leads")
    def test_collect_leads_can_run_with_only_baidu_map(self, collect_baidu):
        collect_baidu.return_value = (
            [
                app.Lead(
                    company="百度单源买家有限公司",
                    region="山东",
                    sector="工业水处理",
                    source="百度地图 POI",
                    score=50,
                    phone="0531-66666666",
                )
            ],
            [],
        )

        result = app.collect_leads(
            {
                "direction": "downstream",
                "regions": ["山东"],
                "sectors": ["water"],
                "baiduMapAk": "test-ak",
                "disableAmap": True,
                "pages": 1,
                "fastMode": True,
                "requireMap": True,
            }
        )

        self.assertEqual(result["meta"]["mode"], "baidu")
        self.assertEqual(result["meta"]["mapSources"], ["百度地图"])
        self.assertEqual(result["meta"]["companyCount"], 1)
        self.assertEqual(result["leads"][0]["company"], "百度单源买家有限公司")

    def test_baidu_map_error_messages_are_actionable(self):
        self.assertIn("IP 白名单", app.baidu_map_error_message(210, "APP IP校验失败"))
        self.assertIn("地点检索", app.baidu_map_error_message(240, "服务未开通"))
        self.assertIn("额度", app.baidu_map_error_message(302, "配额不足"))

    @patch("app.collect_tianditu_leads")
    def test_collect_leads_can_run_with_only_tianditu(self, collect_tianditu):
        collect_tianditu.return_value = (
            [
                app.Lead(
                    company="天地图单源买家有限公司",
                    region="山东",
                    sector="工业水处理",
                    source="天地图 POI",
                    score=50,
                    phone="0531-55555555",
                )
            ],
            [],
        )

        result = app.collect_leads(
            {
                "direction": "downstream",
                "regions": ["山东"],
                "sectors": ["water"],
                "tiandituTk": "test-tk",
                "disableAmap": True,
                "disableBaiduMap": True,
                "pages": 1,
                "fastMode": True,
                "requireMap": True,
            }
        )

        self.assertEqual(result["meta"]["mode"], "tianditu")
        self.assertEqual(result["meta"]["mapSources"], ["天地图"])
        self.assertEqual(result["meta"]["companyCount"], 1)
        self.assertEqual(result["leads"][0]["company"], "天地图单源买家有限公司")

    def test_tianditu_error_messages_are_actionable(self):
        self.assertIn("参数", app.tianditu_error_message(2001, "Parameter Invalid"))
        self.assertIn("分页", app.tianditu_error_message(2007, "count over"))
        self.assertIn("暂时异常", app.tianditu_error_message(3000, "Server error"))

    @patch("app.discover_company_website", return_value="")
    @patch("app.baidu_map_search")
    def test_company_website_discovery_accepts_baidu_without_amap(
        self,
        baidu_search,
        _discover_website,
    ):
        baidu_search.return_value = {
            "status": 0,
            "results": [
                {
                    "name": "百度候选水务有限公司",
                    "province": "山东省",
                    "city": "济南市",
                    "area": "历城区",
                    "address": "工业路2号",
                    "telephone": "0531-77777777",
                    "detail_info": {"classified_poi_tag": "公司企业;水务"},
                }
            ],
        }

        leads, errors, requests = app.collect_company_website_notices(
            "",
            "test-ak",
            "",
            ["山东"],
            {"water": app.PROCUREMENT_SECTOR_LIBRARY["water_treatment"]},
            [],
            ["purchase"],
            "10d",
        )

        self.assertEqual(leads, [])
        self.assertEqual(errors, [])
        self.assertGreater(requests, 0)
        self.assertTrue(baidu_search.called)

    @patch("app.discover_company_website", return_value="")
    @patch("app.tianditu_search")
    def test_company_website_discovery_accepts_tianditu_only(
        self,
        tianditu_search,
        _discover_website,
    ):
        tianditu_search.return_value = {
            "resultType": 1,
            "status": {"infocode": 1000, "cndesc": "OK"},
            "pois": [
                {
                    "name": "天地图候选水务有限公司",
                    "province": "山东省",
                    "city": "济南市",
                    "county": "历城区",
                    "address": "工业路3号",
                    "phone": "0531-55556666",
                    "typeName": "公司企业",
                }
            ],
        }

        leads, errors, requests = app.collect_company_website_notices(
            "",
            "",
            "test-tk",
            ["山东"],
            {"water": app.PROCUREMENT_SECTOR_LIBRARY["water_treatment"]},
            [],
            ["purchase"],
            "10d",
        )

        self.assertEqual(leads, [])
        self.assertEqual(errors, [])
        self.assertGreater(requests, 0)
        self.assertTrue(tianditu_search.called)

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

    def test_social_platform_identification_uses_exact_domain_boundaries(self):
        platform_id, platform = app.identify_social_platform(
            "https://www.douyin.com/video/123"
        )
        self.assertEqual(platform_id, "douyin")
        self.assertEqual(platform["name"], "抖音")
        self.assertEqual(
            app.identify_social_platform("https://douyin.com.evil.example/video/123"),
            (None, None),
        )
        self.assertEqual(
            app.identify_social_platform("javascript:alert(1)"),
            (None, None),
        )
        self.assertEqual(
            app.social_account_from_text("山东液体氯化钙 - 抖音", "抖音"),
            "",
        )
        self.assertEqual(
            app.social_account_from_text(
                "山东海合化工有限公司于20241223发布在抖音", "抖音"
            ),
            "山东海合化工有限公司",
        )
        self.assertEqual(
            app.social_account_from_text(
                "山东液体氯化钙价格，就找润弘化工，库存充足", "抖音"
            ),
            "润弘化工",
        )
        self.assertEqual(
            app.social_public_phones("联系济南坤丰化工13275413810"),
            "13275413810",
        )
        self.assertEqual(
            app.social_engagement_from_text("7130个喜欢，4659次观看"),
            "7130个喜欢；4659次观看",
        )

    def test_social_metadata_and_link_import_are_platform_specific(self):
        page = """
        <html><head>
          <meta property="og:title" content="山东清源化工发布的液体氯化钙供应视频 - 抖音">
          <meta property="og:description" content="副产液钙，浓度30%，山东潍坊可发槽车">
          <meta name="author" content="山东清源化工">
        </head></html>
        """
        with patch("app.fetch_html", return_value=page):
            lead, warning = app.social_link_lead(
                "https://www.douyin.com/video/123",
                app.selected_sectors(["liquid_calcium", "byproduct"], "social"),
                [],
            )

        self.assertEqual(warning, "")
        self.assertEqual(lead.social_platform_id, "douyin")
        self.assertEqual(lead.social_platform, "抖音")
        self.assertEqual(lead.social_account, "山东清源化工")
        self.assertIn("液体氯化钙", lead.social_matched_keywords)
        prepared = app.prepare_lead_payload(lead)
        self.assertEqual(prepared["quality_grade"], "B")
        self.assertTrue(prepared["actionable"])

    def test_social_collection_merges_same_account_but_separates_platforms(self):
        douyin_key = app.lead_dedupe_key(
            {
                "company": "山东清源化工",
                "direction": "social",
                "social_platform_id": "douyin",
                "social_account": "山东清源化工",
            }
        )
        kuaishou_key = app.lead_dedupe_key(
            {
                "company": "山东清源化工",
                "direction": "social",
                "social_platform_id": "kuaishou",
                "social_account": "山东清源化工",
            }
        )
        self.assertNotEqual(douyin_key, kuaishou_key)
        result_page = """
        <li class="res-list"><h3><a>山东清源化工的视频 - 抖音</a></h3>
        <a data-mdurl="https://www.douyin.com/video/1"></a>
        <p class="res-desc">山东液体氯化钙供应，副产液钙处置</p></li>
        <li class="res-list"><h3><a>山东清源化工的视频 - 抖音</a></h3>
        <a data-mdurl="https://www.douyin.com/video/2"></a>
        <p class="res-desc">液体氯化钙槽车供应</p></li>
        """
        with patch("app.fetch_html", return_value=result_page):
            leads, errors, requests = app.collect_social_leads(
                ["山东"],
                app.selected_sectors(["liquid_calcium", "byproduct"], "social"),
                ["douyin"],
                [],
                [],
                "precision",
            )

        self.assertEqual(errors, [])
        self.assertEqual(requests, 1)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].social_platform, "抖音")
        self.assertEqual(leads[0].evidence_count, 2)

    def test_collect_leads_social_mode_returns_platform_metadata(self):
        mock_lead = app.Lead(
            company="清源化工",
            region="山东",
            sector="液体氯化钙供需",
            source="抖音公开索引",
            score=48,
            direction="social",
            search_url="https://www.douyin.com/video/1",
            match_reason="液体氯化钙",
            social_platform="抖音",
            social_platform_id="douyin",
            social_account="清源化工",
            social_discovery_method="公开索引监控",
            evidence_count=1,
        )
        with patch(
            "app.collect_social_leads",
            return_value=([mock_lead], [], 1),
        ):
            result = app.collect_leads(
                {
                    "direction": "social",
                    "regions": ["山东"],
                    "sectors": ["liquid_calcium"],
                    "socialPlatforms": ["douyin"],
                }
            )

        self.assertEqual(result["meta"]["mode"], "social")
        self.assertEqual(result["meta"]["socialPlatforms"], ["douyin"])
        self.assertEqual(result["leads"][0]["social_platform"], "抖音")

    def test_social_intent_classification_is_explainable(self):
        purchase = app.classify_social_intent(
            "长期求购液体氯化钙，每月500吨，槽车运输", "13800138000"
        )
        disposal = app.classify_social_intent(
            "环氧氯丙烷工艺副产液钙，长期外售处置"
        )
        fluoride = app.classify_social_intent(
            "含氟废水氟化物超标，需要除氟改造"
        )

        self.assertEqual(purchase["id"], "purchase")
        self.assertEqual(purchase["role"], "buyer")
        self.assertGreaterEqual(purchase["score"], 70)
        self.assertIn("求购", purchase["reasons"])
        self.assertEqual(disposal["id"], "disposal")
        self.assertEqual(disposal["role"], "supplier")
        self.assertEqual(fluoride["id"], "fluoride_need")

    def test_legacy_social_lead_is_enriched_when_read(self):
        app.save_leads(
            [
                {
                    "company": "历史液钙账号",
                    "direction": "social",
                    "region": "河南",
                    "sector": "液体氯化钙供需",
                    "source": "抖音公开索引",
                    "score": 48,
                    "project_title": "焦作副产液体氯化钙长期外售处置",
                    "social_platform": "抖音",
                    "social_account": "历史液钙账号",
                    "match_reason": "副产液钙公开内容",
                }
            ]
        )

        saved = app.list_saved_leads({"q": "历史液钙账号"})[0]

        self.assertEqual(saved["social_intent_id"], "disposal")
        self.assertGreaterEqual(saved["social_intent_score"], 70)
        self.assertEqual(saved["opportunity_role"], "supplier")
        self.assertEqual(saved["social_entity_status"], "待确认")

    def test_social_negative_keyword_filters_public_result(self):
        result_page = """
        <li class="res-list"><h3><a>液体氯化钙实验教学 - 抖音</a></h3>
        <a data-mdurl="https://www.douyin.com/video/teaching"></a>
        <p class="res-desc">个人分享化学试剂实验教学</p></li>
        """
        with patch("app.fetch_html", return_value=result_page):
            leads, errors, _ = app.collect_social_leads(
                ["山东"],
                app.selected_sectors(["liquid_calcium"], "social"),
                ["douyin"],
                [],
                [],
                "precision",
                negative_keywords=["实验教学", "个人分享"],
            )

        self.assertEqual(leads, [])
        self.assertEqual(errors, [])

    def test_social_feedback_persists_and_teaches_exact_url(self):
        lead = {
            "company": "山东清源化工",
            "direction": "social",
            "region": "山东",
            "sector": "液体氯化钙供需",
            "source": "抖音公开索引",
            "score": 50,
            "search_url": "https://www.douyin.com/video/feedback-test",
            "social_platform": "抖音",
            "social_platform_id": "douyin",
            "social_account": "山东清源化工",
            "match_reason": "液体氯化钙求购",
        }
        app.save_leads([lead])
        saved = app.list_saved_leads({"q": "山东清源化工"})[0]

        updated = app.update_social_feedback(saved["id"], "irrelevant")
        rules = app.social_feedback_rules()

        self.assertEqual(updated["feedback_status"], "irrelevant")
        self.assertEqual(updated["quality_grade"], "D")
        self.assertIn(
            "https://www.douyin.com/video/feedback-test",
            rules["excluded_urls"],
        )

    def test_confirm_social_entity_updates_company_profile(self):
        lead = {
            "company": "待识别账号",
            "direction": "social",
            "region": "山东",
            "sector": "副产液钙/处置",
            "source": "快手公开索引",
            "score": 44,
            "search_url": "https://www.kuaishou.com/short-video/entity-test",
            "social_platform": "快手",
            "social_platform_id": "kuaishou",
            "social_account": "待识别账号",
            "match_reason": "副产液钙处置",
        }
        app.save_leads([lead])
        saved = app.list_saved_leads({"direction": "social"})[0]

        confirmed = app.confirm_social_entity(
            saved["id"], "山东清源环保科技有限公司"
        )

        self.assertEqual(confirmed["company"], "山东清源环保科技有限公司")
        self.assertEqual(confirmed["social_entity_status"], "已确认")
        self.assertEqual(confirmed["quality_grade"], "A")
        self.assertIn("qcc.com", confirmed["qcc_url"])

    def test_persist_search_result_attaches_saved_ids(self):
        result = {
            "leads": [
                app.prepare_lead_payload(
                    {
                        "company": "测试社媒企业",
                        "direction": "social",
                        "source": "微博公开索引",
                        "score": 46,
                        "search_url": "https://weibo.com/123/test",
                        "social_platform": "微博",
                        "social_platform_id": "weibo",
                        "social_account": "测试社媒企业",
                        "match_reason": "液体氯化钙采购",
                    }
                )
            ],
            "meta": {"mode": "social"},
        }

        persistence = app.persist_search_result(result)

        self.assertEqual(persistence["created"], 1)
        self.assertGreater(result["leads"][0]["id"], 0)

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

    def test_system_events_deduplicate_unresolved_identical_messages(self):
        for _ in range(2):
            app.log_system_event(
                "warning",
                "collection",
                "同一数据源响应超时",
                source="回归测试源",
            )

        with app.database_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM system_events"
            ).fetchone()[0]

        self.assertEqual(count, 1)

    def test_external_access_errors_are_compacted_and_classified_as_info(self):
        errors = app.compact_external_access_errors(
            [
                "甲公司官网：<urlopen error timed out>",
                "乙公司官网：HTTP Error 403: Forbidden",
            ],
            "企业官网",
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("2 个外部请求", errors[0])
        self.assertIn("已自动跳过", errors[0])
        self.assertEqual(app.collection_event_level(errors[0], True), "info")
        self.assertEqual(
            app.collection_event_level("采购平台已跳过本轮该来源", True),
            "info",
        )
        self.assertEqual(app.collection_event_level(errors[0], False), "error")

    @patch("app.time.sleep")
    @patch("app.fetch_json", side_effect=TimeoutError("timed out"))
    def test_procurement_probe_stops_timeout_storm(self, fetch_json, _sleep):
        leads, errors, requests = app.collect_procurement_companies(
            ["山东"],
            {"water": app.PROCUREMENT_SECTOR_LIBRARY["water_treatment"]},
            ["液体氯化钙"],
            ["purchase"],
            "10d",
            keyword_limit=6,
        )

        self.assertEqual(leads, [])
        self.assertEqual(requests, 1)
        self.assertEqual(fetch_json.call_count, 2)
        self.assertEqual(len(errors), 1)
        self.assertIn("已跳过本轮该来源", errors[0])

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
        self.assertFalse(monitor["running"])

        app.MONITOR_RUNNING.add(monitor_id)
        running_monitor = next(
            item for item in app.list_monitors() if item["id"] == monitor_id
        )
        self.assertTrue(running_monitor["running"])

    def test_monitor_new_lead_notification_can_open_saved_detail(self):
        monitor_id = app.save_monitor(
            "测试监控",
            {"direction": "downstream", "regions": ["east"], "sectors": ["water"]},
            24,
        )
        result = app.save_leads(
            [
                {
                    "company": "测试新线索有限公司",
                    "direction": "downstream",
                    "region": "山东",
                    "sector": "水处理",
                    "phone": "0531-12345678",
                }
            ],
            monitor_id=monitor_id,
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
        self.assertEqual(notification["monitor_id"], monitor_id)

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
        self.assertIsNone(notification)

    def test_manual_bulk_save_does_not_create_notification_noise(self):
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
            notification_count = connection.execute(
                "SELECT COUNT(*) FROM notifications"
            ).fetchone()[0]
            orphan_count = connection.execute(
                """
                SELECT COUNT(*) FROM notifications AS notification
                LEFT JOIN leads AS lead ON lead.id = notification.lead_id
                WHERE notification.lead_id IS NOT NULL AND lead.id IS NULL
                """
            ).fetchone()[0]
        self.assertEqual(notification_count, 0)
        self.assertEqual(orphan_count, 0)

    def test_dashboard_ignores_legacy_manual_new_lead_notifications(self):
        app.save_leads(
            [
                {
                    "company": "历史手动提醒测试有限公司",
                    "direction": "downstream",
                    "region": "山东",
                    "sector": "水处理",
                }
            ]
        )
        with app.DATABASE_LOCK, app.database_connection() as connection:
            lead_id = connection.execute(
                "SELECT id FROM leads WHERE company = ?",
                ("历史手动提醒测试有限公司",),
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO notifications (
                    type, title, message, lead_id, is_read, created_at
                ) VALUES ('new_lead', '历史提醒', '手动采集旧提醒', ?, 0, ?)
                """,
                (lead_id, app.now_iso()),
            )

        self.assertEqual(app.dashboard_summary()["unreadNotifications"], 0)

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

    def test_sales_fields_survive_later_collection_and_show_history(self):
        source_lead = {
            "company": "销售资料持久化水务有限公司",
            "direction": "downstream",
            "region": "山东",
            "sector": "水处理",
            "source": "高德 POI",
            "score": 48,
            "phone": "0531-77778888",
        }
        app.save_leads([source_lead])
        saved = app.list_saved_leads({"q": "销售资料持久化"})[0]

        updated = app.update_lead_sales_record(
            {
                "id": saved["id"],
                "salesStatus": "qualified",
                "owner": "王销售",
                "nextFollowUp": "2026-07-28T09:30",
                "notes": "需要30%液钙，先寄样。",
                "opportunityRole": "buyer",
                "liquidConcentration": "30%",
                "monthlyVolume": "300吨/月",
                "impurityProfile": "铁离子需低于约定指标",
                "logisticsRadius": "250公里",
                "storageCondition": "具备液体储罐",
                "commercialValue": "60万元/年",
            }
        )
        app.save_leads([{**source_lead, "source": "百度地图 POI"}])
        recollected = app.get_saved_lead(saved["id"])

        self.assertEqual(updated["sales_status"], "qualified")
        self.assertEqual(recollected["liquid_concentration"], "30%")
        self.assertEqual(recollected["monthly_volume"], "300吨/月")
        self.assertEqual(recollected["commercial_value"], "60万元/年")
        self.assertIn("高德 POI", recollected["source"])
        self.assertIn("百度地图 POI", recollected["source"])
        self.assertEqual(recollected["activity_history"][0]["action"], "update")

    def test_dashboard_profiles_only_count_managed_companies(self):
        app.save_leads(
            [
                {
                    "company": "尚未跟进线索有限公司",
                    "direction": "downstream",
                    "source": "高德 POI",
                    "score": 45,
                },
                {
                    "company": "高潜待联系线索有限公司",
                    "direction": "downstream",
                    "source": "百度地图 POI",
                    "score": 70,
                    "phone": "0531-66667777",
                },
            ]
        )
        managed = app.list_saved_leads({"q": "高潜待联系"})[0]
        app.update_lead_sales_record(
            {
                "id": managed["id"],
                "salesStatus": "contacted",
                "owner": "销售甲",
            }
        )

        dashboard = app.dashboard_summary()

        self.assertEqual(dashboard["total"], 2)
        self.assertEqual(dashboard["profileCount"], 1)
        self.assertEqual(dashboard["salesWorkspace"]["hotUncontacted"], 0)

    def test_bounded_int_uses_defaults_and_limits(self):
        self.assertEqual(app.bounded_int("invalid", 4, 1, 8), 4)
        self.assertEqual(app.bounded_int("99", 4, 1, 8), 8)
        self.assertEqual(app.bounded_int("-5", 4, 1, 8), 1)

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
    def test_procurement_does_not_save_search_entries_as_companies(self, collect_procurement):
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

        self.assertEqual(result["leads"], [])
        self.assertIn("不再保存为公司线索", result["meta"]["noResultsReason"])
        self.assertGreater(len(result["meta"]["manualSearches"]), 0)

    def test_procurement_results_merge_by_company_and_keep_richer_contact(self):
        leads = app.merge_procurement_company_leads(
            [
                app.Lead(
                    company="山东某水务集团有限公司",
                    region="山东",
                    sector="采购公告",
                    source="全国公共资源交易平台",
                    score=70,
                    project_title="2026年液体氯化钙采购项目",
                    notice_date="2026-07-20",
                    search_url="https://example.com/notice-1",
                    direction="procurement",
                ),
                app.Lead(
                    company="山东某水务集团有限公司",
                    region="山东",
                    sector="招标公告",
                    source="中国政府采购网",
                    score=76,
                    phone="0531-88888888",
                    address="济南市工业北路",
                    project_title="液体氯化钙年度框架招标",
                    notice_date="2026-07-22",
                    search_url="https://example.com/notice-2",
                    direction="procurement",
                ),
            ]
        )

        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].company, "山东某水务集团有限公司")
        self.assertEqual(leads[0].phone, "0531-88888888")
        self.assertEqual(leads[0].project_title, "液体氯化钙年度框架招标")
        self.assertEqual(leads[0].evidence_count, 2)
        self.assertIn("全国公共资源交易平台", leads[0].source)
        self.assertIn("中国政府采购网", leads[0].source)

    def test_procurement_generic_search_label_is_not_a_concrete_company(self):
        lead = app.prepare_lead_payload(
            {
                "company": "液体氯化钙 · 采购公告",
                "direction": "procurement",
                "source": "中国政府采购网 / 全国公共资源交易平台",
                "confidence": "官方检索",
                "score": 84,
                "match_reason": "采购公告",
            }
        )

        self.assertFalse(app.procurement_company_is_specific(lead["company"]))
        self.assertEqual(lead["quality_grade"], "D")

    @patch("app.collect_procurement_companies")
    def test_procurement_drops_notices_without_specific_buyer(self, collect_procurement):
        collect_procurement.return_value = (
            [
                app.Lead(
                    company="采购单位待核验",
                    region="山东",
                    sector="采购公告",
                    source="中国政府采购网",
                    score=72,
                    project_title="液体氯化钙采购项目",
                    notice_date="2026-07-22",
                    search_url="https://example.com/unresolved-notice",
                    direction="procurement",
                )
            ],
            [],
            1,
        )

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

        self.assertEqual(result["leads"], [])
        self.assertEqual(result["meta"]["companyCount"], 0)
        self.assertIn("暂未发现可确认采购单位", result["meta"]["noResultsReason"])

    def test_procurement_persistence_uses_one_company_profile(self):
        first = {
            "company": "山东采购归并水务集团有限公司",
            "direction": "procurement",
            "source": "全国公共资源交易平台",
            "score": 70,
            "project_title": "液体氯化钙采购项目",
            "notice_date": "2026-07-20",
        }
        second = {
            **first,
            "source": "中国政府采购网",
            "phone": "0531-66668888",
            "project_title": "液体氯化钙年度招标",
            "notice_date": "2026-07-22",
        }

        first_result = app.save_leads([first])
        second_result = app.save_leads([second])
        saved = app.list_saved_leads(
            {"q": "山东采购归并水务集团", "direction": "procurement"}
        )

        self.assertEqual(first_result["created"], 1)
        self.assertEqual(second_result["updated"], 1)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["phone"], "0531-66668888")

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

    @patch("app.collect_environmental_permits")
    def test_environmental_precision_limits_slow_permit_details(self, collect_permits):
        collect_permits.return_value = ([], [], 0)

        app.collect_leads(
            {
                "direction": "environmental",
                "regions": ["江西"],
                "sectors": ["fluorochemicals"],
                "environmentalSources": ["permit"],
                "collectionStrategy": "precision",
                "pages": 1,
            }
        )

        self.assertEqual(collect_permits.call_args.args[-3:], (4, 12, 4))

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
