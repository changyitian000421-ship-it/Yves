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
import html
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
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlparse
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
LOGIN_PHONES = {
    re.sub(r"\D", "", phone).removeprefix("86")
    for phone in re.split(r"[,，;\s]+", os.getenv("LOGIN_PHONES") or os.getenv("LOGIN_PHONE", ""))
    if phone.strip()
}
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_WINDOW = 10 * 60
LOGIN_LIMIT = 8
SMS_DEV_MODE = os.getenv("SMS_DEV_MODE", "").lower() in {"1", "true", "yes", "on"}
SMS_CODE_TTL = 5 * 60
SMS_SEND_COOLDOWN = 60
SMS_SEND_LIMIT = 5
SMS_CODES: dict[str, dict[str, Any]] = {}
SMS_SEND_ATTEMPTS: dict[str, list[float]] = {}
SMS_LOCK = threading.Lock()
ALIYUN_SMS_ACCESS_KEY_ID = os.getenv("ALIYUN_SMS_ACCESS_KEY_ID", "")
ALIYUN_SMS_ACCESS_KEY_SECRET = os.getenv("ALIYUN_SMS_ACCESS_KEY_SECRET", "")
ALIYUN_SMS_SIGN_NAME = os.getenv("ALIYUN_SMS_SIGN_NAME", "")
ALIYUN_SMS_TEMPLATE_CODE = os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "")
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


PROCUREMENT_SECTOR_LIBRARY: dict[str, dict[str, Any]] = {
    "calcium_chloride": {
        "name": "氯化钙直接采购",
        "keywords": ["氯化钙", "无水氯化钙", "二水氯化钙"],
        "company_keywords": ["化工原料采购", "水处理药剂", "油田化学品"],
        "uses": "直接发现氯化钙采购、询价、框架协议和成交公告",
        "pitch": "优先核对采购数量、含量、形态、包装、交货地和报名截止时间",
        "score": 82,
    },
    "liquid_calcium_chloride": {
        "name": "液体氯化钙",
        "keywords": ["液体氯化钙", "液钙采购", "氯化钙溶液"],
        "company_keywords": ["污水处理", "矿业公司", "油田化学品", "工业水处理"],
        "uses": "发现液体氯化钙采购、询价、供应商招募和长期供货需求",
        "pitch": "重点核对液钙浓度、杂质、月用量、储罐卸货条件和运输半径",
        "score": 84,
    },
    "deicing": {
        "name": "融雪/除冰采购",
        "keywords": ["融雪剂", "除冰剂", "道路融雪", "公路养护融雪剂"],
        "company_keywords": ["环卫服务公司", "道路养护公司", "公路养护", "机场集团"],
        "uses": "监控市政、环卫、公路和机场冬季融雪物资需求",
        "pitch": "重点确认氯盐配方、环保指标、采购吨位、供货周期和入围条件",
        "score": 76,
    },
    "water_treatment": {
        "name": "水处理药剂采购",
        "keywords": ["水处理氯化钙", "污水处理药剂", "工业水处理药剂"],
        "company_keywords": ["污水处理厂", "水务集团", "环保科技", "工业水处理"],
        "uses": "发现污水厂、工业园和环保项目的含钙药剂需求",
        "pitch": "核对使用工段、技术指标、年用量、供应商资质和配送频次",
        "score": 68,
    },
    "desiccant": {
        "name": "干燥剂原料采购",
        "keywords": ["干燥剂氯化钙", "集装箱干燥剂原料", "吸湿剂采购"],
        "company_keywords": ["干燥剂厂家", "集装箱干燥剂", "吸湿剂厂家"],
        "uses": "发现干燥剂、吸湿剂和防潮产品的原料采购需求",
        "pitch": "核对含量、白度、粒径、吸湿率、包装和代工要求",
        "score": 66,
    },
    "industrial_chemicals": {
        "name": "工业化学品采购",
        "keywords": ["氯盐采购", "工业盐类采购", "化工原料框架采购"],
        "company_keywords": ["化工集团", "矿业集团", "钢铁集团", "制造集团"],
        "uses": "监控大型企业化工原料年度框架和集中采购项目",
        "pitch": "关注供应商入库、账期、运输资质、年度预计量和价格联动条款",
        "score": 62,
    },
    "upstream_disposal": {
        "name": "副产液钙/废液处置",
        "keywords": ["液体氯化钙处置", "副产氯化钙综合利用", "含钙废液处置", "副产盐酸综合利用"],
        "company_keywords": ["稀土冶炼", "环氧氯丙烷", "飞灰资源化", "钨业公司"],
        "uses": "发现副产液钙、含钙盐水和副产盐酸综合利用或处置项目",
        "pitch": "核实物料属性、月产生量、浓度杂质、危废属性和装运条件",
        "score": 72,
    },
}

