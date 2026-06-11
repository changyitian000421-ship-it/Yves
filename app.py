#!/usr/bin/env python3
"""
Calcium chloride buyer lead finder for the China market.

Run:
    python3 app.py

Then open:
    http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import random
import re
import secrets
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen

import certifi


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DEFAULT_PORT = 8765
DEFAULT_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
SESSION_MAX_AGE = 12 * 60 * 60
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_WINDOW = 10 * 60
LOGIN_LIMIT = 8
AMAP_WORKERS = max(1, min(int(os.getenv("AMAP_WORKERS", "4")), 8))
AMAP_RETRY_CODES = {"10015", "10016", "10019", "10020", "10021"}
SEARCH_JOBS: dict[str, dict[str, Any]] = {}
SEARCH_JOBS_LOCK = threading.Lock()
SEARCH_JOB_TTL = 60 * 60


SECTOR_LIBRARY: dict[str, dict[str, Any]] = {
    "snow": {
        "name": "融雪剂/除冰剂",
        "keywords": ["融雪剂", "除冰剂", "道路养护", "公路养护", "环卫服务"],
        "uses": "冬季融雪、除冰、市政道路和高速养护",
        "pitch": "冬季备货、颗粒粒径稳定、含量稳定、发货时效",
        "score": 26,
    },
    "desiccant": {
        "name": "干燥剂/吸湿材料",
        "keywords": ["干燥剂厂家", "吸湿剂", "集装箱干燥剂", "防潮剂", "除湿剂"],
        "uses": "干燥剂、吸湿剂、防潮包原料",
        "pitch": "吸湿性能、白度、包装规格、杂质控制",
        "score": 23,
    },
    "water": {
        "name": "水处理/污水处理",
        "keywords": ["水处理药剂", "污水处理", "循环水处理", "环保科技", "净水材料"],
        "uses": "水处理药剂、污水处理辅助材料",
        "pitch": "纯度、重金属控制、稳定供货、吨袋/小袋包装",
        "score": 18,
    },
    "concrete": {
        "name": "混凝土外加剂/建材",
        "keywords": ["混凝土外加剂", "速凝剂", "建材助剂", "砂浆外加剂", "建筑材料"],
        "uses": "混凝土早强、建材助剂、工程材料",
        "pitch": "含量、溶解速度、批次稳定、工程项目供货能力",
        "score": 17,
    },
    "oilfield": {
        "name": "油田/钻井液",
        "keywords": ["钻井液", "油田化学品", "完井液", "石油助剂", "采油助剂"],
        "uses": "钻井液、完井液、油田化学品体系",
        "pitch": "工业级指标、杂质控制、长期合同、吨位供应",
        "score": 20,
    },
    "coldchain": {
        "name": "冷库/制冷/盐水冷却",
        "keywords": ["冷库工程", "制冷设备", "盐水制冷", "冷链设备", "工业制冷"],
        "uses": "盐水冷却、制冷系统辅助介质",
        "pitch": "浓度方案、腐蚀控制、桶装/吨袋、就近配送",
        "score": 15,
    },
    "trader": {
        "name": "化工贸易商/经销商",
        "keywords": ["化工原料", "化工贸易", "化工产品经销", "化工供应链", "化学试剂"],
        "uses": "二级分销、区域贸易、客户配套供货",
        "pitch": "价格阶梯、库存、账期、区域保护、样品支持",
        "score": 14,
    },
}

UPSTREAM_SECTOR_LIBRARY: dict[str, dict[str, Any]] = {
    "rare_earth": {
        "name": "稀土分离/冶炼",
        "keywords": ["稀土分离", "稀土冶炼", "稀土氧化物", "稀土湿法冶金"],
        "process": "盐酸体系浸出、萃取或中和过程中，含钙中和剂可能形成氯化钙母液。",
        "pitch": "核实盐酸用量、石灰/石灰石中和工段、母液浓度、杂质和月产生量。",
        "indicators": ["稀土", "分离", "冶炼", "氧化物", "湿法"],
        "strict_indicators": ["稀土"],
        "confidence": "高",
        "score": 32,
    },
    "epichlorohydrin": {
        "name": "环氧氯丙烷",
        "keywords": ["环氧氯丙烷生产", "氯醇法环氧氯丙烷", "甘油法环氧氯丙烷", "氯碱环氧氯丙烷"],
        "process": "氯醇化/皂化等工段使用石灰时，可形成含氯化钙的工艺废水或副产液。",
        "pitch": "优先确认工艺路线、皂化剂、液钙浓度、COD和有机氯等杂质。",
        "indicators": ["环氧氯丙烷", "氯醇", "氯碱"],
        "strict_indicators": ["环氧氯丙烷", "氯醇法", "甘油法"],
        "confidence": "高",
        "score": 36,
    },
    "fly_ash": {
        "name": "飞灰水洗/资源化",
        "keywords": ["飞灰水洗", "焚烧飞灰资源化", "飞灰资源综合利用", "生活垃圾飞灰处理"],
        "process": "飞灰水洗液及后续酸碱调节、除杂处理中，可能形成高盐含钙氯化物溶液。",
        "pitch": "确认水洗盐水去向、钙氯浓度、重金属指标、蒸发结晶或外售处置方式。",
        "indicators": ["飞灰", "水洗", "焚烧", "资源化"],
        "strict_indicators": ["飞灰", "焚烧"],
        "confidence": "高",
        "score": 34,
    },
    "tungsten": {
        "name": "钨湿法冶炼",
        "keywords": ["钨冶炼", "仲钨酸铵", "钨湿法冶金", "钨资源综合利用"],
        "process": "钨湿法冶炼的酸分解、中和或钙盐转化工段可能产生含氯化钙溶液。",
        "pitch": "确认盐酸/氯化钙体系、石灰中和、钨钼磷砷等杂质及液体副产量。",
        "indicators": ["钨", "仲钨酸铵", "湿法", "冶炼"],
        "strict_indicators": ["钨", "仲钨酸铵"],
        "confidence": "高",
        "score": 32,
    },
    "soda_ash": {
        "name": "氨碱法纯碱",
        "keywords": ["氨碱法纯碱", "纯碱生产", "联碱纯碱", "制碱工业"],
        "process": "氨碱法蒸氨母液典型含氯化钙，是大宗液体氯化钙潜在线索。",
        "pitch": "先核实是否为氨碱法，再确认蒸氨废液浓度、氨氮、盐分和综合利用现状。",
        "indicators": ["纯碱", "制碱", "氨碱"],
        "strict_indicators": ["纯碱", "制碱", "氨碱"],
        "confidence": "高",
        "score": 38,
    },
    "pharma": {
        "name": "医药/农药中间体",
        "keywords": ["医药中间体生产", "原料药生产", "农药中间体", "精细化工中间体"],
        "process": "含氯或盐酸工艺废酸经石灰中和时，可能形成含有机物的氯化钙溶液。",
        "pitch": "核实具体产品和中和工艺，重点关注COD、色度、残留溶剂及危废属性。",
        "indicators": ["医药", "原料药", "农药", "中间体", "制药"],
        "strict_indicators": ["医药", "原料药", "农药", "制药"],
        "confidence": "中",
        "score": 24,
    },
    "new_energy": {
        "name": "新能源材料/电池回收",
        "keywords": ["锂电材料生产", "动力电池回收", "新能源材料湿法", "三元前驱体生产"],
        "process": "湿法浸出、酸洗和石灰中和工段可能形成含钙氯盐溶液，需按具体路线核实。",
        "pitch": "确认是否使用盐酸、石灰中和以及镍钴锰锂等金属杂质和废液处置方式。",
        "indicators": ["锂电", "电池", "新能源材料", "三元", "湿法"],
        "strict_indicators": ["锂电", "动力电池", "新能源材料", "三元"],
        "confidence": "中",
        "score": 23,
    },
    "fluorine": {
        "name": "含氟新材料",
        "keywords": ["含氟新材料生产", "氟化工", "氟盐生产", "含氟废水处理"],
        "process": "含氟/含酸废水采用钙法处理时可能伴随高氯含钙母液，具体成分需核验。",
        "pitch": "确认盐酸来源、钙法除氟、中和母液及氟离子和其他杂质指标。",
        "indicators": ["氟", "氟化工", "氟盐", "含氟"],
        "strict_indicators": ["氟化工", "氟盐", "含氟"],
        "confidence": "中",
        "score": 22,
    },
    "chlorinated_chemicals": {
        "name": "氯化精细化工",
        "keywords": ["有机氯化工", "氯化精细化工", "氯代中间体", "副产盐酸化工"],
        "process": "氯化反应副产盐酸或含酸废液经石灰中和时，可能形成液体氯化钙。",
        "pitch": "核实副产盐酸量、石灰中和工段、液钙有机杂质和稳定产生周期。",
        "indicators": ["氯化", "氯代", "有机氯", "精细化工"],
        "strict_indicators": ["氯化", "氯代", "有机氯"],
        "confidence": "中",
        "score": 25,
    },
    "pickling": {
        "name": "酸洗/金属表面处理",
        "keywords": ["盐酸酸洗", "金属表面处理", "钢材酸洗", "废盐酸资源化"],
        "process": "盐酸酸洗废液若采用石灰中和，会形成含氯化钙盐水，但也可能以氯化亚铁为主。",
        "pitch": "先核实废酸成分和处理路线，排查铁离子、重金属及是否具备液钙利用价值。",
        "indicators": ["酸洗", "表面处理", "废盐酸", "钢材"],
        "strict_indicators": ["酸洗", "废盐酸"],
        "confidence": "待核验",
        "score": 18,
    },
}

UPSTREAM_SUPPLIER_WORDS = ["氯化钙", "融雪剂", "干燥剂", "化工原料", "化工贸易", "经销"]
UPSTREAM_INDUSTRIAL_WORDS = [
    "公司",
    "集团",
    "有限",
    "厂",
    "工业",
    "生产",
    "制造",
    "冶炼",
    "材料",
    "资源",
    "科技",
    "化工",
    "环保",
    "项目",
    "基地",
    "园区",
]
UPSTREAM_CONSUMER_WORDS = [
    "餐饮",
    "茶饮",
    "饭店",
    "酒店",
    "宾馆",
    "超市",
    "商店",
    "便利店",
    "锅饼",
    "单饼",
    "食品",
    "洗衣",
    "洗鞋",
    "地坪",
    "涂料",
    "建材门市",
    "装饰",
    "培训",
    "学校",
]


REGION_PRESETS: dict[str, list[str]] = {
    "north": ["北京", "天津", "河北", "山西", "内蒙古"],
    "northeast": ["辽宁", "吉林", "黑龙江"],
    "east": ["上海", "江苏", "浙江", "安徽", "福建", "山东", "江西"],
    "central": ["河南", "湖北", "湖南"],
    "south": ["广东", "广西", "海南"],
    "southwest": ["重庆", "四川", "贵州", "云南", "西藏"],
    "northwest": ["陕西", "甘肃", "青海", "宁夏", "新疆"],
}


PROCUREMENT_KEYWORDS = [
    "氯化钙",
    "融雪剂",
    "除冰剂",
    "道路养护 融雪剂",
    "水处理 氯化钙",
    "干燥剂 氯化钙",
]


POSITIVE_WORDS = [
    "融雪",
    "除冰",
    "干燥剂",
    "吸湿",
    "防潮",
    "水处理",
    "污水",
    "钻井",
    "油田",
    "完井",
    "外加剂",
    "速凝",
    "冷库",
    "制冷",
    "化工",
    "原料",
    "经销",
    "贸易",
    "道路养护",
    "环卫",
]


@dataclass
class Lead:
    company: str
    region: str
    sector: str
    source: str
    score: int
    phone: str = ""
    address: str = ""
    website: str = ""
    use_case: str = ""
    pitch: str = ""
    match_reason: str = ""
    status: str = "未联系"
    search_url: str = ""
    raw_type: str = ""
    qcc_url: str = ""
    alias: str = ""
    email: str = ""
    company_website: str = ""
    poi_id: str = ""
    location: str = ""
    updated_at: str = ""
    direction: str = "downstream"
    process_basis: str = ""
    confidence: str = ""


def json_response(handler: SimpleHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def normalize_regions(input_regions: list[str] | str | None) -> list[str]:
    if not input_regions:
        return ["山东", "河北", "辽宁", "吉林", "黑龙江", "江苏"]
    raw_parts = [input_regions] if isinstance(input_regions, str) else input_regions
    parts = [
        part
        for raw_part in raw_parts
        for part in re.split(r"[,，、;\s]+", str(raw_part))
    ]

    regions: list[str] = []
    for part in parts:
        value = str(part).strip()
        if not value:
            continue
        if value in REGION_PRESETS:
            regions.extend(REGION_PRESETS[value])
        else:
            regions.append(value)
    return list(dict.fromkeys(regions))


def get_sector_library(direction: str) -> dict[str, dict[str, Any]]:
    return UPSTREAM_SECTOR_LIBRARY if direction == "upstream" else SECTOR_LIBRARY


def selected_sectors(ids: list[str] | None, direction: str) -> dict[str, dict[str, Any]]:
    library = get_sector_library(direction)
    if not ids:
        ids = (
            ["rare_earth", "epichlorohydrin", "fly_ash", "tungsten", "soda_ash"]
            if direction == "upstream"
            else ["snow", "desiccant", "water", "concrete", "trader"]
        )
    return {sector_id: library[sector_id] for sector_id in ids if sector_id in library}


def lead_score(name: str, raw_type: str, base: int, has_phone: bool) -> tuple[int, str]:
    text = f"{name} {raw_type}"
    hits = [word for word in POSITIVE_WORDS if word in text]
    score = base + min(32, len(hits) * 6)
    if has_phone:
        score += 16
    if any(word in text for word in ["市政", "环卫", "公路", "高速", "道路养护"]):
        score += 10
    if any(word in text for word in ["厂家", "制造", "实业", "科技", "材料"]):
        score += 6
    return min(score, 100), "、".join(hits) if hits else "行业关键词匹配"


def upstream_lead_score(
    name: str,
    raw_type: str,
    sector: dict[str, Any],
    has_phone: bool,
) -> tuple[int, str, str]:
    text = f"{name} {raw_type}"
    hits = [word for word in sector["indicators"] if word in text]
    score = int(sector["score"]) + min(30, len(hits) * 10)
    if has_phone:
        score += 12
    if any(word in text for word in ["生产", "制造", "冶炼", "材料", "资源", "工业"]):
        score += 8
    confidence = sector["confidence"]
    if not hits:
        confidence = "待核验"
    reason = "、".join(hits) if hits else "仅搜索关键词命中，需核验实际工艺"
    return min(score, 100), reason, confidence


def upstream_match_quality(
    name: str,
    raw_type: str,
    sector: dict[str, Any],
) -> tuple[bool, bool]:
    text = f"{name} {raw_type}"
    if any(word in text for word in UPSTREAM_CONSUMER_WORDS):
        return False, False

    strict_indicators = sector.get("strict_indicators") or sector["indicators"]
    indicator_hit = any(word in text for word in strict_indicators)
    industrial_hit = any(word in text for word in UPSTREAM_INDUSTRIAL_WORDS)
    return True, indicator_hit and industrial_hit


def amap_search(
    key: str,
    city: str,
    keyword: str,
    page: int,
    offset: int = 20,
    timeout: int = 12,
) -> dict[str, Any]:
    query = urlencode(
        {
            "key": key,
            "keywords": keyword,
            "city": city,
            "citylimit": "true",
            "offset": str(offset),
            "page": str(page),
            "extensions": "all",
            "output": "json",
        }
    )
    url = f"https://restapi.amap.com/v3/place/text?{query}"
    req = Request(url, headers={"User-Agent": "BuyerLeadFinder/1.0"})
    for attempt in range(5):
        with urlopen(req, timeout=timeout, context=DEFAULT_SSL_CONTEXT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rate_limited = (
            data.get("infocode") in AMAP_RETRY_CODES
            or "QPS" in str(data.get("info") or "")
        )
        if not rate_limited:
            return data
        time.sleep((0.65 * (2**attempt)) + random.uniform(0.1, 0.55))
    return data


def build_search_links(company_type: str, region: str, keyword: str) -> dict[str, str]:
    query = quote(f"{region} {keyword} {company_type}")
    company_query = quote(company_type)
    return {
        "baidu": f"https://www.baidu.com/s?wd={query}",
        "amap": f"https://www.amap.com/search?query={query}",
        "ccgp": f"https://search.ccgp.gov.cn/bxsearch?searchtype=1&kw={quote(keyword)}",
        "qcc": f"https://www.qcc.com/web/search?key={company_query}",
    }


def as_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item)
    return str(value or "").strip()


def first_text(value: Any) -> str:
    if isinstance(value, list):
        return str(next((item for item in value if item), "")).strip()
    return str(value or "").strip()


def fallback_leads(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    direction: str,
) -> list[Lead]:
    leads: list[Lead] = []
    for region in regions:
        for sector in sectors.values():
            keywords = list(sector["keywords"])
            for custom in custom_keywords:
                if custom and custom not in keywords:
                    keywords.append(custom)
            for keyword in keywords[:6]:
                links = build_search_links(sector["name"], region, keyword)
                leads.append(
                    Lead(
                        company=f"开发任务：{region} - {keyword}",
                        region=region,
                        sector=sector["name"],
                        source="开发任务",
                        score=int(sector["score"]) + 18,
                        use_case=sector.get("uses") or sector.get("process", ""),
                        pitch=sector["pitch"],
                        match_reason=f"建议批量搜索：{region} + {keyword}",
                        search_url=links["baidu"],
                        website=links["amap"],
                        qcc_url=links["qcc"],
                        direction=direction,
                        process_basis=sector.get("process", ""),
                        confidence=sector.get("confidence", ""),
                    )
                )
    return leads


def collect_amap_leads(
    amap_key: str,
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    pages: int,
    keyword_limit: int,
    direction: str,
    exclude_suppliers: bool,
    strict_upstream: bool,
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str]]:
    leads: list[Lead] = []
    errors: list[str] = []
    seen: set[str] = set()
    pages = max(1, min(pages, 10))
    jobs: list[tuple[str, dict[str, Any], str, int]] = []
    for region in regions:
        for sector in sectors.values():
            keywords = list(sector["keywords"])
            for custom in custom_keywords:
                custom = custom.strip()
                if custom and custom not in keywords:
                    keywords.insert(0, custom)
            keywords = list(dict.fromkeys(keywords))[:keyword_limit]
            for keyword in keywords:
                for page in range(1, pages + 1):
                    jobs.append((region, sector, keyword, page))

    with ThreadPoolExecutor(max_workers=AMAP_WORKERS) as executor:
        future_jobs = {
            executor.submit(amap_search, amap_key, region, keyword, page): (region, sector, keyword, page)
            for region, sector, keyword, page in jobs
        }
        completed = 0
        total = len(jobs)
        if progress_callback:
            progress_callback(completed, total, len(leads), 0, "正在连接高德数据服务")
        for future in as_completed(future_jobs):
            region, sector, keyword, page = future_jobs[future]
            try:
                data = future.result()
            except Exception as exc:  # noqa: BLE001 - show concise collection errors to user.
                errors.append(f"{region}/{keyword}/第{page}页：{exc}")
                completed += 1
                if progress_callback:
                    progress_callback(
                        completed,
                        total,
                        len(leads),
                        len([lead for lead in leads if lead.phone]),
                        f"{region} · {keyword} · 第{page}页",
                    )
                continue

            if data.get("status") != "1":
                info = data.get("info") or "高德接口返回失败"
                errors.append(f"{region}/{keyword}：{info}")
                completed += 1
                if progress_callback:
                    progress_callback(
                        completed,
                        total,
                        len(leads),
                        len([lead for lead in leads if lead.phone]),
                        f"{region} · {keyword} · 第{page}页",
                    )
                continue

            for poi in data.get("pois") or []:
                name = str(poi.get("name") or "").strip()
                if not name:
                    continue
                province = as_text(poi.get("pname"))
                city_name = as_text(poi.get("cityname"))
                district = as_text(poi.get("adname"))
                region_label = " ".join(part for part in [province, city_name, district] if part) or region
                address = as_text(poi.get("address"))
                phone = as_text(poi.get("tel")).replace(";", " / ")
                raw_type = as_text(poi.get("type"))
                if direction == "upstream":
                    upstream_text = f"{name} {raw_type}"
                    if exclude_suppliers and any(word in upstream_text for word in UPSTREAM_SUPPLIER_WORDS):
                        continue
                    allowed, strong_match = upstream_match_quality(name, raw_type, sector)
                    if not allowed or (strict_upstream and not strong_match):
                        continue
                alias = as_text(poi.get("alias"))
                email = as_text(poi.get("email"))
                company_website = first_text(poi.get("website"))
                poi_id = as_text(poi.get("id"))
                location = as_text(poi.get("location"))
                updated_at = as_text(poi.get("timestamp"))
                dedupe_key = f"{name}|{address}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                if direction == "upstream":
                    score, reason, confidence = upstream_lead_score(
                        name,
                        raw_type,
                        sector,
                        bool(phone),
                    )
                else:
                    score, reason = lead_score(name, raw_type, int(sector["score"]), bool(phone))
                    confidence = ""
                links = build_search_links(name, region, keyword)
                leads.append(
                    Lead(
                        company=name,
                        region=region_label,
                        sector=sector["name"],
                        source="高德 POI",
                        score=score,
                        phone=phone,
                        address=address,
                        website=links["baidu"],
                        use_case=sector.get("uses") or sector.get("process", ""),
                        pitch=sector["pitch"],
                        match_reason=(
                            f"{keyword}；工艺线索：{reason}"
                            if direction == "upstream"
                            else f"{keyword}；{reason}"
                        ),
                        search_url=links["amap"],
                        raw_type=raw_type,
                        qcc_url=links["qcc"],
                        alias=alias,
                        email=email,
                        company_website=company_website,
                        poi_id=poi_id,
                        location=location,
                        updated_at=updated_at,
                        direction=direction,
                        process_basis=sector.get("process", ""),
                        confidence=confidence,
                    )
                )
            completed += 1
            if progress_callback:
                progress_callback(
                    completed,
                    total,
                    len(leads),
                    len([lead for lead in leads if lead.phone]),
                    f"{region} · {keyword} · 第{page}页",
                )
    return leads, errors


def procurement_links(regions: list[str]) -> list[Lead]:
    leads: list[Lead] = []
    for keyword in PROCUREMENT_KEYWORDS:
        region_text = "、".join(regions[:4]) + ("等" if len(regions) > 4 else "")
        leads.append(
            Lead(
                company=f"{keyword} 采购公告监控",
                region=region_text,
                sector="招投标/采购",
                source="采购监控入口",
                score=72 if "氯化钙" in keyword else 66,
                use_case="发现公开采购需求、中标单位、历史成交价格",
                pitch="优先联系采购单位、代理机构和历史中标供应商",
                match_reason=f"每天检索关键词：{keyword}",
                search_url=f"https://search.ccgp.gov.cn/bxsearch?searchtype=1&kw={quote(keyword)}",
                website=f"https://www.baidu.com/s?wd={quote(keyword + ' 采购 招标 中标')}",
                qcc_url=f"https://www.baidu.com/s?wd={quote(keyword + ' 中标 供应商 公司')}",
            )
        )
    return leads


def collect_leads(payload: dict[str, Any], progress_callback: Any = None) -> dict[str, Any]:
    direction = "upstream" if payload.get("direction") == "upstream" else "downstream"
    regions = normalize_regions(payload.get("regions"))
    sectors = selected_sectors(payload.get("sectors"), direction)
    custom_keywords = [
        item.strip()
        for item in re.split(r"[,，\n]+", str(payload.get("customKeywords") or ""))
        if item.strip()
    ]
    pages = int(payload.get("pages") or 1)
    fast_mode = bool(payload.get("fastMode", True))
    keyword_limit = 2 if fast_mode else 8
    amap_key = str(os.getenv("AMAP_KEY") or payload.get("amapKey") or "").strip()
    require_amap = bool(payload.get("requireAmap"))
    exclude_suppliers = bool(payload.get("excludeSuppliers", True))
    strict_upstream = bool(payload.get("strictUpstream", True))

    errors: list[str] = []
    if require_amap and not amap_key:
        leads = []
        errors.append("要显示具体公司和电话，必须填写高德 Web 服务 API Key；否则只能生成开发任务清单。")
    elif amap_key:
        leads, errors = collect_amap_leads(
            amap_key,
            regions,
            sectors,
            custom_keywords,
            pages,
            keyword_limit,
            direction,
            exclude_suppliers,
            strict_upstream,
            progress_callback,
        )
        if not leads:
            leads = fallback_leads(regions, sectors, custom_keywords, direction)
            errors.append("未采集到高德结果，已生成搜索任务清单。")
    else:
        leads = fallback_leads(regions, sectors, custom_keywords, direction)

    if payload.get("includeProcurement", True) and not require_amap and direction == "downstream":
        leads.extend(procurement_links(regions))

    leads = sorted(leads, key=lambda item: item.score, reverse=True)
    return {
        "leads": [asdict(lead) for lead in leads],
        "errors": errors[:40],
        "meta": {
            "count": len(leads),
            "companyCount": len([lead for lead in leads if lead.source == "高德 POI"]),
            "phoneCount": len([lead for lead in leads if lead.phone]),
            "requestCount": len(regions) * sum(
                min(keyword_limit, len(item["keywords"]) + len(custom_keywords))
                for item in sectors.values()
            ) * max(1, min(pages, 10)),
            "workers": AMAP_WORKERS,
            "fastMode": fast_mode,
            "direction": direction,
            "excludeSuppliers": exclude_suppliers,
            "strictUpstream": strict_upstream,
            "regions": regions,
            "sectors": [item["name"] for item in sectors.values()],
            "mode": "amap" if amap_key else "need_key" if require_amap else "task",
        },
    }


def update_search_job(
    job_id: str,
    completed: int,
    total: int,
    company_count: int,
    phone_count: int,
    current: str,
) -> None:
    percent = round((completed / total) * 100) if total else 0
    with SEARCH_JOBS_LOCK:
        job = SEARCH_JOBS.get(job_id)
        if not job:
            return
        job.update(
            {
                "completed": completed,
                "total": total,
                "percent": min(percent, 99),
                "companyCount": company_count,
                "phoneCount": phone_count,
                "current": current,
            }
        )


def run_search_job(job_id: str, payload: dict[str, Any]) -> None:
    try:
        result = collect_leads(
            payload,
            lambda completed, total, companies, phones, current: update_search_job(
                job_id,
                completed,
                total,
                companies,
                phones,
                current,
            ),
        )
        with SEARCH_JOBS_LOCK:
            job = SEARCH_JOBS.get(job_id)
            if job:
                job.update(
                    {
                        "status": "completed",
                        "completed": result["meta"].get("requestCount", 0),
                        "total": result["meta"].get("requestCount", 0),
                        "percent": 100,
                        "companyCount": result["meta"].get("companyCount", 0),
                        "phoneCount": result["meta"].get("phoneCount", 0),
                        "current": "采集完成",
                        "result": result,
                    }
                )
    except Exception as exc:  # noqa: BLE001 - return concise job failure to the UI.
        with SEARCH_JOBS_LOCK:
            job = SEARCH_JOBS.get(job_id)
            if job:
                job.update(
                    {
                        "status": "failed",
                        "current": "采集失败",
                        "error": str(exc),
                    }
                )


def cleanup_search_jobs() -> None:
    cutoff = time.time() - SEARCH_JOB_TTL
    with SEARCH_JOBS_LOCK:
        expired = [
            job_id
            for job_id, job in SEARCH_JOBS.items()
            if job.get("createdAt", 0) < cutoff
        ]
        for job_id in expired:
            SEARCH_JOBS.pop(job_id, None)


def csv_bytes(leads: list[dict[str, Any]]) -> bytes:
    output = io.StringIO()
    fieldnames = [
        "score",
        "company",
        "region",
        "sector",
        "phone",
        "address",
        "use_case",
        "pitch",
        "match_reason",
        "status",
        "source",
        "raw_type",
        "search_url",
        "website",
        "qcc_url",
        "alias",
        "email",
        "company_website",
        "poi_id",
        "location",
        "updated_at",
        "direction",
        "process_basis",
        "confidence",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


class AppHandler(SimpleHTTPRequestHandler):
    def client_id(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",", 1)[0].strip() or self.client_address[0]

    def session_token(self) -> str:
        expires = str(int(time.time()) + SESSION_MAX_AGE)
        nonce = secrets.token_urlsafe(12)
        payload = f"{expires}.{nonce}"
        signature = hmac.new(
            SESSION_SECRET.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return base64.urlsafe_b64encode(f"{payload}.{signature}".encode("utf-8")).decode("ascii")

    def authenticated(self) -> bool:
        raw_cookie = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
            token = cookie.get("buyer_session")
            if not token:
                return False
            decoded = base64.urlsafe_b64decode(token.value.encode("ascii")).decode("utf-8")
            expires, nonce, signature = decoded.split(".", 2)
            if int(expires) < int(time.time()):
                return False
            payload = f"{expires}.{nonce}"
            expected = hmac.new(
                SESSION_SECRET.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(signature, expected)
        except (ValueError, TypeError, base64.binascii.Error):
            return False

    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def login_allowed(self) -> bool:
        now = time.time()
        client = self.client_id()
        attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(client, []) if now - stamp < LOGIN_WINDOW]
        LOGIN_ATTEMPTS[client] = attempts
        return len(attempts) < LOGIN_LIMIT

    def record_login_failure(self) -> None:
        LOGIN_ATTEMPTS.setdefault(self.client_id(), []).append(time.time())

    def is_public_path(self, path: str) -> bool:
        return path in {"/login", "/login.html", "/styles.css", "/login.js", "/api/login", "/health"}

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        if parsed.path == "/":
            return str(STATIC_DIR / "index.html")
        return str(STATIC_DIR / parsed.path.lstrip("/"))

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
        path = urlparse(self.path).path
        if path == "/health":
            json_response(self, {"status": "ok", "version": "progress-company-profile-v1"})
            return
        if path in {"/login", "/login.html"}:
            if self.authenticated():
                self.redirect("/")
                return
            self.path = "/login.html"
            return super().do_GET()
        if path in {"/styles.css", "/login.js"}:
            return super().do_GET()
        if not self.authenticated():
            if path.startswith("/api/"):
                json_response(self, {"error": "请先登录"}, 401)
            else:
                self.redirect("/login")
            return
        if path == "/api/config":
            json_response(
                self,
                {
                    "sectors": SECTOR_LIBRARY,
                    "downstreamSectors": SECTOR_LIBRARY,
                    "upstreamSectors": UPSTREAM_SECTOR_LIBRARY,
                    "regionPresets": REGION_PRESETS,
                    "hasEnvAmapKey": bool(os.getenv("AMAP_KEY")),
                },
            )
            return
        if path == "/api/search/status":
            query = urlparse(self.path).query
            params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
            job_id = params.get("id", "")
            with SEARCH_JOBS_LOCK:
                job = SEARCH_JOBS.get(job_id)
                payload = dict(job) if job else None
            if not payload:
                json_response(self, {"error": "采集任务不存在或已过期"}, 404)
                return
            payload.pop("createdAt", None)
            json_response(self, payload)
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            json_response(self, {"error": "JSON 格式错误"}, 400)
            return

        path = urlparse(self.path).path
        if path == "/api/login":
            if not APP_PASSWORD:
                json_response(self, {"error": "服务器尚未配置 APP_PASSWORD"}, 503)
                return
            if not self.login_allowed():
                json_response(self, {"error": "尝试次数过多，请十分钟后再试"}, 429)
                return
            password = str(payload.get("password") or "")
            if not hmac.compare_digest(password, APP_PASSWORD):
                self.record_login_failure()
                json_response(self, {"error": "密码错误"}, 401)
                return
            LOGIN_ATTEMPTS.pop(self.client_id(), None)
            body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
            secure = self.headers.get("X-Forwarded-Proto") == "https"
            cookie = (
                f"buyer_session={self.session_token()}; Path=/; HttpOnly; "
                f"SameSite=Strict; Max-Age={SESSION_MAX_AGE}"
            )
            if secure:
                cookie += "; Secure"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/logout":
            self.send_response(200)
            self.send_header("Set-Cookie", "buyer_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if not self.authenticated():
            json_response(self, {"error": "请先登录"}, 401)
            return

        if path == "/api/search/start":
            cleanup_search_jobs()
            job_id = uuid.uuid4().hex
            with SEARCH_JOBS_LOCK:
                SEARCH_JOBS[job_id] = {
                    "status": "running",
                    "completed": 0,
                    "total": 0,
                    "percent": 0,
                    "companyCount": 0,
                    "phoneCount": 0,
                    "current": "正在准备采集任务",
                    "result": None,
                    "error": "",
                    "createdAt": time.time(),
                }
            threading.Thread(
                target=run_search_job,
                args=(job_id, payload),
                daemon=True,
            ).start()
            json_response(self, {"jobId": job_id}, 202)
            return

        if path == "/api/search":
            json_response(self, collect_leads(payload))
            return

        if path == "/api/export":
            leads = payload.get("leads") or []
            data = csv_bytes(leads)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="calcium-chloride-leads.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        json_response(self, {"error": "Not found"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if not APP_PASSWORD:
        print("WARNING: APP_PASSWORD is not set. Login will remain disabled.")
    if not os.getenv("AMAP_KEY"):
        print("WARNING: AMAP_KEY is not set. Company collection will remain disabled.")
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Calcium chloride buyer finder running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