PROCUREMENT_NOTICE_TYPES: dict[str, str] = {
    "purchase": "采购公告",
    "tender": "招标公告",
    "award": "中标/成交结果",
}

PROCUREMENT_DATE_WINDOWS: dict[str, tuple[str, int]] = {
    "3d": ("近3天", 3),
    "10d": ("近10天", 10),
    "30d": ("近30天", 30),
    "90d": ("近90天", 90),
}


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
    project_title: str = ""
    notice_date: str = ""
    contact_name: str = ""
    agency: str = ""
    deadline: str = ""
    budget: str = ""


def json_response(handler: SimpleHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def normalize_phone(value: Any) -> str:
    phone = re.sub(r"\D", "", str(value or ""))
    if phone.startswith("86") and len(phone) == 13:
        phone = phone[2:]
    return phone


def valid_login_phone(phone: str) -> bool:
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        return False
    return phone in LOGIN_PHONES if LOGIN_PHONES else SMS_DEV_MODE


def sms_configured() -> bool:
    return all(
        [
            ALIYUN_SMS_ACCESS_KEY_ID,
            ALIYUN_SMS_ACCESS_KEY_SECRET,
            ALIYUN_SMS_SIGN_NAME,
            ALIYUN_SMS_TEMPLATE_CODE,
        ]
    )


def aliyun_percent_encode(value: Any) -> str:
    return quote(str(value), safe="~")


def send_aliyun_sms(phone: str, code: str) -> None:
    params = {
        "AccessKeyId": ALIYUN_SMS_ACCESS_KEY_ID,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": "cn-hangzhou",
        "SignName": ALIYUN_SMS_SIGN_NAME,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "TemplateCode": ALIYUN_SMS_TEMPLATE_CODE,
        "TemplateParam": json.dumps({"code": code}, ensure_ascii=False, separators=(",", ":")),
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Version": "2017-05-25",
    }
    canonicalized = "&".join(
        f"{aliyun_percent_encode(key)}={aliyun_percent_encode(params[key])}"
        for key in sorted(params)
    )
    string_to_sign = f"GET&%2F&{aliyun_percent_encode(canonicalized)}"
    digest = hmac.new(
        f"{ALIYUN_SMS_ACCESS_KEY_SECRET}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    params["Signature"] = base64.b64encode(digest).decode("ascii")
    url = "https://dysmsapi.aliyuncs.com/?" + "&".join(
        f"{aliyun_percent_encode(key)}={aliyun_percent_encode(params[key])}"
        for key in sorted(params)
    )
    req = Request(url, headers={"User-Agent": "CalciumLeadFinder/1.0"})
    with urlopen(req, timeout=15, context=DEFAULT_SSL_CONTEXT) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("Code") != "OK":
        raise RuntimeError(str(result.get("Message") or result.get("Code") or "短信发送失败"))


def code_digest(phone: str, code: str) -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        f"{phone}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


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
    if direction == "upstream":
        return UPSTREAM_SECTOR_LIBRARY
    if direction == "procurement":
        return PROCUREMENT_SECTOR_LIBRARY
    return SECTOR_LIBRARY


def selected_sectors(ids: list[str] | None, direction: str) -> dict[str, dict[str, Any]]:
    library = get_sector_library(direction)
    if not ids:
        if direction == "upstream":
            ids = ["rare_earth", "epichlorohydrin", "fly_ash", "tungsten", "soda_ash"]
        elif direction == "procurement":
            ids = ["calcium_chloride", "liquid_calcium_chloride", "deicing", "upstream_disposal"]
        else:
            ids = ["snow", "desiccant", "water", "concrete", "trader"]
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
            if str(data.get("status")) != "1":
                info = str(data.get("info") or "UNKNOWN_ERROR")
                if info == "USER_DAILY_QUERY_OVER_LIMIT":
                    raise RuntimeError("高德 API 今日调用额度已用完，请明日重试或更换 Key")
                raise RuntimeError(f"高德 API 返回错误：{info}")
            return data
        time.sleep((0.65 * (2**attempt)) + random.uniform(0.1, 0.55))
    raise RuntimeError(f"高德 API 请求频率受限：{data.get('info') or '请稍后重试'}")


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


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self.current_href = ""
        self.current_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self.current_href = next((value or "" for key, value in attrs if key.lower() == "href"), "")
        self.current_parts = []

    def handle_data(self, data: str) -> None:
        if self.current_href:
            value = re.sub(r"\s+", " ", data).strip()
            if value:
                self.current_parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current_href:
            self.links.append(
                {
                    "href": self.current_href.strip(),
                    "text": " ".join(self.current_parts).strip(),
                }
            )
            self.current_href = ""
            self.current_parts = []


def html_text(value: str) -> str:
    parser = TextExtractor()
    parser.feed(html.unescape(value))
    return " ".join(parser.parts)


def html_links(value: str) -> list[dict[str, str]]:
    parser = LinkExtractor()
    parser.feed(html.unescape(value))
    return parser.links


def normalize_website(value: str) -> str:
    website = value.strip()
    if not website:
        return ""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    parsed = urlparse(website)
    return website if parsed.hostname else ""


def same_website(url: str, website: str) -> bool:
    target_host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    website_host = (urlparse(website).hostname or "").lower().removeprefix("www.")
    return bool(target_host and website_host and (target_host == website_host or target_host.endswith(f".{website_host}")))


def notice_date_from_text(value: str) -> str:
    match = re.search(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})", value)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def fetch_json(url: str, data: dict[str, str], timeout: int = 15) -> dict[str, Any]:
    body = urlencode(data).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.ggzy.gov.cn/deal/dealList.html",
        },
    )
    with urlopen(req, timeout=timeout, context=DEFAULT_SSL_CONTEXT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_html(url: str, timeout: int = 15) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.ggzy.gov.cn/deal/dealList.html",
        },
    )
    with urlopen(req, timeout=timeout, context=DEFAULT_SSL_CONTEXT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def detail_value(page: str, code: str) -> str:
    match = re.search(
        rf'class="[^"]*\bcode-{re.escape(code)}\b[^"]*"[^>]*>(.*?)</samp>',
        page,
        re.S,
    )
    return html_text(match.group(1)) if match else ""


def extract_procurement_detail(page: str) -> dict[str, str]:
    text = html_text(page)
    deadline_match = re.search(
        r"(?:投标文件截止时间|响应文件提交截止时间)[:：]\s*(.+?)(?=（北京时间|投标地点|开标时间|$)",
        text,
    )
    budget_match = re.search(r"预算金额（元）[:：]\s*([0-9,.]+)", text)
    return {
        "company": detail_value(page, "00014"),
        "address": detail_value(page, "00018"),
        "phone": detail_value(page, "00016"),
        "agency": detail_value(page, "00009"),
        "contact": detail_value(page, "00010"),
        "deadline": deadline_match.group(1).strip() if deadline_match else "",
        "budget": budget_match.group(1).strip() if budget_match else "",
    }


def company_from_notice_title(title: str) -> str:
    candidates = re.split(
        r"(?:20\d{2}年|关于|融雪剂|氯化钙|采购项目|公开招标|竞争性|询价)",
        title,
        maxsplit=1,
    )
    company = candidates[0].strip(" -—：:")
    return company if len(company) >= 4 else "采购单位待核验"


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


def procurement_monitor_entries(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    notice_type_ids: list[str],
    date_window_id: str,
) -> list[Lead]:
    leads: list[Lead] = []
    seen: set[tuple[str, str]] = set()
    region_text = "、".join(regions[:6]) + ("等" if len(regions) > 6 else "")
    date_label, days = PROCUREMENT_DATE_WINDOWS.get(date_window_id, PROCUREMENT_DATE_WINDOWS["10d"])
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    notice_types = [
        (notice_type_id, PROCUREMENT_NOTICE_TYPES[notice_type_id])
        for notice_type_id in notice_type_ids
        if notice_type_id in PROCUREMENT_NOTICE_TYPES
    ] or [("purchase", PROCUREMENT_NOTICE_TYPES["purchase"])]

    for sector in sectors.values():
        keywords = list(dict.fromkeys([*custom_keywords, *sector["keywords"]]))
        for keyword in keywords:
            for notice_type_id, notice_type in notice_types:
                dedupe_key = (keyword, notice_type_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                query_text = f"{region_text} {keyword} {notice_type}".strip()
                ccgp_query = urlencode(
                    {
                        "searchtype": "1",
                        "page_index": "1",
                        "bidSort": "0",
                        "buyerName": "",
                        "projectId": "",
                        "pinMu": "0",
                        "bidType": "0",
                        "dbselect": "bidx",
                        "kw": keyword,
                        "start_time": start_date.strftime("%Y:%m:%d"),
                        "end_time": end_date.strftime("%Y:%m:%d"),
                        "timeType": "6",
                    }
                )
                ggzy_query = urlencode(
                    {
                        "HEADER_DEAL_TYPE": "02",
                        "DEAL_TIME": "06",
                        "DEAL_CLASSIFY": "00",
                        "DEAL_STAGE": "0000",
                        "DEAL_PROVINCE": "0",
                        "DEAL_CITY": "0",
                        "DEAL_PLATFORM": "0",
                        "BID_PLATFORM": "0",
                        "DEAL_TRADE": "0",
                        "isShowAll": "1",
                        "PAGENUMBER": "1",
                        "FINDTXT": keyword,
                    }
                )
                score = int(sector["score"])
                if notice_type_id == "award":
                    score -= 6
                leads.append(
                    Lead(
                        company=f"{keyword} · {notice_type}",
                        region=region_text,
                        sector=sector["name"],
                        source="中国政府采购网 / 全国公共资源交易平台",
                        score=max(score, 1),
                        use_case=sector["uses"],
                        pitch=sector["pitch"],
                        match_reason=(
                            f"{date_label}监控；关键词：{keyword}；公告类型：{notice_type}；"
                            f"关注地区：{region_text}（进入平台后核验地区）"
                        ),
                        search_url=f"https://search.ccgp.gov.cn/bxsearch?{ccgp_query}",
                        website=f"https://www.ggzy.gov.cn/deal/dealList.html?{ggzy_query}",
                        qcc_url=f"https://www.baidu.com/s?wd={quote(query_text + ' 采购单位 中标供应商')}",
                        direction="procurement",
                        process_basis=f"监控周期：{start_date.isoformat()} 至 {end_date.isoformat()}",
                        confidence="官方检索",
                    )
                )
    return leads


def procurement_notice_kind(record: dict[str, Any]) -> str:
    title = str(record.get("title") or "")
    info_type = str(record.get("informationTypeText") or "")
    if "中标" in title or "成交" in title or "中标" in info_type or "成交" in info_type:
        return "award"
    if "招标" in title or "招标" in info_type:
        return "tender"
    return "purchase"


def collect_procurement_companies(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    notice_type_ids: list[str],
    date_window_id: str,
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    window_map = {"3d": "02", "10d": "03", "30d": "04", "90d": "05"}
    keywords: list[tuple[str, dict[str, Any]]] = []
    for sector in sectors.values():
        sector_keywords = list(dict.fromkeys([*custom_keywords, *sector["keywords"]]))[:3]
        keywords.extend((keyword, sector) for keyword in sector_keywords)

    records_by_id: dict[str, tuple[dict[str, Any], dict[str, Any], str]] = {}
    errors: list[str] = []
    total_searches = len(keywords)
    completed = 0
    if progress_callback:
        progress_callback(0, total_searches, 0, 0, "正在搜索全国公共资源交易平台")

    def search_keyword(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        keyword, sector = item
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                data = fetch_json(
                    "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList",
                    {
                        "SOURCE_TYPE": "1",
                        "DEAL_TIME": window_map.get(date_window_id, "03"),
                        "FINDTXT": keyword,
                        "PAGENUMBER": "1",
                    },
                    timeout=20,
                )
                return keyword, sector, data
            except Exception as exc:  # noqa: BLE001 - retry transient platform failures.
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
        raise last_error or RuntimeError("查询失败")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(search_keyword, item): item for item in keywords}
        for future in as_completed(futures):
            keyword, sector = futures[future]
            try:
                keyword, sector, data = future.result()
            except Exception as exc:  # noqa: BLE001 - keep other keywords running.
                errors.append(f"{keyword}：{exc}")
                data = {}
            if data.get("code") != 200:
                if data:
                    errors.append(f"{keyword}：平台返回 {data.get('message') or '查询失败'}")
            for record in ((data.get("data") or {}).get("records") or []):
                record_id = str(record.get("id") or "")
                province = str(record.get("provinceText") or "")
                if not record_id or not any(region.replace("省", "") in province.replace("省", "") for region in regions):
                    continue
                if notice_type_ids and procurement_notice_kind(record) not in notice_type_ids:
                    continue
                records_by_id.setdefault(record_id, (record, sector, keyword))
            completed += 1
            if progress_callback:
                progress_callback(
                    completed,
                    total_searches,
                    len(records_by_id),
                    0,
                    f"正在搜索：{keyword}",
                )

    leads: list[Lead] = []
    records = list(records_by_id.values())[:40]
    detail_total = len(records)

    def build_lead(item: tuple[dict[str, Any], dict[str, Any], str]) -> Lead:
        record, sector, keyword = item
        relative_url = str(record.get("url") or "")
        detail_path = relative_url.replace("/html/a/", "/html/b/")
        detail_url = f"https://www.ggzy.gov.cn{detail_path}"
        detail: dict[str, str] = {}
        try:
            detail = extract_procurement_detail(fetch_html(detail_url))
        except Exception:  # Detail availability varies by source platform.
            detail = {}
        title = str(record.get("title") or "")
        company = detail.get("company") or company_from_notice_title(title)
        notice_kind = procurement_notice_kind(record)
        notice_label = PROCUREMENT_NOTICE_TYPES.get(notice_kind, str(record.get("informationTypeText") or "采购公告"))
        score = int(sector["score"]) - (6 if notice_kind == "award" else 0)
        links = build_search_links(company, str(record.get("provinceText") or ""), keyword)
        deadline = detail.get("deadline") or ""
        budget = detail.get("budget") or ""
        follow_up = sector["pitch"]
        if deadline:
            follow_up = f"截止时间：{deadline}；{follow_up}"
        return Lead(
            company=company,
            region=str(record.get("provinceText") or ""),
            sector=notice_label,
            source="全国公共资源交易平台",
            score=max(score, 1),
            phone=detail.get("phone") or "",
            address=detail.get("address") or "",
            website=f"https://www.ggzy.gov.cn{relative_url}",
            use_case="公开招采项目，可核实采购数量、技术要求和报名条件",
            pitch=follow_up,
            match_reason=f"{record.get('publishTime') or '日期待核验'}；关键词：{keyword}",
            search_url=detail_url,
            raw_type=str(record.get("transactionSourcesPlatformText") or record.get("businessTypeText") or ""),
            qcc_url=links["qcc"],
            direction="procurement",
            process_basis=f"公告发布日期：{record.get('publishTime') or '待核验'}",
            confidence="官方公告",
            project_title=title,
            notice_date=str(record.get("publishTime") or ""),
            contact_name=detail.get("contact") or "",
            agency=detail.get("agency") or "",
            deadline=deadline,
            budget=budget,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(build_lead, item): item for item in records}
        for index, future in enumerate(as_completed(futures), start=1):
            try:
                leads.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(f"公告详情解析失败：{exc}")
            if progress_callback:
                progress_callback(
                    total_searches + index,
                    total_searches + detail_total,
                    len(leads),
                    len([lead for lead in leads if lead.phone]),
                    "正在读取采购单位和联系方式",
                )
    return leads, errors, total_searches + detail_total


def collect_company_website_notices(
    amap_key: str,
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    notice_type_ids: list[str],
    date_window_id: str,
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    if not amap_key:
        return [], ["企业官网采集需要配置高德 Web 服务 API Key。"], 0

    company_jobs: list[tuple[str, dict[str, Any], str]] = []
    for region in regions:
        for sector in sectors.values():
            for keyword in sector.get("company_keywords", [])[:2]:
                company_jobs.append((region, sector, keyword))

    candidates: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    completed = 0
    total = len(company_jobs)
    if progress_callback:
        progress_callback(0, total, 0, 0, "正在查找目标公司及官网")

    with ThreadPoolExecutor(max_workers=AMAP_WORKERS) as executor:
        futures = {
            executor.submit(amap_search, amap_key, region, keyword, 1): (region, sector, keyword)
            for region, sector, keyword in company_jobs
        }
        for future in as_completed(futures):
            region, sector, keyword = futures[future]
            try:
                data = future.result()
                for poi in data.get("pois") or []:
                    name = str(poi.get("name") or "").strip()
                    website = normalize_website(first_text(poi.get("website")))
                    if not name or not website:
                        continue
                    key = f"{name}|{website}"
                    candidates.setdefault(
                        key,
                        {
                            "company": name,
                            "region": " ".join(
                                part
                                for part in [
                                    as_text(poi.get("pname")),
                                    as_text(poi.get("cityname")),
                                    as_text(poi.get("adname")),
                                ]
                                if part
                            ),
                            "phone": as_text(poi.get("tel")).replace(";", " / "),
                            "address": as_text(poi.get("address")),
                            "website": website,
                            "raw_type": as_text(poi.get("type")),
                            "sector": sector,
                            "company_keyword": keyword,
                        },
                    )
            except Exception as exc:  # noqa: BLE001 - keep other company searches running.
                errors.append(f"{region}/{keyword}：{exc}")
            completed += 1
            if progress_callback:
                progress_callback(completed, total, len(candidates), 0, f"正在查找公司：{keyword}")

    announcement_words = ["招标", "采购", "询价", "竞价", "比选", "供应商", "征集", "谈判"]
    product_words = list(
        dict.fromkeys(
            [
                *custom_keywords,
                *[
                    keyword
                    for sector in sectors.values()
                    for keyword in sector.get("keywords", [])
                ],
            ]
        )
    )
    _, max_days = PROCUREMENT_DATE_WINDOWS.get(date_window_id, PROCUREMENT_DATE_WINDOWS["10d"])
    cutoff = date.today() - timedelta(days=max_days)
    selected_notice_types = set(notice_type_ids) or {"purchase", "tender", "award"}

    def website_notice_kind(value: str) -> str:
        if any(word in value for word in ["中标", "成交", "候选人公示"]):
            return "award"
        if "招标" in value:
            return "tender"
        return "purchase"

    def inspect_candidate(candidate: dict[str, Any]) -> list[Lead]:
        website = candidate["website"]
        homepage = fetch_html(website, timeout=12)
        navigation_pages: list[str] = [website]
        for link in html_links(homepage):
            combined = f"{link['text']} {link['href']}".lower()
            if not any(word in combined for word in announcement_words):
                continue
            url = urljoin(website, link["href"])
            if same_website(url, website) and url not in navigation_pages:
                navigation_pages.append(url)
            if len(navigation_pages) >= 5:
                break

        found: list[Lead] = []
        seen_urls: set[str] = set()
        detail_checks = 0
        for page_url in navigation_pages:
            page = homepage if page_url == website else fetch_html(page_url, timeout=12)
            for link in html_links(page):
                title = link["text"].strip()
                if len(title) < 6:
                    continue
                title_and_href = f"{title} {link['href']}"
                if not any(word in title_and_href for word in announcement_words):
                    continue
                notice_url = urljoin(page_url, link["href"])
                if not same_website(notice_url, website) or notice_url in seen_urls:
                    continue
                notice_kind = website_notice_kind(title_and_href)
                if notice_kind not in selected_notice_types:
                    continue
                notice_text = title_and_href
                if product_words and not any(word in notice_text for word in product_words):
                    if detail_checks >= 12:
                        continue
                    detail_checks += 1
                    try:
                        notice_text = f"{notice_text} {html_text(fetch_html(notice_url, timeout=10))}"
                    except Exception:  # noqa: BLE001 - skip inaccessible detail pages.
                        continue
                    if not any(word in notice_text for word in product_words):
                        continue
                notice_date = notice_date_from_text(notice_text)
                if notice_date:
                    try:
                        if date.fromisoformat(notice_date) < cutoff:
                            continue
                    except ValueError:
                        pass
                seen_urls.add(notice_url)
                sector = candidate["sector"]
                links = build_search_links(candidate["company"], candidate["region"], title)
                found.append(
                    Lead(
                        company=candidate["company"],
                        region=candidate["region"],
                        sector="企业官网采购公告",
                        source="企业官网",
                        score=min(100, int(sector["score"]) + 8),
                        phone=candidate["phone"],
                        address=candidate["address"],
                        website=notice_url,
                        use_case="企业官网公开的招标、采购、询价或供应商公告",
                        pitch=sector["pitch"],
                        match_reason=f"官网命中：{title}",
                        search_url=notice_url,
                        raw_type=candidate["raw_type"],
                        qcc_url=links["qcc"],
                        company_website=website,
                        direction="procurement",
                        process_basis=f"先定位目标公司，再检索官网采购栏目；公司线索：{candidate['company_keyword']}",
                        confidence="官网公告",
                        project_title=title,
                        notice_date=notice_date,
                    )
                )
                if len(found) >= 5:
                    return found
        return found

    leads: list[Lead] = []
    selected_candidates = list(candidates.values())[:30]
    website_total = len(selected_candidates)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(inspect_candidate, item): item for item in selected_candidates}
        for index, future in enumerate(as_completed(futures), start=1):
            candidate = futures[future]
            try:
                leads.extend(future.result())
            except Exception as exc:  # noqa: BLE001 - one inaccessible website should not stop the batch.
                errors.append(f"{candidate['company']}官网：{exc}")
            if progress_callback:
                progress_callback(
                    total + index,
                    total + website_total,
                    len(leads),
                    len([lead for lead in leads if lead.phone]),
                    f"正在检查官网：{candidate['company']}",
                )
    return leads, errors, total + website_total


def collect_leads(payload: dict[str, Any], progress_callback: Any = None) -> dict[str, Any]:
    requested_direction = str(payload.get("direction") or "")
    direction = requested_direction if requested_direction in {"upstream", "procurement"} else "downstream"
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
    if direction == "procurement":
        notice_type_ids = payload.get("noticeTypes") or ["purchase", "tender", "award"]
        date_window_id = str(payload.get("dateWindow") or "10d")
        procurement_sources = payload.get("procurementSources") or ["public_platform"]
        leads: list[Lead] = []
        errors = []
        request_count = 0
        if "public_platform" in procurement_sources:
            platform_leads, platform_errors, platform_requests = collect_procurement_companies(
                regions,
                sectors,
                custom_keywords,
                notice_type_ids,
                date_window_id,
                progress_callback,
            )
            leads.extend(platform_leads)
            errors.extend(platform_errors)
            request_count += platform_requests
        if "company_website" in procurement_sources:
            website_leads, website_errors, website_requests = collect_company_website_notices(
                amap_key,
                regions,
                sectors,
                custom_keywords,
                notice_type_ids,
                date_window_id,
                progress_callback,
            )
            leads.extend(website_leads)
            errors.extend(website_errors)
            request_count += website_requests
        deduped: dict[str, Lead] = {}
        for lead in leads:
            key = f"{lead.company}|{lead.project_title}|{lead.website}"
            deduped.setdefault(key, lead)
        leads = list(deduped.values())
        leads = sorted(leads, key=lambda item: item.score, reverse=True)
        return {
            "leads": [asdict(lead) for lead in leads],
            "errors": errors[:40],
            "meta": {
                "count": len(leads),
                "companyCount": len(leads),
                "phoneCount": len([lead for lead in leads if lead.phone]),
                "requestCount": request_count,
                "workers": 4,
                "fastMode": True,
                "direction": direction,
                "regions": regions,
                "sectors": [item["name"] for item in sectors.values()],
                "noticeTypes": [PROCUREMENT_NOTICE_TYPES[item] for item in notice_type_ids if item in PROCUREMENT_NOTICE_TYPES],
                "procurementSources": procurement_sources,
                "dateWindow": PROCUREMENT_DATE_WINDOWS.get(date_window_id, PROCUREMENT_DATE_WINDOWS["10d"])[0],
                "mode": "procurement",
            },
        }
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
        "project_title",
        "notice_date",
        "contact_name",
        "agency",
        "deadline",
        "budget",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


class AppHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        if urlparse(self.path).path in {
            "/",
            "/index.html",
            "/app.js",
            "/login",
            "/login.html",
            "/login.js",
        }:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

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
        return path in {
            "/login",
            "/login.html",
            "/styles.css",
            "/login.js",
            "/api/login",
            "/api/send-code",
            "/health",
        }

    def sms_send_allowed(self, phone: str) -> tuple[bool, str]:
        now = time.time()
        client = self.client_id()
        with SMS_LOCK:
            attempts = [
                stamp
                for stamp in SMS_SEND_ATTEMPTS.get(client, [])
                if now - stamp < LOGIN_WINDOW
            ]
            SMS_SEND_ATTEMPTS[client] = attempts
            if len(attempts) >= SMS_SEND_LIMIT:
                return False, "验证码发送过于频繁，请十分钟后再试"
            existing = SMS_CODES.get(phone)
            if existing and now - float(existing.get("sentAt", 0)) < SMS_SEND_COOLDOWN:
                wait_seconds = max(
                    1,
                    int(SMS_SEND_COOLDOWN - (now - float(existing["sentAt"]))),
                )
                return False, f"请等待 {wait_seconds} 秒后重新发送"
        return True, ""

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        if parsed.path == "/":
            return str(STATIC_DIR / "index.html")
        return str(STATIC_DIR / parsed.path.lstrip("/"))

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
        path = urlparse(self.path).path
        if path == "/health":
            json_response(self, {"status": "ok", "version": "procurement-monitor-v1"})
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
                self.redirect("/login?v=sms-login-1")
            return
        if path == "/api/config":
            json_response(
                self,
                {
                    "sectors": SECTOR_LIBRARY,
                    "downstreamSectors": SECTOR_LIBRARY,
                    "upstreamSectors": UPSTREAM_SECTOR_LIBRARY,
                    "procurementSectors": PROCUREMENT_SECTOR_LIBRARY,
                    "procurementNoticeTypes": PROCUREMENT_NOTICE_TYPES,
                    "procurementDateWindows": {
                        key: value[0] for key, value in PROCUREMENT_DATE_WINDOWS.items()
                    },
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
        if path == "/api/send-code":
            if not APP_PASSWORD:
                json_response(self, {"error": "服务器尚未配置 APP_PASSWORD"}, 503)
                return
            phone = normalize_phone(payload.get("phone"))
            password = str(payload.get("password") or "")
            if not valid_login_phone(phone) or not hmac.compare_digest(password, APP_PASSWORD):
                self.record_login_failure()
                json_response(self, {"error": "手机号或密码错误"}, 401)
                return
            allowed, reason = self.sms_send_allowed(phone)
            if not allowed:
                json_response(self, {"error": reason}, 429)
                return
            if not sms_configured() and not SMS_DEV_MODE:
                json_response(self, {"error": "服务器尚未配置短信服务"}, 503)
                return
            code = f"{secrets.randbelow(1_000_000):06d}"
            try:
                if sms_configured():
                    send_aliyun_sms(phone, code)
            except Exception as exc:  # noqa: BLE001
                json_response(self, {"error": f"验证码发送失败：{exc}"}, 502)
                return
            now = time.time()
            with SMS_LOCK:
                SMS_CODES[phone] = {
                    "digest": code_digest(phone, code),
                    "expiresAt": now + SMS_CODE_TTL,
                    "sentAt": now,
                    "attempts": 0,
                }
                SMS_SEND_ATTEMPTS.setdefault(self.client_id(), []).append(now)
            response: dict[str, Any] = {"ok": True, "expiresIn": SMS_CODE_TTL}
            if SMS_DEV_MODE and not sms_configured():
                response["devCode"] = code
            json_response(self, response)
            return

        if path == "/api/login":
            if not APP_PASSWORD:
                json_response(self, {"error": "服务器尚未配置 APP_PASSWORD"}, 503)
                return
            if not self.login_allowed():
                json_response(self, {"error": "尝试次数过多，请十分钟后再试"}, 429)
                return
            phone = normalize_phone(payload.get("phone"))
            password = str(payload.get("password") or "")
            code = re.sub(r"\D", "", str(payload.get("code") or ""))
            if not valid_login_phone(phone) or not hmac.compare_digest(password, APP_PASSWORD):
                self.record_login_failure()
                json_response(self, {"error": "手机号或密码错误"}, 401)
                return
            now = time.time()
            with SMS_LOCK:
                saved_code = SMS_CODES.get(phone)
                if not saved_code or now > float(saved_code.get("expiresAt", 0)):
                    SMS_CODES.pop(phone, None)
                    saved_code = None
                elif int(saved_code.get("attempts", 0)) >= 5:
                    SMS_CODES.pop(phone, None)
                    saved_code = None
                elif not hmac.compare_digest(
                    str(saved_code.get("digest") or ""),
                    code_digest(phone, code),
                ):
                    saved_code["attempts"] = int(saved_code.get("attempts", 0)) + 1
                    saved_code = None
                else:
                    SMS_CODES.pop(phone, None)
            if not saved_code:
                self.record_login_failure()
                json_response(self, {"error": "验证码错误或已过期"}, 401)
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
    if not LOGIN_PHONES:
        print("WARNING: LOGIN_PHONES is not set. Only SMS_DEV_MODE can allow local login.")
    if not sms_configured() and not SMS_DEV_MODE:
        print("WARNING: Aliyun SMS is not configured. Verification codes cannot be sent.")
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
