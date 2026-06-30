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
import sqlite3
import ssl
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen

import certifi

try:
    import turso
    from turso import lib_sync as turso_sync
except Exception as exc:  # noqa: BLE001 - optional cloud database dependency.
    turso = None
    turso_sync = None
    TURSO_IMPORT_ERROR = exc
else:
    TURSO_IMPORT_ERROR = None


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
ALIYUN_PNVS_ACCESS_KEY_ID = (
    os.getenv("ALIYUN_PNVS_ACCESS_KEY_ID")
    or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
    or os.getenv("ALIYUN_SMS_ACCESS_KEY_ID", "")
)
ALIYUN_PNVS_ACCESS_KEY_SECRET = (
    os.getenv("ALIYUN_PNVS_ACCESS_KEY_SECRET")
    or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    or os.getenv("ALIYUN_SMS_ACCESS_KEY_SECRET", "")
)
ALIYUN_PNVS_SCHEME_NAME = os.getenv("ALIYUN_PNVS_SCHEME_NAME", "")
ALIYUN_PNVS_SIGN_NAME = os.getenv("ALIYUN_PNVS_SIGN_NAME", "速通互联验证码")
ALIYUN_PNVS_TEMPLATE_CODE = os.getenv("ALIYUN_PNVS_TEMPLATE_CODE", "100001")
AMAP_WORKERS = max(1, min(int(os.getenv("AMAP_WORKERS", "4")), 8))
AMAP_RETRY_CODES = {"10015", "10016", "10019", "10020", "10021"}
SEARCH_JOBS: dict[str, dict[str, Any]] = {}
SEARCH_JOBS_LOCK = threading.Lock()
SEARCH_JOB_TTL = 60 * 60
DATA_DIR = Path(os.getenv("DATA_DIR") or (ROOT / "data")).expanduser().resolve()
DATABASE_PATH = DATA_DIR / "leads.db"
TURSO_REPLICA_PATH = DATA_DIR / "turso-replica.db"
BACKUP_DIR = DATA_DIR / "backups"
TURSO_CLIENT_NAME = os.getenv("TURSO_CLIENT_NAME", "calcium-leads")
TURSO_RUNTIME_DISABLED = False
TURSO_RUNTIME_ERROR = ""
DATABASE_LOCK = threading.RLock()
MONITOR_WAKE_EVENT = threading.Event()
MONITOR_RUNNING: set[int] = set()
MONITOR_RUNNING_LOCK = threading.Lock()
APP_VERSION = "liquid-calcium-ops-v2"
MAX_REQUEST_BODY = 5 * 1024 * 1024

DIRECTION_LABELS = {
    "downstream": "下游买家",
    "upstream": "上游液钙副产企业",
    "procurement": "招投标/采购",
    "environmental": "含氟废水企业",
    "competitor": "竞品/同行情报",
}
DIRECTION_ORDER = ["downstream", "upstream", "procurement", "environmental", "competitor"]
DIRECTION_SET = set(DIRECTION_ORDER)


COMPETITOR_SECTOR_LIBRARY: dict[str, dict[str, Any]] = {
    "liquid": {
        "name": "液体氯化钙",
        "keywords": ["液体氯化钙", "氯化钙溶液", "液钙厂家"],
        "score": 30,
    },
    "anhydrous": {
        "name": "无水氯化钙",
        "keywords": ["无水氯化钙", "94%氯化钙", "氯化钙颗粒"],
        "score": 24,
    },
    "dihydrate": {
        "name": "二水氯化钙",
        "keywords": ["二水氯化钙", "74%氯化钙", "氯化钙片状"],
        "score": 24,
    },
    "deicing": {
        "name": "融雪剂/道路除冰",
        "keywords": ["氯化钙融雪剂", "道路除冰剂", "液体融雪剂"],
        "score": 22,
    },
    "desiccant": {
        "name": "干燥剂/吸湿剂",
        "keywords": ["氯化钙干燥剂", "集装箱干燥剂", "吸湿剂原料"],
        "score": 20,
    },
    "water": {
        "name": "水处理/工业应用",
        "keywords": ["水处理氯化钙", "工业级氯化钙", "污水处理氯化钙"],
        "score": 18,
    },
}

COMPETITOR_SOURCE_LIBRARY: dict[str, dict[str, str]] = {
    "company_website": {
        "name": "同行企业官网",
        "site": "",
    },
    "1688": {
        "name": "1688",
        "site": "site:1688.com",
    },
    "aicaigou": {
        "name": "百度爱采购",
        "site": "site:b2b.baidu.com",
    },
    "chemnet": {
        "name": "中国化工网",
        "site": "site:chemnet.com",
    },
}

COMPETITOR_APPLICATION_SIGNALS: dict[str, list[str]] = {
    "融雪剂/道路除冰": ["融雪", "除冰", "道路养护", "高速公路", "机场除冰"],
    "干燥剂/吸湿": ["干燥剂", "吸湿剂", "防潮", "集装箱", "除湿"],
    "水处理": ["水处理", "污水处理", "废水处理", "净水", "絮凝"],
    "油田/钻井": ["油田", "钻井液", "完井液", "石油助剂"],
    "混凝土/建材": ["混凝土", "早强剂", "速凝剂", "建材", "砂浆"],
    "制冷/冷链": ["制冷", "冷冻盐水", "冷库", "载冷剂"],
    "食品/饲料": ["食品级", "食品添加剂", "饲料级", "饲料添加剂"],
    "化工贸易": ["化工原料", "经销", "贸易", "供应链", "厂家直销"],
}

COMPETITOR_KEYWORD_SIGNALS = [
    "液体氯化钙",
    "氯化钙溶液",
    "无水氯化钙",
    "二水氯化钙",
    "94%氯化钙",
    "74%氯化钙",
    "工业级",
    "食品级",
    "饲料级",
    "厂家直销",
    "吨包",
    "槽车",
    "出口",
]


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

ENVIRONMENTAL_SEARCH_TERMS: dict[str, list[str]] = {
    "fluorochemicals": ["氟", "氟化工", "含氟"],
    "rare_earth": ["稀土"],
    "phosphorus": ["磷化工", "磷肥", "磷酸"],
    "surface_treatment": ["电镀", "表面处理", "铝业", "汽车零部件"],
    "electronics": ["电子", "半导体", "光伏"],
    "glass_ceramics": ["玻璃", "陶瓷", "建筑材料"],
    "battery_materials": ["锂电", "电池材料"],
}

ENVIRONMENTAL_DOCUMENT_SOURCES: dict[str, dict[str, Any]] = {
    "eia": {
        "name": "环评/审批公示",
        "terms": ["环境影响评价", "环评审批公示"],
        "score": 34,
    },
    "acceptance": {
        "name": "竣工环保验收",
        "terms": ["竣工环境保护验收", "验收监测报告"],
        "score": 38,
    },
    "monitoring": {
        "name": "自行监测",
        "terms": ["自行监测", "废水监测报告"],
        "score": 42,
    },
    "enforcement": {
        "name": "处罚/整改",
        "terms": ["行政处罚", "超标 整改"],
        "score": 46,
    },
}

ENVIRONMENTAL_WEBSITE_NAV_WORDS = [
    "环境",
    "环保",
    "可持续",
    "社会责任",
    "ESG",
    "信息公开",
    "项目公示",
    "环评",
    "验收",
    "监测",
    "招标",
    "采购",
]
ENVIRONMENTAL_WEBSITE_CONFIRMED_WORDS = [
    "含氟废水",
    "废水氟化物",
    "氟离子",
    "氟化物（以F-计）",
    "氟化物(以F-计)",
]
ENVIRONMENTAL_WEBSITE_PROCESS_WORDS = [
    "氢氟酸",
    "蚀刻液",
    "含氟酸",
    "除氟",
    "氟盐",
    "湿法冶炼",
    "晶硅清洗",
    "酸洗",
]
ENVIRONMENTAL_WEBSITE_WATER_WORDS = [
    "废水",
    "污水处理",
    "废水处理站",
    "水污染",
    "排放口",
    "零排放",
]

FLUORIDE_SECTOR_LIBRARY: dict[str, dict[str, Any]] = {
    "fluorochemicals": {
        "name": "氟化工/含氟材料",
        "keywords": ["氟", "氟化工", "含氟"],
        "process": "含氟原料、含氟产品或含氟废水处理工序。",
        "pitch": "核实废水氟离子浓度、日排水量、钙法除氟药剂、污泥量及现有处理成本。",
        "indicators": ["氟", "氟化"],
        "strict_indicators": ["氟"],
        "confidence": "官方许可",
        "score": 48,
    },
    "rare_earth": {
        "name": "稀土冶炼/分离",
        "keywords": ["稀土"],
        "process": "稀土湿法冶炼、萃取和沉淀工序可能形成含氟废水。",
        "pitch": "核实含氟废水来源、浓度、排量、钙盐投加和含氟污泥处置方式。",
        "indicators": ["稀土", "冶炼"],
        "strict_indicators": ["稀土"],
        "confidence": "官方许可",
        "score": 44,
    },
    "phosphorus": {
        "name": "磷化工/磷肥",
        "keywords": ["磷化工", "磷肥", "磷酸"],
        "process": "磷矿及磷化工生产废水常需监控氟化物。",
        "pitch": "确认氟化物排放限值、石灰乳用量、除氟污泥和废水回用比例。",
        "indicators": ["磷", "化肥"],
        "strict_indicators": ["磷"],
        "confidence": "官方许可",
        "score": 45,
    },
    "surface_treatment": {
        "name": "金属表面处理/铝加工",
        "keywords": ["电镀", "表面处理", "铝业", "汽车零部件"],
        "process": "酸洗、蚀刻、表面处理或铝材加工废水可能含氟。",
        "pitch": "核实含氟槽液来源、废水分质收集、钙法除氟和日均处理量。",
        "indicators": ["电镀", "表面处理", "铝", "汽车零部件"],
        "strict_indicators": ["电镀", "表面处理", "铝"],
        "confidence": "官方许可",
        "score": 42,
    },
    "electronics": {
        "name": "电子/半导体/光伏",
        "keywords": ["电子", "半导体", "光伏"],
        "process": "蚀刻、清洗和晶硅加工工序可能产生含氟废水。",
        "pitch": "核实氢氟酸使用量、含氟废水浓度、处理规模和深度除氟需求。",
        "indicators": ["电子", "半导体", "光伏"],
        "strict_indicators": ["电子", "半导体", "光伏"],
        "confidence": "官方许可",
        "score": 44,
    },
    "glass_ceramics": {
        "name": "玻璃/陶瓷/建材",
        "keywords": ["玻璃", "陶瓷", "建筑材料"],
        "process": "原料、窑炉配套或表面处理废水可能纳入氟化物许可管理。",
        "pitch": "确认废水氟化物实际来源、浓度波动、排放方式及处理药剂。",
        "indicators": ["玻璃", "陶瓷", "建筑材料"],
        "strict_indicators": ["玻璃", "陶瓷"],
        "confidence": "官方许可",
        "score": 38,
    },
    "battery_materials": {
        "name": "锂电/电池材料",
        "keywords": ["锂电", "电池材料"],
        "process": "含氟电解质、材料制备或回收工序可能产生含氟废水。",
        "pitch": "核实氟离子浓度、金属杂质、废水量和钙法沉淀处理条件。",
        "indicators": ["锂电", "电池"],
        "strict_indicators": ["锂电", "电池"],
        "confidence": "官方许可",
        "score": 43,
    },
}

FLUORIDE_PERMIT_INDEX: list[dict[str, str]] = [
    {
        "company": "安徽华晟新能源科技股份有限公司",
        "region": "安徽省宣城市",
        "sector_id": "electronics",
        "industry": "光伏设备及元器件制造",
        "permit_number": "91341800MA2W1EY93F001U",
        "data_id": "1295cf87d7bb4863ad054087d43c125f",
        "official_website": "https://www.huasunsolar.com/cn",
        "water_pollutants": "氟化物（以F-计）、pH值、化学需氧量、氨氮",
    },
    {
        "company": "徐州鑫宇光伏科技有限公司",
        "region": "江苏省徐州市",
        "sector_id": "electronics",
        "industry": "光伏设备及元器件制造",
        "permit_number": "",
        "data_id": "1850ddb4e7c94730ba9b48999367d1d8",
        "water_pollutants": "氟化物（以F-计）、pH值、化学需氧量、氨氮",
    },
    {
        "company": "大连徽连表面处理有限公司",
        "region": "辽宁省大连市",
        "sector_id": "surface_treatment",
        "industry": "金属表面处理及热处理加工",
        "permit_number": "91210213582024101G001P",
        "data_id": "4ed70afca36447328f361d8a7a2dfe7e",
        "water_pollutants": "氟化物（以F-计）、总铬、六价铬、总镍、总镉",
    },
    {
        "company": "湖北宜化磷化工有限公司",
        "region": "湖北省宜昌市",
        "sector_id": "phosphorus",
        "industry": "磷肥制造",
        "permit_number": "9142050074769775XK001P",
        "data_id": "5b8d981a1de34638b8104cf76b0c14d8",
        "water_pollutants": "氟化物（以F-计）、pH值、悬浮物、磷酸盐、总磷",
    },
    {
        "company": "山东威高血液净化制品股份有限公司",
        "region": "山东省威海市",
        "sector_id": "fluorochemicals",
        "industry": "医疗仪器设备及器械制造",
        "permit_number": "91371000706241054H001U",
        "data_id": "5acef27ba2c94308a84b7801731a64fc",
        "official_website": "https://www.wego-healthcare.com/",
        "water_pollutants": "氟化物（以F-计）、pH值、化学需氧量、氨氮",
    },
    {
        "company": "中节能（连云港）清洁技术发展有限公司",
        "region": "江苏省连云港市",
        "sector_id": "fluorochemicals",
        "industry": "危险废物治理",
        "permit_number": "",
        "data_id": "b92d1bd237ce48e7b4728b4ff82194a7",
        "official_website": "https://www.qjlyg.cecep.cn/",
        "water_pollutants": "氟化物（以F-计）、pH值、化学需氧量、氨氮",
    },
    {
        "company": "江苏方洋水务有限公司",
        "region": "江苏省连云港市",
        "sector_id": "fluorochemicals",
        "industry": "污水处理及其再生利用",
        "permit_number": "91320700595593999Q001V",
        "data_id": "4d626c2d1ec7489c85a058753127054a",
        "official_website": "https://www.jsfywater.com/",
        "water_pollutants": "氟化物（以F-计）、总磷、总氮、化学需氧量、氨氮",
    },
    {
        "company": "江苏赛科化学有限公司",
        "region": "江苏省镇江市",
        "sector_id": "fluorochemicals",
        "industry": "化学原料和化学制品制造业",
        "permit_number": "91321191MA1MPTK67G001P",
        "data_id": "5f40d565aedb45d3b64f22ad6e641f59",
        "official_website": "https://www.secol.com.cn/",
        "water_pollutants": "氟化物（以F-计）、总有机碳、化学需氧量、氨氮",
    },
]

PERMIT_REGION_CODES: dict[str, str] = {
    "北京": "110000000000",
    "天津": "120000000000",
    "河北": "130000000000",
    "山西": "140000000000",
    "内蒙古": "150000000000",
    "辽宁": "210000000000",
    "吉林": "220000000000",
    "黑龙江": "230000000000",
    "上海": "310000000000",
    "江苏": "320000000000",
    "浙江": "330000000000",
    "安徽": "340000000000",
    "福建": "350000000000",
    "江西": "360000000000",
    "山东": "370000000000",
    "河南": "410000000000",
    "湖北": "420000000000",
    "湖南": "430000000000",
    "广东": "440000000000",
    "广西": "450000000000",
    "海南": "460000000000",
    "重庆": "500000000000",
    "四川": "510000000000",
    "贵州": "520000000000",
    "云南": "530000000000",
    "西藏": "540000000000",
    "陕西": "610000000000",
    "甘肃": "620000000000",
    "青海": "630000000000",
    "宁夏": "640000000000",
    "新疆": "650000000000",
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
UPSTREAM_NON_COMPANY_WORDS = [
    "地名地址",
    "道路名",
    "交通地名",
    "商务住宅",
    "生活服务",
    "购物服务",
    "门牌信息",
    "住宅区",
]
UPSTREAM_COMPANY_HINTS = [
    "公司",
    "集团",
    "有限",
    "股份",
    "工厂",
    "厂",
    "化工",
    "材料",
    "工业",
    "科技",
    "能源",
    "环保",
    "药业",
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
    competitor_industries: str = ""
    competitor_regions: str = ""
    competitor_keywords: str = ""
    competitor_channels: str = ""
    evidence_count: int = 0


SALES_STATUSES = {
    "new": "待核实",
    "contacted": "已联系",
    "qualified": "有需求",
    "quoted": "报价中",
    "won": "已成交",
    "lost": "无效",
}


class TursoRow:
    """Small mapping wrapper so pyturso rows behave like sqlite3.Row."""

    def __init__(self, cursor: Any, row: Any) -> None:
        self._values = tuple(row)
        self._keys = [description[0] for description in cursor.description or ()]
        self._mapping = dict(zip(self._keys, self._values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, str):
            return self._mapping[key]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._keys)


def turso_database_url() -> str:
    return (
        os.getenv("TURSO_DATABASE_URL")
        or os.getenv("LIBSQL_URL")
        or os.getenv("TURSO_DB_URL")
        or ""
    ).strip()


def turso_auth_token() -> str:
    return (
        os.getenv("TURSO_AUTH_TOKEN")
        or os.getenv("LIBSQL_AUTH_TOKEN")
        or os.getenv("TURSO_DATABASE_AUTH_TOKEN")
        or ""
    ).strip()


def turso_configured() -> bool:
    return bool(turso_database_url() and turso_auth_token())


def turso_active() -> bool:
    return turso_configured() and not TURSO_RUNTIME_DISABLED


def disable_turso_runtime(error: Any) -> None:
    global TURSO_RUNTIME_DISABLED, TURSO_RUNTIME_ERROR
    TURSO_RUNTIME_DISABLED = True
    TURSO_RUNTIME_ERROR = str(error)
    print(
        f"WARNING: Turso unavailable, falling back to local SQLite: {TURSO_RUNTIME_ERROR}",
        flush=True,
    )


def push_turso_changes(connection: Any) -> None:
    try:
        connection.push()
    except Exception as exc:  # noqa: BLE001 - repair stale replica relationships once.
        if "FOREIGN KEY constraint failed" not in str(exc):
            raise
        connection.execute("UPDATE leads SET monitor_id = NULL WHERE monitor_id IS NOT NULL")
        connection.execute(
            "UPDATE notifications SET lead_id = NULL, monitor_id = NULL "
            "WHERE lead_id IS NOT NULL OR monitor_id IS NOT NULL"
        )
        connection.commit()
        connection.push()


@contextmanager
def database_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    using_turso = turso_active()
    if using_turso:
        if turso_sync is None:
            disable_turso_runtime(f"Turso SDK 未安装或无法加载：{TURSO_IMPORT_ERROR}")
            using_turso = False
        else:
            try:
                connection = turso_sync.connect_sync(
                    str(TURSO_REPLICA_PATH),
                    turso_database_url(),
                    auth_token=turso_auth_token(),
                    client_name=TURSO_CLIENT_NAME,
                    bootstrap_if_empty=True,
                )
                connection.row_factory = TursoRow
                # Turso embedded-replica changesets can apply related rows in a
                # different order remotely. Relationships are validated in-app.
                connection.execute("PRAGMA foreign_keys=OFF")
            except Exception as exc:  # noqa: BLE001 - keep the web service available.
                disable_turso_runtime(exc)
                using_turso = False
    if not using_turso:
        connection = sqlite3.connect(DATABASE_PATH, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
        if using_turso:
            try:
                push_turso_changes(connection)
            except Exception as exc:  # noqa: BLE001 - do not crash Render on sync failure.
                disable_turso_runtime(exc)
    finally:
        connection.close()


def initialize_database() -> None:
    with DATABASE_LOCK, database_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE,
                company TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'downstream',
                region TEXT NOT NULL DEFAULT '',
                sector TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                company_website TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                score_details TEXT NOT NULL DEFAULT '{}',
                sales_status TEXT NOT NULL DEFAULT 'new',
                owner TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                next_follow_up TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '{}',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_new INTEGER NOT NULL DEFAULT 1,
                monitor_id INTEGER,
                FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(sales_status);
            CREATE INDEX IF NOT EXISTS idx_leads_direction ON leads(direction);
            CREATE INDEX IF NOT EXISTS idx_leads_follow_up ON leads(next_follow_up);
            CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);

            CREATE TABLE IF NOT EXISTS monitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                interval_hours INTEGER NOT NULL DEFAULT 24,
                payload TEXT NOT NULL,
                last_run TEXT NOT NULL DEFAULT '',
                next_run TEXT NOT NULL DEFAULT '',
                last_result TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                lead_id INTEGER,
                monitor_id INTEGER,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
                FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read, created_at DESC);

            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_system_events_created
                ON system_events(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_system_events_source
                ON system_events(source, level, created_at DESC);

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                summary TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_activity_created
                ON activity_log(created_at DESC);
            """
        )
        existing_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(leads)").fetchall()
        }
        lead_columns = {
            "opportunity_role": "TEXT NOT NULL DEFAULT ''",
            "liquid_concentration": "TEXT NOT NULL DEFAULT ''",
            "monthly_volume": "TEXT NOT NULL DEFAULT ''",
            "impurity_profile": "TEXT NOT NULL DEFAULT ''",
            "logistics_radius": "TEXT NOT NULL DEFAULT ''",
            "storage_condition": "TEXT NOT NULL DEFAULT ''",
            "commercial_value": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in lead_columns.items():
            if column not in existing_columns:
                connection.execute(
                    f"ALTER TABLE leads ADD COLUMN {column} {definition}"
                )
        connection.execute(
            """
            UPDATE leads
            SET opportunity_role = CASE
                WHEN direction = 'upstream' THEN 'supplier'
                WHEN direction IN ('downstream', 'procurement') THEN 'buyer'
                ELSE 'prospect'
            END
            WHERE opportunity_role = ''
            """
        )


def log_system_event(
    level: str,
    category: str,
    message: str,
    source: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    try:
        with DATABASE_LOCK, database_connection() as connection:
            connection.execute(
                """
                INSERT INTO system_events (
                    level, category, source, message, details, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    level,
                    category,
                    source,
                    message[:2000],
                    json.dumps(details or {}, ensure_ascii=False),
                    now_iso(),
                ),
            )
    except Exception:
        pass


def log_activity(
    action: str,
    entity_type: str,
    summary: str,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    with DATABASE_LOCK, database_connection() as connection:
        connection.execute(
            """
            INSERT INTO activity_log (
                action, entity_type, entity_id, summary, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                entity_type,
                entity_id,
                summary[:1000],
                json.dumps(details or {}, ensure_ascii=False),
                now_iso(),
            ),
        )


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalized_company_name(value: Any) -> str:
    company = str(value or "").lower()
    company = re.sub(r"[（(].*?[）)]", "", company)
    company = re.sub(
        r"(有限责任公司|股份有限公司|有限公司|集团公司|集团|公司)$",
        "",
        company,
    )
    return re.sub(r"[\s\-—_·,，.。/]+", "", company)


def lead_dedupe_key(lead: dict[str, Any]) -> str:
    company = normalized_company_name(lead.get("company"))
    direction = str(lead.get("direction") or "downstream")
    project = re.sub(
        r"[\s\-—_（）()【】\[\]：:]+",
        "",
        str(lead.get("project_title") or "").lower(),
    )
    if direction == "procurement" and project:
        raw = f"{direction}|{company}|{project}"
    else:
        raw = f"{direction}|{company}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_date_value(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def calculate_lead_score(lead: dict[str, Any]) -> tuple[int, dict[str, int]]:
    base = max(0, min(int(lead.get("score") or 0), 60))
    contact = 0
    if lead.get("phone"):
        contact += 12
    if lead.get("email"):
        contact += 4
    if lead.get("company_website") or lead.get("website"):
        contact += 4

    evidence = 0
    evidence_text = " ".join(
        str(lead.get(key) or "")
        for key in ("source", "confidence", "match_reason", "process_basis")
    )
    if any(word in evidence_text for word in ("官方", "排污许可", "政府采购", "公共资源")):
        evidence += 10
    elif "企业官网" in evidence_text:
        evidence += 8
    elif evidence_text.strip():
        evidence += 4

    freshness = 0
    evidence_date = parse_date_value(lead.get("notice_date") or lead.get("updated_at"))
    if evidence_date:
        age = max(0, (date.today() - evidence_date).days)
        freshness = 10 if age <= 7 else 7 if age <= 30 else 3 if age <= 90 else 0
    elif lead.get("source") == "高德 POI":
        freshness = 4

    liquid_fit = 0
    liquid_text = " ".join(
        str(lead.get(key) or "")
        for key in (
            "sector",
            "use_case",
            "pitch",
            "match_reason",
            "process_basis",
            "project_title",
            "opportunity_role",
            "liquid_concentration",
            "monthly_volume",
            "direction",
            "source",
        )
    )
    if any(word in liquid_text for word in ("液体氯化钙", "液钙", "氯化钙溶液")):
        liquid_fit += 10
    elif lead.get("direction") == "upstream":
        liquid_fit += 6
    elif any(word in liquid_text for word in ("副产", "盐酸", "石灰中和", "水处理", "融雪")):
        liquid_fit += 6
    if lead.get("liquid_concentration") or lead.get("monthly_volume"):
        liquid_fit = min(10, liquid_fit + 2)

    total = min(100, base + contact + evidence + freshness + liquid_fit)
    return total, {
        "匹配度": base,
        "液钙适配": liquid_fit,
        "联系方式": contact,
        "证据强度": evidence,
        "信息时效": freshness,
    }


def merged_lead_payload(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if value not in ("", None, [], {}):
            merged[key] = value
    return merged


def clipped_text(payload: dict[str, Any], key: str, limit: int) -> str:
    return str(payload.get(key) or "").strip()[:limit]


def create_manual_lead(payload: dict[str, Any]) -> dict[str, Any]:
    company = clipped_text(payload, "company", 160)
    if not company:
        raise ValueError("请填写公司名称")
    status = str(payload.get("salesStatus") or "new")
    if status not in SALES_STATUSES:
        raise ValueError("销售状态无效")

    direction = str(payload.get("direction") or "downstream")
    if direction not in DIRECTION_SET:
        direction = "downstream"
    opportunity_role = clipped_text(payload, "opportunityRole", 20)
    if not opportunity_role:
        opportunity_role = (
            "supplier"
            if direction == "upstream"
            else "buyer"
            if direction in {"downstream", "procurement"}
            else "prospect"
        )

    website = clipped_text(payload, "website", 300)
    company_website = clipped_text(payload, "companyWebsite", 300) or website
    lead = {
        "company": company,
        "direction": direction,
        "region": clipped_text(payload, "region", 120),
        "sector": clipped_text(payload, "sector", 120),
        "phone": clipped_text(payload, "phone", 200),
        "email": clipped_text(payload, "email", 160),
        "address": clipped_text(payload, "address", 240),
        "website": website,
        "company_website": company_website,
        "source": "手动新增档案",
        "match_reason": clipped_text(payload, "matchReason", 500) or "手动新增公司档案",
        "use_case": clipped_text(payload, "useCase", 500),
        "pitch": clipped_text(payload, "pitch", 500) or "根据销售记录继续跟进",
        "raw_type": "手动维护",
        "confidence": "人工确认",
        "opportunity_role": opportunity_role,
        "liquid_concentration": clipped_text(payload, "liquidConcentration", 80),
        "monthly_volume": clipped_text(payload, "monthlyVolume", 80),
        "impurity_profile": clipped_text(payload, "impurityProfile", 500),
        "logistics_radius": clipped_text(payload, "logisticsRadius", 80),
        "storage_condition": clipped_text(payload, "storageCondition", 300),
        "commercial_value": clipped_text(payload, "commercialValue", 80),
    }
    persistence = save_leads([lead])
    key = lead_dedupe_key(lead)
    timestamp = now_iso()
    owner = clipped_text(payload, "owner", 80)
    notes = clipped_text(payload, "notes", 5000)
    next_follow_up = clipped_text(payload, "nextFollowUp", 40)

    with DATABASE_LOCK, database_connection() as connection:
        row = connection.execute(
            """
            SELECT id, payload, owner, notes, next_follow_up
            FROM leads WHERE dedupe_key = ?
            """,
            (key,),
        ).fetchone()
        if not row:
            raise RuntimeError("档案保存失败")
        merged_payload = merged_lead_payload(json.loads(row["payload"] or "{}"), lead)
        score, score_details = calculate_lead_score(merged_payload)
        connection.execute(
            """
            UPDATE leads SET sales_status = ?, owner = ?, notes = ?,
                next_follow_up = ?, is_new = 0, updated_at = ?,
                opportunity_role = ?, liquid_concentration = ?,
                monthly_volume = ?, impurity_profile = ?,
                logistics_radius = ?, storage_condition = ?,
                commercial_value = ?, score = ?, score_details = ?, payload = ?
            WHERE id = ?
            """,
            (
                status,
                owner or row["owner"],
                notes or row["notes"],
                next_follow_up or row["next_follow_up"],
                timestamp,
                opportunity_role,
                merged_payload.get("liquid_concentration", ""),
                merged_payload.get("monthly_volume", ""),
                merged_payload.get("impurity_profile", ""),
                merged_payload.get("logistics_radius", ""),
                merged_payload.get("storage_condition", ""),
                merged_payload.get("commercial_value", ""),
                score,
                json.dumps(score_details, ensure_ascii=False),
                json.dumps(merged_payload, ensure_ascii=False),
                row["id"],
            ),
        )
        lead_id = int(row["id"])

    log_activity(
        "create",
        "lead",
        f"手动新增公司档案：{company}",
        lead_id,
        {"status": status, "direction": direction, "owner": owner},
    )
    return {"lead": get_saved_lead(lead_id), "persistence": persistence}


def save_leads(
    leads: list[dict[str, Any]],
    monitor_id: int | None = None,
) -> dict[str, int]:
    created = 0
    updated = 0
    timestamp = now_iso()
    with DATABASE_LOCK, database_connection() as connection:
        valid_monitor_id = monitor_id
        if monitor_id is not None:
            monitor_exists = connection.execute(
                "SELECT 1 FROM monitors WHERE id = ?",
                (monitor_id,),
            ).fetchone()
            if not monitor_exists:
                valid_monitor_id = None
        connection.execute(
            """
            UPDATE leads SET monitor_id = NULL
            WHERE monitor_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM monitors WHERE monitors.id = leads.monitor_id)
            """
        )
        connection.execute(
            """
            UPDATE notifications SET monitor_id = NULL
            WHERE monitor_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM monitors WHERE monitors.id = notifications.monitor_id
              )
            """
        )
        connection.execute(
            """
            UPDATE notifications SET lead_id = NULL
            WHERE lead_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM leads WHERE leads.id = notifications.lead_id)
            """
        )
        for incoming in leads:
            lead = dict(incoming)
            if not lead.get("opportunity_role"):
                lead["opportunity_role"] = (
                    "supplier"
                    if lead.get("direction") == "upstream"
                    else "buyer"
                    if lead.get("direction") in {"downstream", "procurement"}
                    else "prospect"
                )
            key = lead_dedupe_key(lead)
            existing_row = connection.execute(
                "SELECT id, payload FROM leads WHERE dedupe_key = ?",
                (key,),
            ).fetchone()
            existing_payload = json.loads(existing_row["payload"]) if existing_row else {}
            merged = merged_lead_payload(existing_payload, lead)
            score, score_details = calculate_lead_score(merged)
            merged["score"] = score
            if existing_row:
                connection.execute(
                    """
                    UPDATE leads SET
                        company = ?, direction = ?, region = ?, sector = ?,
                        phone = ?, email = ?, company_website = ?, source = ?,
                        score = ?, score_details = ?, payload = ?, last_seen = ?,
                        updated_at = ?, monitor_id = COALESCE(?, monitor_id),
                        opportunity_role = ?, liquid_concentration = ?,
                        monthly_volume = ?, impurity_profile = ?,
                        logistics_radius = ?, storage_condition = ?,
                        commercial_value = ?
                    WHERE id = ?
                    """,
                    (
                        merged.get("company", ""),
                        merged.get("direction", "downstream"),
                        merged.get("region", ""),
                        merged.get("sector", ""),
                        merged.get("phone", ""),
                        merged.get("email", ""),
                        merged.get("company_website", ""),
                        merged.get("source", ""),
                        score,
                        json.dumps(score_details, ensure_ascii=False),
                        json.dumps(merged, ensure_ascii=False),
                        timestamp,
                        timestamp,
                        valid_monitor_id,
                        merged.get("opportunity_role", ""),
                        merged.get("liquid_concentration", ""),
                        merged.get("monthly_volume", ""),
                        merged.get("impurity_profile", ""),
                        merged.get("logistics_radius", ""),
                        merged.get("storage_condition", ""),
                        merged.get("commercial_value", ""),
                        existing_row["id"],
                    ),
                )
                updated += 1
            else:
                connection.execute(
                    """
                    INSERT INTO leads (
                        dedupe_key, company, direction, region, sector, phone,
                        email, company_website, source, score, score_details,
                        payload, first_seen, last_seen, updated_at, monitor_id,
                        opportunity_role, liquid_concentration, monthly_volume,
                        impurity_profile, logistics_radius, storage_condition,
                        commercial_value
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        merged.get("company", ""),
                        merged.get("direction", "downstream"),
                        merged.get("region", ""),
                        merged.get("sector", ""),
                        merged.get("phone", ""),
                        merged.get("email", ""),
                        merged.get("company_website", ""),
                        merged.get("source", ""),
                        score,
                        json.dumps(score_details, ensure_ascii=False),
                        json.dumps(merged, ensure_ascii=False),
                        timestamp,
                        timestamp,
                        timestamp,
                        valid_monitor_id,
                        merged.get("opportunity_role", ""),
                        merged.get("liquid_concentration", ""),
                        merged.get("monthly_volume", ""),
                        merged.get("impurity_profile", ""),
                        merged.get("logistics_radius", ""),
                        merged.get("storage_condition", ""),
                        merged.get("commercial_value", ""),
                    ),
                )
                created += 1
                saved_row = connection.execute(
                    "SELECT id FROM leads WHERE dedupe_key = ?",
                    (key,),
                ).fetchone()
                saved_lead_id = int(saved_row["id"]) if saved_row else None
                connection.execute(
                    """
                    INSERT INTO notifications (
                        type, title, message, lead_id, monitor_id, created_at
                    ) VALUES ('new_lead', ?, ?, ?, ?, ?)
                    """,
                    (
                        "发现新线索",
                        f"{merged.get('company', '新企业')}，智能评分 {score} 分",
                        saved_lead_id,
                        valid_monitor_id,
                        timestamp,
                    ),
                )
    return {"created": created, "updated": updated, "total": len(leads)}


def lead_row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload"] or "{}")
    payload.update(
        {
            "id": row["id"],
            "score": row["score"],
            "score_details": json.loads(row["score_details"] or "{}"),
            "sales_status": row["sales_status"],
            "sales_status_label": SALES_STATUSES.get(row["sales_status"], row["sales_status"]),
            "owner": row["owner"],
            "notes": row["notes"],
            "next_follow_up": row["next_follow_up"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "is_new": bool(row["is_new"]),
            "opportunity_role": row["opportunity_role"],
            "liquid_concentration": row["liquid_concentration"],
            "monthly_volume": row["monthly_volume"],
            "impurity_profile": row["impurity_profile"],
            "logistics_radius": row["logistics_radius"],
            "storage_condition": row["storage_condition"],
            "commercial_value": row["commercial_value"],
        }
    )
    return payload


def list_saved_leads(params: dict[str, str]) -> list[dict[str, Any]]:
    conditions = ["1 = 1"]
    values: list[Any] = []
    status = params.get("status", "")
    direction = params.get("direction", "")
    query = params.get("q", "").strip()
    if status:
        conditions.append("sales_status = ?")
        values.append(status)
    if direction:
        conditions.append("direction = ?")
        values.append(direction)
    if query:
        conditions.append(
            "(company LIKE ? OR sector LIKE ? OR region LIKE ? OR phone LIKE ? OR notes LIKE ?)"
        )
        values.extend([f"%{query}%"] * 5)
    limit = max(1, min(int(params.get("limit", "1000") or 1000), 5000))
    with DATABASE_LOCK, database_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM leads
            WHERE {' AND '.join(conditions)}
            ORDER BY
                CASE sales_status
                    WHEN 'qualified' THEN 1 WHEN 'quoted' THEN 2
                    WHEN 'new' THEN 3 WHEN 'contacted' THEN 4
                    WHEN 'won' THEN 5 ELSE 6
                END,
                score DESC, last_seen DESC
            LIMIT ?
            """,
            (*values, limit),
        ).fetchall()
    return [lead_row_payload(row) for row in rows]


def get_saved_lead(lead_id: int) -> dict[str, Any] | None:
    with DATABASE_LOCK, database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM leads WHERE id = ?",
            (lead_id,),
        ).fetchone()
    return lead_row_payload(row) if row else None


def dashboard_summary() -> dict[str, Any]:
    today = date.today().isoformat()
    with DATABASE_LOCK, database_connection() as connection:
        total = connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        high_score = connection.execute(
            "SELECT COUNT(*) FROM leads WHERE score >= 70"
        ).fetchone()[0]
        due = connection.execute(
            """
            SELECT COUNT(*) FROM leads
            WHERE next_follow_up != '' AND substr(next_follow_up, 1, 10) <= ?
              AND sales_status NOT IN ('won', 'lost')
            """,
            (today,),
        ).fetchone()[0]
        unread = connection.execute(
            "SELECT COUNT(*) FROM notifications WHERE is_read = 0"
        ).fetchone()[0]
        unresolved_events = connection.execute(
            """
            SELECT COUNT(*) FROM system_events
            WHERE resolved = 0 AND level IN ('error', 'warning')
            """
        ).fetchone()[0]
        status_rows = connection.execute(
            "SELECT sales_status, COUNT(*) AS count FROM leads GROUP BY sales_status"
        ).fetchall()
        buyer_count = connection.execute(
            "SELECT COUNT(*) FROM leads WHERE opportunity_role = 'buyer'"
        ).fetchone()[0]
        supplier_count = connection.execute(
            "SELECT COUNT(*) FROM leads WHERE opportunity_role = 'supplier'"
        ).fetchone()[0]
    return {
        "total": total,
        "highScore": high_score,
        "dueFollowUps": due,
        "unreadNotifications": unread,
        "unresolvedEvents": unresolved_events,
        "buyerCount": buyer_count,
        "supplierCount": supplier_count,
        "statuses": {row["sales_status"]: row["count"] for row in status_rows},
    }


def system_overview() -> dict[str, Any]:
    with DATABASE_LOCK, database_connection() as connection:
        event_rows = connection.execute(
            """
            SELECT * FROM system_events
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()
        activity_rows = connection.execute(
            """
            SELECT * FROM activity_log
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()
        source_rows = connection.execute(
            """
            SELECT source,
                   MAX(created_at) AS last_event,
                   SUM(CASE WHEN level = 'error' THEN 1 ELSE 0 END) AS errors,
                   SUM(CASE WHEN level = 'warning' THEN 1 ELSE 0 END) AS warnings
            FROM system_events
            WHERE source != '' AND resolved = 0
            GROUP BY source
            ORDER BY last_event DESC
            """
        ).fetchall()
        active_database_path = TURSO_REPLICA_PATH if turso_active() else DATABASE_PATH
        database_size = active_database_path.stat().st_size if active_database_path.exists() else 0
    return {
        "version": APP_VERSION,
        "databaseSize": database_size,
        "databaseMode": "turso" if turso_active() else "sqlite",
        "tursoConfigured": turso_active(),
        "tursoEnvConfigured": turso_configured(),
        "tursoError": TURSO_RUNTIME_ERROR,
        "amapConfigured": bool(os.getenv("AMAP_KEY")),
        "smsConfigured": sms_configured() or SMS_DEV_MODE,
        "events": [
            {
                "id": row["id"],
                "level": row["level"],
                "category": row["category"],
                "source": row["source"],
                "message": row["message"],
                "details": json.loads(row["details"] or "{}"),
                "resolved": bool(row["resolved"]),
                "createdAt": row["created_at"],
            }
            for row in event_rows
        ],
        "activity": [
            {
                "id": row["id"],
                "action": row["action"],
                "entityType": row["entity_type"],
                "entityId": row["entity_id"],
                "summary": row["summary"],
                "createdAt": row["created_at"],
            }
            for row in activity_rows
        ],
        "sources": [
            {
                "source": row["source"],
                "lastEvent": row["last_event"],
                "errors": row["errors"],
                "warnings": row["warnings"],
            }
            for row in source_rows
        ],
    }


def create_database_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"liquid-calcium-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    backup_path = BACKUP_DIR / filename
    with DATABASE_LOCK:
        if turso_active():
            if not TURSO_REPLICA_PATH.exists():
                raise RuntimeError("Turso 本地副本尚未创建，请稍后再试")
            source = sqlite3.connect(TURSO_REPLICA_PATH, timeout=20)
            target = sqlite3.connect(backup_path)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
        else:
            with database_connection() as source:
                target = sqlite3.connect(backup_path)
                try:
                    source.backup(target)
                finally:
                    target.close()
    backups = sorted(BACKUP_DIR.glob("liquid-calcium-backup-*.db"), reverse=True)
    for stale in backups[10:]:
        stale.unlink(missing_ok=True)
    log_activity("backup", "database", f"创建数据库备份 {filename}")
    return backup_path


def ensure_daily_backup() -> None:
    if turso_active():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today()
    latest = max(
        BACKUP_DIR.glob("liquid-calcium-backup-*.db"),
        key=lambda path: path.stat().st_mtime,
        default=None,
    )
    if latest and datetime.fromtimestamp(latest.stat().st_mtime).date() == today:
        return
    create_database_backup()


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
    return bool(ALIYUN_PNVS_ACCESS_KEY_ID and ALIYUN_PNVS_ACCESS_KEY_SECRET)


def aliyun_percent_encode(value: Any) -> str:
    return quote(str(value), safe="~")


def aliyun_pnvs_request(action: str, action_params: dict[str, Any]) -> dict[str, Any]:
    params = {
        "AccessKeyId": ALIYUN_PNVS_ACCESS_KEY_ID,
        "Action": action,
        "Format": "JSON",
        "RegionId": "cn-hangzhou",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Version": "2017-05-25",
        **action_params,
    }
    canonicalized = "&".join(
        f"{aliyun_percent_encode(key)}={aliyun_percent_encode(params[key])}"
        for key in sorted(params)
    )
    string_to_sign = f"GET&%2F&{aliyun_percent_encode(canonicalized)}"
    digest = hmac.new(
        f"{ALIYUN_PNVS_ACCESS_KEY_SECRET}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    params["Signature"] = base64.b64encode(digest).decode("ascii")
    url = "https://dypnsapi.aliyuncs.com/?" + "&".join(
        f"{aliyun_percent_encode(key)}={aliyun_percent_encode(params[key])}"
        for key in sorted(params)
    )
    req = Request(url, headers={"User-Agent": "CalciumLeadFinder/1.0"})
    with urlopen(req, timeout=15, context=DEFAULT_SSL_CONTEXT) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("Code") != "OK":
        raise RuntimeError(str(result.get("Message") or result.get("Code") or "短信认证请求失败"))
    return result


def send_aliyun_verify_code(phone: str) -> None:
    params: dict[str, Any] = {
        "PhoneNumber": phone,
        "CountryCode": "86",
        "CodeLength": 6,
        "CodeType": 1,
        "ValidTime": SMS_CODE_TTL,
        "Interval": SMS_SEND_COOLDOWN,
        "DuplicatePolicy": 1,
        "ReturnVerifyCode": "false",
        "SignName": ALIYUN_PNVS_SIGN_NAME,
        "TemplateCode": ALIYUN_PNVS_TEMPLATE_CODE,
        "TemplateParam": json.dumps(
            {"code": "##code##", "min": max(1, SMS_CODE_TTL // 60)},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    if ALIYUN_PNVS_SCHEME_NAME:
        params["SchemeName"] = ALIYUN_PNVS_SCHEME_NAME
    aliyun_pnvs_request("SendSmsVerifyCode", params)


def check_aliyun_verify_code(phone: str, code: str) -> bool:
    params: dict[str, Any] = {
        "PhoneNumber": phone,
        "CountryCode": "86",
        "VerifyCode": code,
    }
    if ALIYUN_PNVS_SCHEME_NAME:
        params["SchemeName"] = ALIYUN_PNVS_SCHEME_NAME
    result = aliyun_pnvs_request("CheckSmsVerifyCode", params)
    model = result.get("Model") or {}
    return str(model.get("VerifyResult") or "").upper() == "PASS"


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
    if direction == "competitor":
        return COMPETITOR_SECTOR_LIBRARY
    if direction == "environmental":
        return FLUORIDE_SECTOR_LIBRARY
    if direction == "upstream":
        return UPSTREAM_SECTOR_LIBRARY
    if direction == "procurement":
        return PROCUREMENT_SECTOR_LIBRARY
    return SECTOR_LIBRARY


def selected_sectors(ids: list[str] | None, direction: str) -> dict[str, dict[str, Any]]:
    library = get_sector_library(direction)
    if not ids:
        if direction == "competitor":
            ids = ["liquid", "anhydrous", "dihydrate", "deicing", "desiccant"]
        elif direction == "environmental":
            ids = ["fluorochemicals", "rare_earth", "phosphorus", "surface_treatment", "electronics"]
        elif direction == "upstream":
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


def likely_upstream_company(name: str, raw_type: str) -> bool:
    text = f"{name} {raw_type}"
    if any(word in text for word in UPSTREAM_NON_COMPANY_WORDS):
        return any(word in name for word in ["公司", "集团", "厂"])
    return any(word in text for word in UPSTREAM_COMPANY_HINTS)


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


def visible_html_text(value: str) -> str:
    cleaned = re.sub(
        r"<(script|style|noscript)\b[^>]*>.*?</\1>",
        " ",
        value,
        flags=re.S | re.I,
    )
    return html_text(cleaned)


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


def post_form_html(
    url: str,
    data: dict[str, str],
    timeout: int = 18,
    referer: str = "",
) -> str:
    req = Request(
        url,
        data=urlencode(data).encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": referer or url,
        },
    )
    with urlopen(req, timeout=timeout, context=DEFAULT_SSL_CONTEXT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def post_json(url: str, data: dict[str, Any], timeout: int = 18, referer: str = "") -> dict[str, Any]:
    req = Request(
        url,
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
            "Referer": referer or url,
        },
    )
    with urlopen(req, timeout=timeout, context=DEFAULT_SSL_CONTEXT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


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


def html_table_value(page: str, label: str) -> str:
    match = re.search(
        rf"<td[^>]*class=['\"]title['\"][^>]*>\s*{re.escape(label)}\s*</td>"
        rf"\s*<td[^>]*>(.*?)</td>",
        page,
        re.S | re.I,
    )
    return html_text(match.group(1)) if match else ""


def first_public_phone(value: str) -> str:
    match = re.search(
        r"(?:\+?86[- ]?)?(?:0\d{2,3}[- ]?)?\d{7,8}(?:-\d{1,6})?|1[3-9]\d{9}",
        value,
    )
    return match.group(0).strip() if match else value.strip()


def extract_official_notice_detail(page: str) -> dict[str, str]:
    text = html_text(page)

    def text_value(patterns: list[str]) -> str:
        for pattern in patterns:
            match = re.search(pattern, text, re.S)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip(" ：:;；")
        return ""

    phone = html_table_value(page, "采购单位联系方式") or text_value(
        [r"采购人信息.*?联系方式[:：]\s*([0-9\-（）()转 ]{7,30})"]
    )
    return {
        "company": html_table_value(page, "采购单位")
        or text_value(
            [
                r"[1１][.．、]?\s*采购人信息.*?名\s*称[:：]\s*(.+?)(?=地\s*址[:：])",
            ]
        ),
        "address": html_table_value(page, "采购单位地址")
        or text_value([r"采购人信息.*?地\s*址[:：]\s*(.+?)(?=联系方式[:：])"]),
        "phone": first_public_phone(phone),
        "agency": html_table_value(page, "代理机构名称")
        or text_value(
            [
                r"[2２][.．、]?\s*采购(?:代理机构|执行机构)信息.*?名\s*称[:：]\s*(.+?)(?=地\s*址[:：])",
            ]
        ),
        "contact": html_table_value(page, "项目联系人")
        or text_value(
            [
                r"项目联系人[:：]\s*(.+?)(?=项目联系电话|电话[:：])",
                r"文件联系人及电话[:：]\s*经办人[:：]?\s*(\S+)",
            ]
        ),
        "deadline": html_table_value(page, "开标时间")
        or text_value([r"(?:截止时间|提交投标文件截止时间)[:：]\s*(.+?)(?=（北京时间|投标地点|开标地点)"]),
        "budget": html_table_value(page, "预算金额")
        or text_value(
            [
                r"预算金额[:：]\s*([￥¥]?[0-9,.]+\s*(?:万元|元)?)",
                r"中标[（(]成交[）)]金额[:：]\s*(?:人民币)?\s*([0-9,.]+\s*(?:万元|元)?)",
            ]
        ),
    }


def region_selected(region: str, regions: list[str]) -> bool:
    if not region or not regions:
        return True
    normalized = region.replace("省", "").replace("市", "").replace("自治区", "")
    return any(
        item in {"全国", "中央"}
        or item.replace("省", "").replace("市", "").replace("自治区", "") in normalized
        for item in regions
    )


def company_from_notice_title(title: str) -> str:
    candidates = re.split(
        r"(?:20\d{2}年|关于|融雪剂|氯化钙|采购项目|公开招标|竞争性|询价)",
        title,
        maxsplit=1,
    )
    company = candidates[0].strip(" -—：:")
    return company if len(company) >= 4 else "采购单位待核验"


def company_from_keyword_notice_title(title: str, keyword: str) -> str:
    prefix = title.split(keyword, 1)[0].strip(" -—：:（）()【】")
    return prefix if len(prefix) >= 4 else company_from_notice_title(title)


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
                    if not likely_upstream_company(name, raw_type):
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


CCGP_NOTICE_CHANNELS: dict[str, list[tuple[str, str]]] = {
    "purchase": [
        ("dfgg/jzxcs", "竞争性磋商公告"),
        ("dfgg/xjgg", "询价公告"),
        ("zygg/jzxcs", "中央竞争性磋商公告"),
        ("zygg/xjgg", "中央询价公告"),
    ],
    "tender": [
        ("dfgg/gkzb", "地方公开招标公告"),
        ("zygg/gkzb", "中央公开招标公告"),
    ],
    "award": [
        ("dfgg/zbgg", "地方中标公告"),
        ("dfgg/cjgg", "地方成交公告"),
        ("zygg/zbgg", "中央中标公告"),
        ("zygg/cjgg", "中央成交公告"),
    ],
}


def ccgp_list_url(channel: str, page: int) -> str:
    suffix = "" if page == 0 else f"index_{page}.htm"
    return f"https://www.ccgp.gov.cn/cggg/{channel}/{suffix}"


def parse_ccgp_list(page: str, base_url: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"<li>\s*<a\s+href=['\"]([^'\"]+)['\"][^>]*title=['\"]([^'\"]+)['\"][^>]*>.*?</a>"
        r"\s*发布时间：<em>(.*?)</em>\s*地域：<em>(.*?)</em>\s*采购人：<em>(.*?)</em>",
        re.S | re.I,
    )
    return [
        {
            "url": urljoin(base_url, match.group(1).strip()),
            "title": html.unescape(match.group(2).strip()),
            "date": html_text(match.group(3)),
            "region": html_text(match.group(4)),
            "company": html_text(match.group(5)),
        }
        for match in pattern.finditer(page)
    ]


def collect_ccgp_notices(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    notice_type_ids: list[str],
    date_window_id: str,
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    product_keywords = list(
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
    channels = [
        (kind, channel, label)
        for kind in (notice_type_ids or list(CCGP_NOTICE_CHANNELS))
        for channel, label in CCGP_NOTICE_CHANNELS.get(kind, [])
    ]
    page_limits = {"3d": 2, "10d": 4, "30d": 6, "90d": 8}
    page_limit = page_limits.get(date_window_id, 4)
    list_jobs = [
        (kind, channel, label, page)
        for kind, channel, label in channels
        for page in range(page_limit)
    ]
    _, max_days = PROCUREMENT_DATE_WINDOWS.get(date_window_id, PROCUREMENT_DATE_WINDOWS["10d"])
    cutoff = date.today() - timedelta(days=max_days)
    matches: dict[str, tuple[dict[str, str], str, dict[str, Any], str]] = {}
    errors: list[str] = []
    completed = 0
    if progress_callback:
        progress_callback(0, len(list_jobs), 0, 0, "正在扫描中国政府采购网公告")

    def fetch_list(job: tuple[str, str, str, int]) -> tuple[tuple[str, str, str, int], str]:
        _, channel, _, page_number = job
        url = ccgp_list_url(channel, page_number)
        return job, fetch_html(url, timeout=18)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_list, job): job for job in list_jobs}
        for future in as_completed(futures):
            kind, channel, label, page_number = futures[future]
            try:
                job, page = future.result()
                base_url = ccgp_list_url(job[1], job[3])
                for record in parse_ccgp_list(page, base_url):
                    try:
                        notice_day = date.fromisoformat(record["date"][:10])
                    except ValueError:
                        notice_day = date.today()
                    if notice_day < cutoff or not region_selected(record["region"], regions):
                        continue
                    keyword = next(
                        (item for item in product_keywords if item and item in record["title"]),
                        "",
                    )
                    if not keyword:
                        continue
                    sector = next(
                        (
                            item
                            for item in sectors.values()
                            if keyword in item.get("keywords", []) or keyword in custom_keywords
                        ),
                        next(iter(sectors.values())),
                    )
                    matches.setdefault(record["url"], (record, kind, sector, label))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"中国政府采购网/{label}/第{page_number + 1}页：{exc}")
            completed += 1
            if progress_callback:
                progress_callback(
                    completed,
                    len(list_jobs),
                    len(matches),
                    0,
                    f"正在扫描中国政府采购网：{label}",
                )

    selected = list(matches.values())[:40]
    leads: list[Lead] = []

    def build_lead(item: tuple[dict[str, str], str, dict[str, Any], str]) -> Lead:
        record, kind, sector, label = item
        detail: dict[str, str] = {}
        try:
            detail = extract_official_notice_detail(fetch_html(record["url"], timeout=18))
        except Exception:  # noqa: BLE001 - list data is still a valid official lead.
            detail = {}
        company = detail.get("company") or record["company"] or company_from_notice_title(record["title"])
        score = int(sector["score"]) - (6 if kind == "award" else 0)
        links = build_search_links(company, record["region"], record["title"])
        deadline = detail.get("deadline") or ""
        return Lead(
            company=company,
            region=record["region"],
            sector=PROCUREMENT_NOTICE_TYPES.get(kind, label),
            source="中国政府采购网",
            score=max(score, 1),
            phone=detail.get("phone") or "",
            address=detail.get("address") or "",
            website=record["url"],
            use_case="财政部指定政府采购信息发布媒体的公开公告",
            pitch=(f"截止时间：{deadline}；" if deadline else "") + sector["pitch"],
            match_reason=f"{record['date']}；来源栏目：{label}",
            search_url=record["url"],
            raw_type=label,
            qcc_url=links["qcc"],
            direction="procurement",
            process_basis="中国政府采购网官方公告栏目自动采集",
            confidence="官方公告",
            project_title=record["title"],
            notice_date=record["date"],
            contact_name=detail.get("contact") or "",
            agency=detail.get("agency") or "",
            deadline=deadline,
            budget=detail.get("budget") or "",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(build_lead, item): item for item in selected}
        for index, future in enumerate(as_completed(futures), start=1):
            try:
                leads.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(f"中国政府采购网公告解析失败：{exc}")
            if progress_callback:
                progress_callback(
                    len(list_jobs) + index,
                    len(list_jobs) + len(selected),
                    len(leads),
                    len([lead for lead in leads if lead.phone]),
                    "正在读取中国政府采购网公告详情",
                )
    return leads, errors, len(list_jobs) + len(selected)


def fetch_zycg_records(page_number: int = 1) -> list[dict[str, Any]]:
    landing_url = "https://www.zycg.gov.cn/freecms/site/zygjjgzfcgzx/cggg/index.html"
    landing_request = Request(landing_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(landing_request, timeout=18, context=DEFAULT_SSL_CONTEXT) as response:
        cookies = response.headers.get_all("Set-Cookie") or []
        response.read()
    cookie_header = "; ".join(cookie.split(";", 1)[0] for cookie in cookies)
    query = urlencode(
        {
            "siteId": "6f5243ee-d4d9-4b69-abbd-1e40576ccd7d",
            "channel": "d0e7c5f4-b93e-4478-b7fe-61110bb47fd5",
            "currPage": str(page_number),
            "pageSize": "15",
            "title": "",
            "implementWay": "1",
            "noticeType": "1,2,3,31,32,52,57,61",
        }
    )
    request = Request(
        f"https://www.zycg.gov.cn/freecms/rest/v1/notice/selectInfoMore.do?{query}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": landing_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Cookie": cookie_header,
        },
    )
    with urlopen(request, timeout=18, context=DEFAULT_SSL_CONTEXT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if str(payload.get("code")) != "200":
        raise RuntimeError(str(payload.get("msg") or "公告列表查询失败"))
    return payload.get("data") or []


def collect_zycg_notices(
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    notice_type_ids: list[str],
    date_window_id: str,
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    keyword_items: list[tuple[str, dict[str, Any]]] = [
        (keyword, sector)
        for sector in sectors.values()
        for keyword in list(dict.fromkeys([*custom_keywords, *sector.get("keywords", [])]))
    ]
    _, max_days = PROCUREMENT_DATE_WINDOWS.get(date_window_id, PROCUREMENT_DATE_WINDOWS["10d"])
    cutoff = date.today() - timedelta(days=max_days)
    records: dict[str, tuple[dict[str, Any], str, dict[str, Any]]] = {}
    errors: list[str] = []
    page_limits = {"3d": 8, "10d": 14, "30d": 20, "90d": 24}
    page_limit = page_limits.get(date_window_id, 14)
    if progress_callback:
        progress_callback(0, page_limit, 0, 0, "正在查询中央政府采购网")

    pages_read = 0
    for page_number in range(1, page_limit + 1):
        try:
            page_records = fetch_zycg_records(page_number)
            pages_read += 1
            stop_after_page = False
            for record in page_records:
                title = str(record.get("title") or "")
                notice_date = str(record.get("addtimeStr") or "")
                try:
                    notice_day = date.fromisoformat(notice_date[:10])
                    if notice_day < cutoff:
                        stop_after_page = True
                        continue
                except ValueError:
                    pass
                keyword_sector = next(
                    (
                        (keyword, sector)
                        for keyword, sector in keyword_items
                        if keyword and keyword in title
                    ),
                    None,
                )
                if not keyword_sector:
                    continue
                kind = website_notice_kind_from_title(title)
                if notice_type_ids and kind not in notice_type_ids:
                    continue
                url = urljoin("https://www.zycg.gov.cn", str(record.get("pageurl") or ""))
                if url:
                    keyword, sector = keyword_sector
                    records.setdefault(url, (record, keyword, sector))
            if progress_callback:
                progress_callback(
                    page_number,
                    page_limit,
                    len(records),
                    0,
                    f"正在查询中央政府采购网：第{page_number}页",
                )
            if stop_after_page or not page_records:
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"中央政府采购网/第{page_number}页：{exc}")
            break

    leads: list[Lead] = []
    selected = list(records.items())[:30]

    def build_lead(item: tuple[str, tuple[dict[str, Any], str, dict[str, Any]]]) -> Lead:
        url, (record, keyword, sector) = item
        detail: dict[str, str] = {}
        try:
            detail = extract_official_notice_detail(fetch_html(url, timeout=18))
        except Exception:  # noqa: BLE001
            detail = {}
        title = str(record.get("title") or "")
        kind = website_notice_kind_from_title(title)
        company = detail.get("company") or company_from_notice_title(title)
        score = int(sector["score"]) - (6 if kind == "award" else 0)
        links = build_search_links(company, "中央", keyword)
        return Lead(
            company=company,
            region="中央",
            sector=PROCUREMENT_NOTICE_TYPES.get(kind, "采购公告"),
            source="中央政府采购网",
            score=max(score, 1),
            phone=detail.get("phone") or "",
            address=detail.get("address") or "",
            website=url,
            use_case="中央国家机关政府采购中心公开项目",
            pitch=sector["pitch"],
            match_reason=f"{record.get('addtimeStr') or '日期待核验'}；关键词：{keyword}",
            search_url=url,
            raw_type="中央政府采购",
            qcc_url=links["qcc"],
            direction="procurement",
            process_basis="中央政府采购网官方查询接口",
            confidence="官方公告",
            project_title=title,
            notice_date=str(record.get("addtimeStr") or ""),
            contact_name=detail.get("contact") or "",
            agency=detail.get("agency") or "",
            deadline=detail.get("deadline") or "",
            budget=detail.get("budget") or "",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(build_lead, item): item for item in selected}
        for future in as_completed(futures):
            try:
                leads.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(f"中央政府采购网公告解析失败：{exc}")
    return leads, errors, pages_read + len(selected)


def procurement_keyword_items(
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    fallback_sector = next(iter(sectors.values()))
    for keyword in custom_keywords:
        if keyword and keyword not in seen:
            seen.add(keyword)
            items.append((keyword, fallback_sector))
    for sector in sectors.values():
        for keyword in sector.get("keywords", []):
            if keyword and keyword not in seen:
                seen.add(keyword)
                items.append((keyword, sector))
    return items


def parse_shandong_procurement_list(page: str, base_url: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r'<div class="article-list3-t">\s*<a href="([^"]+)"[^>]*>(.*?)</a>'
        r'\s*<div class="list-times">(.*?)</div>',
        re.S | re.I,
    )
    records: list[dict[str, str]] = []
    for match in pattern.finditer(page):
        title = html_text(match.group(2))
        region_match = re.match(r"【([^】]+)】", title)
        title = re.sub(r"^【[^】]+】", "", title).strip()
        records.append(
            {
                "url": urljoin(base_url, match.group(1).strip()),
                "title": title,
                "date": html_text(match.group(3)),
                "region": region_match.group(1) if region_match else "山东",
            }
        )
    return records


def collect_shandong_notices(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    notice_type_ids: list[str],
    date_window_id: str,
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    if not region_selected("山东", regions):
        return [], [], 0
    keyword_items = procurement_keyword_items(sectors, custom_keywords)
    channels: list[tuple[str, str]] = []
    if any(kind in notice_type_ids for kind in ["purchase", "tender"]):
        channels.append(("queryContent-jyxxgg.jspx", "采购/资审公告"))
    if "award" in notice_type_ids:
        channels.append(("queryContent-jyxxgs.jspx", "交易结果公示"))
    jobs = [(keyword, sector, channel, label) for keyword, sector in keyword_items for channel, label in channels]
    window_days = str(PROCUREMENT_DATE_WINDOWS.get(date_window_id, PROCUREMENT_DATE_WINDOWS["10d"])[1])
    base_url = "https://ggzyjy.shandong.gov.cn/"
    records: dict[str, tuple[dict[str, str], str, dict[str, Any], str]] = {}
    errors: list[str] = []
    if progress_callback:
        progress_callback(0, len(jobs), 0, 0, "正在查询山东省公共资源交易平台")

    def fetch_job(
        job: tuple[str, dict[str, Any], str, str],
    ) -> tuple[tuple[str, dict[str, Any], str, str], str]:
        keyword, _, channel, _ = job
        page = post_form_html(
            urljoin(base_url, channel),
            {
                "title": keyword,
                "origin": "",
                "inDates": window_days,
                "channelId": "79",
                "ext": "",
            },
            referer=urljoin(base_url, channel),
        )
        return job, page

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(fetch_job, job): job for job in jobs}
        for completed, future in enumerate(as_completed(futures), start=1):
            keyword, sector, channel, label = futures[future]
            try:
                _, page = future.result()
                for record in parse_shandong_procurement_list(page, base_url):
                    kind = website_notice_kind_from_title(record["title"])
                    if kind not in notice_type_ids:
                        continue
                    records.setdefault(record["url"], (record, keyword, sector, label))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"山东省公共资源交易平台/{keyword}：{exc}")
            if progress_callback:
                progress_callback(
                    completed,
                    len(jobs),
                    len(records),
                    0,
                    f"正在查询山东省平台：{keyword}",
                )

    leads: list[Lead] = []
    for record, keyword, sector, label in list(records.values())[:40]:
        kind = website_notice_kind_from_title(record["title"])
        company = company_from_keyword_notice_title(record["title"], keyword)
        score = int(sector["score"]) - (6 if kind == "award" else 0)
        links = build_search_links(company, "山东", keyword)
        leads.append(
            Lead(
                company=company,
                region=f"山东/{record['region']}",
                sector=PROCUREMENT_NOTICE_TYPES.get(kind, label),
                source="山东省公共资源交易平台",
                score=max(score, 1),
                website=record["url"],
                use_case="山东省公共资源交易平台政府采购公开公告",
                pitch=sector["pitch"],
                match_reason=f"{record['date']}；关键词：{keyword}",
                search_url=record["url"],
                raw_type=label,
                qcc_url=links["qcc"],
                direction="procurement",
                process_basis="山东省公共资源交易平台官方检索",
                confidence="官方公告",
                project_title=record["title"],
                notice_date=record["date"],
            )
        )
    return leads, errors, len(jobs)


def fetch_sichuan_procurement_records(
    keyword: str,
    start_date: date,
    category: str,
) -> list[dict[str, Any]]:
    condition: list[dict[str, Any]] = [
        {
            "fieldName": "categorynum",
            "equal": category,
            "notEqual": None,
            "equalList": None,
            "notEqualList": None,
            "isLike": True,
            "likeType": 2,
        }
    ]
    condition.append(
        {
            "fieldName": "titlenew",
            "equal": keyword,
            "notEqual": None,
            "equalList": None,
            "notEqualList": None,
            "isLike": True,
            "likeType": 0,
        }
    )
    payload = {
        "token": "",
        "pn": 0,
        "rn": 40,
        "sdt": "",
        "edt": "",
        "wd": "",
        "inc_wd": "",
        "exc_wd": "",
        "fields": "",
        "cnum": "",
        "sort": '{"ordernum":"0","webdate":"0"}',
        "ssort": "",
        "cl": 10000,
        "terminal": "",
        "condition": condition,
        "time": [
            {
                "fieldName": "webdate",
                "startTime": f"{start_date.isoformat()} 00:00:00",
                "endTime": f"{date.today().isoformat()} 23:59:59",
            }
        ],
        "highlights": "",
        "statistics": None,
        "unionCondition": None,
        "accuracy": "",
        "noParticiple": "1",
        "searchRange": None,
        "noWd": True,
    }
    response = post_json(
        "https://ggzyjy.sc.gov.cn/inteligentsearch/rest/esinteligentsearch/getFullTextDataNew",
        payload,
        referer="https://ggzyjy.sc.gov.cn/jyxx/transactionInfo.html",
    )
    return ((response.get("result") or {}).get("records") or [])


def collect_sichuan_notices(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    notice_type_ids: list[str],
    date_window_id: str,
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    if not region_selected("四川", regions):
        return [], [], 0
    keyword_items = procurement_keyword_items(sectors, custom_keywords)
    categories: list[str] = []
    if any(kind in notice_type_ids for kind in ["purchase", "tender"]):
        categories.append("002002001")
    if "award" in notice_type_ids:
        categories.append("002002003")
    jobs = [
        (keyword, sector, category)
        for keyword, sector in keyword_items
        for category in categories
    ]
    _, max_days = PROCUREMENT_DATE_WINDOWS.get(date_window_id, PROCUREMENT_DATE_WINDOWS["10d"])
    start_date = date.today() - timedelta(days=max_days)
    records: dict[str, tuple[dict[str, Any], str, dict[str, Any]]] = {}
    errors: list[str] = []
    if progress_callback:
        progress_callback(0, len(jobs), 0, 0, "正在查询四川省公共资源交易信息网")

    def fetch_job(
        item: tuple[str, dict[str, Any], str],
    ) -> tuple[tuple[str, dict[str, Any], str], list[dict[str, Any]]]:
        return item, fetch_sichuan_procurement_records(item[0], start_date, item[2])

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_job, item): item for item in jobs}
        for completed, future in enumerate(as_completed(futures), start=1):
            keyword, sector, category = futures[future]
            try:
                _, page_records = future.result()
                for record in page_records:
                    title = str(record.get("title") or record.get("titlenew") or "")
                    kind = website_notice_kind_from_title(title)
                    if kind not in notice_type_ids:
                        continue
                    url = urljoin("https://ggzyjy.sc.gov.cn", str(record.get("linkurl") or ""))
                    if url:
                        records.setdefault(url, (record, keyword, sector))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"四川省公共资源交易信息网/{keyword}/{category}：{exc}")
            if progress_callback:
                progress_callback(
                    completed,
                    len(jobs),
                    len(records),
                    0,
                    f"正在查询四川省平台：{keyword}",
                )

    leads: list[Lead] = []
    for url, (record, keyword, sector) in list(records.items())[:40]:
        title = str(record.get("title") or record.get("titlenew") or "")
        content = str(record.get("content") or "")
        detail = extract_official_notice_detail(content)
        kind = website_notice_kind_from_title(title)
        company = detail.get("company") or company_from_notice_title(title)
        score = int(sector["score"]) - (6 if kind == "award" else 0)
        links = build_search_links(company, "四川", keyword)
        leads.append(
            Lead(
                company=company,
                region="四川",
                sector=PROCUREMENT_NOTICE_TYPES.get(kind, "采购公告"),
                source="四川省公共资源交易信息网",
                score=max(score, 1),
                phone=detail.get("phone") or "",
                address=detail.get("address") or "",
                website=url,
                use_case="四川省公共资源交易信息网政府采购公开公告",
                pitch=sector["pitch"],
                match_reason=f"{record.get('webdate') or '日期待核验'}；关键词：{keyword}",
                search_url=url,
                raw_type="四川政府采购",
                qcc_url=links["qcc"],
                direction="procurement",
                process_basis="四川省公共资源交易信息网官方检索接口",
                confidence="官方公告",
                project_title=title,
                notice_date=str(record.get("webdate") or ""),
                contact_name=detail.get("contact") or "",
                agency=detail.get("agency") or "",
                deadline=detail.get("deadline") or "",
                budget=detail.get("budget") or "",
            )
        )
    return leads, errors, len(jobs)


PERMIT_LIST_URL = (
    "https://permit.mee.gov.cn/perxxgkinfo/syssb/xkgg/"
    "xkgg!licenseInformation.action"
)
PERMIT_BASE_URL = "https://permit.mee.gov.cn"


def normalized_permit_region(value: str) -> str:
    normalized = (
        value.replace("省", "")
        .replace("市", "")
        .replace("壮族自治区", "")
        .replace("回族自治区", "")
        .replace("维吾尔自治区", "")
        .replace("自治区", "")
        .strip()
    )
    return normalized


def permit_region_code(value: str) -> str:
    normalized = normalized_permit_region(value)
    return next(
        (code for name, code in PERMIT_REGION_CODES.items() if name in normalized or normalized in name),
        "",
    )


def parse_permit_list(page: str) -> list[dict[str, str]]:
    row_pattern = re.compile(r"<tr>\s*(.*?)\s*</tr>", re.S | re.I)
    cell_pattern = re.compile(r"<td([^>]*)>(.*?)</td>", re.S | re.I)
    records: list[dict[str, str]] = []
    for row_match in row_pattern.finditer(page):
        row = row_match.group(1)
        cells = cell_pattern.findall(row)
        if len(cells) < 9:
            continue
        values = [html_text(content) for _, content in cells]
        detail_match = re.search(
            r'href=["\']([^"\']*xkgk=getxxgkContent[^"\']+)["\']',
            row,
            re.I,
        )
        if not detail_match or not values[2] or values[2] == "许可证编号":
            continue
        records.append(
            {
                "province": values[0],
                "city": values[1],
                "permit_number": values[2],
                "company": values[3],
                "industry": values[4],
                "validity": values[5],
                "issue_date": values[6],
                "management": values[7],
                "url": urljoin(PERMIT_BASE_URL, html.unescape(detail_match.group(1))),
            }
        )
    return records


def fetch_permit_records(
    region: str,
    keyword: str,
    page_number: int,
) -> list[dict[str, str]]:
    page = post_form_html(
        PERMIT_LIST_URL,
        {
            "page.pageNo": str(page_number),
            "page.orderBy": "",
            "page.order": "",
            "tempReportKey": "",
            "province": permit_region_code(region),
            "city": "",
            "management": "",
            "registerentername": keyword,
            "xkznum": "",
            "treadname": "",
            "treadcode": "",
            "publishtime": "",
        },
        timeout=8,
        referer=PERMIT_LIST_URL,
    )
    if "错误页" in page and "请您访问" in page:
        raise RuntimeError("官方平台当前限制程序直连")
    return parse_permit_list(page)


def extract_permit_detail(page: str) -> dict[str, str]:
    text = html_text(page)

    def capture(pattern: str) -> str:
        match = re.search(pattern, text, re.S)
        return re.sub(r"\s+", " ", match.group(1)).strip(" ：:") if match else ""

    return {
        "address": capture(r"生产经营场所地址[:：]\s*(.+?)(?=行业类别[:：])"),
        "industry": capture(r"行业类别[:：]\s*(.+?)(?=所在地区[:：])"),
        "region": capture(r"所在地区[:：]\s*(.+?)(?=发证机关[:：])"),
        "issuer": capture(r"发证机关[:：]\s*(.+?)(?=排污许可证正本|许可证编号|$)"),
        "air_pollutants": capture(r"大气主要污染物种类[:：]\s*(.+?)(?=大气污染物排放规律|$)"),
        "water_pollutants": capture(r"废水主要污染物种类[:：]\s*(.+?)(?=废水污染物排放规律|$)"),
    }


def environmental_sector_for_record(
    selected: dict[str, dict[str, Any]],
    industry: str,
    company: str,
    fallback_id: str,
) -> tuple[str, dict[str, Any], list[str]]:
    text = f"{company} {industry}"
    best_id = fallback_id
    best_sector = selected[fallback_id]
    best_hits: list[str] = []
    for sector_id, sector in selected.items():
        indicators = list(dict.fromkeys([*sector.get("strict_indicators", []), *sector.get("indicators", [])]))
        hits = [indicator for indicator in indicators if indicator and indicator in text]
        if len(hits) > len(best_hits):
            best_id = sector_id
            best_sector = sector
            best_hits = hits
    return best_id, best_sector, best_hits


def indexed_fluoride_permit_leads(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
) -> list[Lead]:
    normalized_regions = [normalized_permit_region(region) for region in regions]
    leads: list[Lead] = []
    for record in FLUORIDE_PERMIT_INDEX:
        if record["sector_id"] not in sectors:
            continue
        record_region = normalized_permit_region(record["region"])
        if normalized_regions and not any(
            region and (region in record_region or record_region in region)
            for region in normalized_regions
        ):
            continue
        sector = sectors[record["sector_id"]]
        detail_url = (
            f"{PERMIT_BASE_URL}/perxxgkinfo/xkgkAction!xkgk.action"
            f"?dataid={record['data_id']}&xkgk=getxxgkContent"
        )
        links = build_search_links(record["company"], record["region"], record["industry"])
        leads.append(
            Lead(
                company=record["company"],
                region=record["region"],
                sector=sector["name"],
                source="全国排污许可证管理信息平台（已核验索引）",
                score=min(int(sector["score"]) + 44, 100),
                website=detail_url,
                use_case="官方许可索引明确记录废水氟化物；建议打开许可详情复核最新状态。",
                pitch=sector["pitch"],
                match_reason=f"废水主要污染物：{record['water_pollutants']}",
                search_url=detail_url,
                raw_type=record["industry"],
                qcc_url=links["qcc"],
                company_website=record.get("official_website", ""),
                poi_id=record["permit_number"] or f"索引-{record['data_id'][:8]}",
                direction="environmental",
                process_basis=(
                    f"官方许可废水主要污染物：{record['water_pollutants']}；"
                    f"许可行业：{record['industry']}"
                ),
                confidence="官方许可/废水含氟（已核验索引）",
            )
        )
    return leads


def parse_360_environmental_results(page: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for block in re.findall(r'<li class="[^"]*\bres-list\b[^"]*"[^>]*>(.*?)</li>', page, re.S | re.I):
        title_match = re.search(r"<h3[^>]*>.*?<a[^>]*>(.*?)</a>", block, re.S | re.I)
        url_match = re.search(r'data-mdurl="([^"]+)"', block, re.I)
        if not title_match or not url_match:
            continue
        description_match = re.search(
            r'<p[^>]*class="[^"]*\bres-desc\b[^"]*"[^>]*>(.*?)</p>',
            block,
            re.S | re.I,
        )
        results.append(
            {
                "title": html_text(title_match.group(1)),
                "url": html.unescape(url_match.group(1)),
                "description": html_text(description_match.group(1)) if description_match else "",
            }
        )
    return results


def extract_competitor_company(value: str) -> str:
    text = re.sub(r"\s+", " ", html.unescape(value or "")).strip()
    suffix = r"(?:股份有限公司|有限责任公司|集团有限公司|有限公司)"
    candidates = re.findall(
        rf"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{{2,56}}{suffix}",
        text,
    )
    for candidate in sorted(candidates, key=len, reverse=True):
        candidate = re.split(r"以及|与", candidate)[-1]
        cleaned = re.split(r"[_|｜—\-：:]", candidate)[-1]
        cleaned = re.split(
            r"厂家直销|生产厂家|供应商|批发|价格|产品|首页|公司简介",
            cleaned,
        )[-1]
        if 6 <= len(cleaned) <= 64:
            return cleaned
    return ""


def competitor_signals(text: str) -> tuple[list[str], list[str]]:
    compact = re.sub(r"\s+", " ", text or "")
    industries = [
        industry
        for industry, signals in COMPETITOR_APPLICATION_SIGNALS.items()
        if any(signal in compact for signal in signals)
    ]
    keywords = [keyword for keyword in COMPETITOR_KEYWORD_SIGNALS if keyword in compact]
    return industries, keywords


def competitor_reverse_pitch(
    industries: list[str],
    regions: list[str],
    keywords: list[str],
) -> str:
    industry_text = "、".join(industries[:4]) or "融雪、干燥剂、水处理等"
    region_text = "、".join(regions[:4]) or "同行重点覆盖地区"
    keyword_text = "、".join(keywords[:5]) or "氯化钙用途词"
    return (
        f"反向开发{region_text}的{industry_text}企业；"
        f"优先组合“地区 + {keyword_text} + 厂家/采购/项目”检索，并核实其现供应商、月用量和到货半径。"
    )


def collect_competitor_intelligence(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    source_ids: list[str],
    custom_keywords: list[str],
    deep_scan: bool,
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    sources = {
        source_id: COMPETITOR_SOURCE_LIBRARY[source_id]
        for source_id in source_ids
        if source_id in COMPETITOR_SOURCE_LIBRARY
    }
    if not sources:
        return [], ["请至少选择一个竞品信息来源"], 0

    keyword_items = [
        (sector_id, sector, keyword)
        for sector_id, sector in sectors.items()
        for keyword in sector.get("keywords", [])[:2]
    ]
    keyword_items.extend(
        ("custom", {"name": "自定义关键词", "score": 18}, keyword)
        for keyword in custom_keywords[:4]
    )
    keyword_items = keyword_items[:12]
    search_regions = regions[:6] or ["全国"]
    jobs: list[tuple[str, dict[str, str], str, str, dict[str, Any]]] = []
    for source_id, source in sources.items():
        for index, region in enumerate(search_regions):
            if not keyword_items:
                break
            _, sector, keyword = keyword_items[index % len(keyword_items)]
            jobs.append((source_id, source, region, keyword, sector))
    jobs = jobs[:24]
    errors: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    if progress_callback:
        progress_callback(0, len(jobs), 0, 0, "正在检索同行供应商和公开产品页面")

    def fetch_job(
        job: tuple[str, dict[str, str], str, str, dict[str, Any]],
    ) -> tuple[tuple[str, dict[str, str], str, str, dict[str, Any]], list[dict[str, str]], str]:
        source_id, source, region, keyword, sector = job
        query_parts = [
            source.get("site", ""),
            region,
            keyword,
            "厂家 供应商 应用 客户 案例",
        ]
        query = " ".join(part for part in query_parts if part)
        query_url = "https://www.so.com/s?" + urlencode({"q": query})
        return job, parse_360_environmental_results(fetch_html(query_url, timeout=20)), query_url

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_job, job): job for job in jobs}
        for completed, future in enumerate(as_completed(futures), start=1):
            source_id, source, region, keyword, sector = futures[future]
            try:
                _, results, query_url = future.result()
                for result in results[:12]:
                    evidence_text = f"{result['title']} {result['description']}"
                    if "氯化钙" not in evidence_text:
                        continue
                    company = extract_competitor_company(evidence_text)
                    if not company:
                        continue
                    host = (urlparse(result["url"]).hostname or "").lower()
                    if source_id == "company_website" and any(
                        blocked in host
                        for blocked in [
                            "1688.com",
                            "b2b.baidu.com",
                            "chemnet.com",
                            "made-in-china.com",
                            "qcc.com",
                            "tianyancha.com",
                        ]
                    ):
                        continue
                    expected_site = source.get("site", "").removeprefix("site:")
                    if expected_site and expected_site not in host:
                        continue
                    key = normalized_company_name(company)
                    if not key:
                        continue
                    record = records.setdefault(
                        key,
                        {
                            "company": company,
                            "regions": set(),
                            "industries": set(),
                            "keywords": set(),
                            "channels": set(),
                            "evidence": [],
                            "sector": sector["name"],
                            "score": int(sector.get("score") or 18),
                            "query_url": query_url,
                            "company_website": "",
                        },
                    )
                    found_industries, found_keywords = competitor_signals(evidence_text)
                    record["regions"].add(region)
                    record["industries"].update(found_industries)
                    record["keywords"].update(found_keywords)
                    record["keywords"].add(keyword)
                    record["channels"].add(source["name"])
                    record["score"] = max(record["score"], int(sector.get("score") or 18))
                    if len(record["evidence"]) < 8:
                        record["evidence"].append(
                            {
                                "title": result["title"],
                                "description": result["description"][:260],
                                "url": result["url"],
                                "source": source["name"],
                            }
                        )
                    if source_id == "company_website" and host and not any(
                        blocked in host
                        for blocked in [
                            "so.com",
                            "baidu.com",
                            "1688.com",
                            "b2b.baidu.com",
                            "chemnet.com",
                            "qcc.com",
                            "tianyancha.com",
                        ]
                    ):
                        record["company_website"] = (
                            f"{urlparse(result['url']).scheme or 'https'}://"
                            f"{urlparse(result['url']).netloc}/"
                        )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source['name']}/{region}/{keyword}：{exc}")
            if progress_callback:
                progress_callback(
                    completed,
                    len(jobs),
                    len(records),
                    0,
                    f"正在检索{source['name']}：{region} {keyword}",
                )

    scan_records = sorted(
        records.values(),
        key=lambda item: (len(item["evidence"]), item["score"]),
        reverse=True,
    )[:16]
    if deep_scan and scan_records:
        if progress_callback:
            progress_callback(
                0,
                len(scan_records),
                len(records),
                0,
                "正在定位同行官网并分析应用、案例和地区词",
            )

        def inspect_website(record: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
            website = record["company_website"] or discover_company_website(record["company"])
            if not website:
                return record, "", ""
            homepage = fetch_html(website, timeout=12)
            pages = [(website, homepage)]
            for link in html_links(homepage):
                combined = f"{link['text']} {link['href']}"
                if not any(
                    word in combined
                    for word in ["产品", "应用", "案例", "客户", "解决方案", "市场", "新闻"]
                ):
                    continue
                page_url = urljoin(website, link["href"])
                if same_website(page_url, website) and page_url not in [item[0] for item in pages]:
                    pages.append((page_url, ""))
                if len(pages) >= 7:
                    break
            text_parts: list[str] = []
            for page_url, cached_page in pages:
                try:
                    page = cached_page or fetch_html(page_url, timeout=12)
                    text_parts.append(visible_html_text(page)[:30000])
                except Exception:  # noqa: BLE001
                    continue
            return record, website, " ".join(text_parts)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(inspect_website, record): record for record in scan_records
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                record = futures[future]
                try:
                    _, website, website_text = future.result()
                    if website:
                        record["company_website"] = website
                        record["channels"].add("同行企业官网")
                    if website_text:
                        industries, keywords = competitor_signals(website_text)
                        record["industries"].update(industries)
                        record["keywords"].update(keywords)
                        record["score"] += 12
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{record['company']}官网分析：{exc}")
                if progress_callback:
                    progress_callback(
                        completed,
                        len(scan_records),
                        len(records),
                        0,
                        f"正在分析同行官网：{record['company']}",
                    )

    leads: list[Lead] = []
    for record in records.values():
        industries = sorted(record["industries"])
        result_regions = sorted(record["regions"])
        keywords = sorted(record["keywords"])
        channels = sorted(record["channels"])
        evidence = record["evidence"]
        if not industries:
            industries = ["化工贸易"]
        evidence_excerpt = "；".join(
            f"{item['source']}：{item['title']}" for item in evidence[:3]
        )
        links = build_search_links(record["company"], result_regions[0] if result_regions else "", "氯化钙")
        score = min(
            100,
            record["score"]
            + min(24, len(evidence) * 4)
            + min(18, len(industries) * 4)
            + min(10, len(channels) * 3),
        )
        leads.append(
            Lead(
                company=record["company"],
                region="、".join(result_regions),
                sector=record["sector"],
                source="；".join(channels),
                score=score,
                website=evidence[0]["url"] if evidence else "",
                use_case=f"重点服务行业：{'、'.join(industries)}",
                pitch=competitor_reverse_pitch(industries, result_regions, keywords),
                match_reason=evidence_excerpt or "公开页面出现氯化钙供应信息",
                search_url=record["query_url"],
                raw_type="竞品供应商公开情报",
                qcc_url=links["qcc"],
                company_website=record["company_website"],
                direction="competitor",
                process_basis="；".join(
                    f"{item['source']}｜{item['title']}｜{item['url']}"
                    for item in evidence[:6]
                ),
                confidence="公开页面/待核验",
                competitor_industries="、".join(industries),
                competitor_regions="、".join(result_regions),
                competitor_keywords="、".join(keywords[:12]),
                competitor_channels="、".join(channels),
                evidence_count=len(evidence),
            )
        )
    return sorted(leads, key=lambda item: item.score, reverse=True), errors, len(jobs) + (
        len(scan_records) if deep_scan else 0
    )


def extract_environmental_company(value: str) -> str:
    text = re.sub(r"\s+", "", value)
    suffix_pattern = r"(?:股份有限公司|有限责任公司|集团有限公司|有限公司)"
    candidates = re.findall(rf"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{{2,48}}{suffix_pattern}", text)
    for candidate in candidates:
        candidate = re.split(r"[-—_|：:]", candidate)[-1]
        candidate = re.split(
            r"关于|拟对|受理|公示|项目名称|建设单位|评价机构|编制单位|检测单位|运营单位",
            candidate,
        )[-1]
        if len(candidate) < 6:
            continue
        if any(
            excluded in candidate
            for excluded in ["环境科技有限公司", "检测有限公司", "评价有限公司", "咨询有限公司"]
        ):
            continue
        return candidate[-48:]
    return ""


def environmental_document_sector(
    sectors: dict[str, dict[str, Any]],
    text: str,
    fallback_id: str,
) -> tuple[str, dict[str, Any]]:
    best_id = fallback_id
    best_sector = sectors[fallback_id]
    best_score = 0
    for sector_id, sector in sectors.items():
        indicators = list(dict.fromkeys([*sector.get("strict_indicators", []), *sector.get("indicators", [])]))
        score = len([indicator for indicator in indicators if indicator and indicator in text])
        if score > best_score:
            best_id = sector_id
            best_sector = sector
            best_score = score
    return best_id, best_sector


def collect_environmental_documents(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    source_ids: list[str],
    custom_keywords: list[str],
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    selected_sources = {
        source_id: ENVIRONMENTAL_DOCUMENT_SOURCES[source_id]
        for source_id in source_ids
        if source_id in ENVIRONMENTAL_DOCUMENT_SOURCES
    }
    if not selected_sources:
        return [], [], 0
    region_terms = [normalized_permit_region(region) for region in regions if permit_region_code(region)][:8]
    fallback_sector_id = next(iter(sectors))
    sector_terms = [
        keyword
        for sector_id in sectors
        for keyword in ENVIRONMENTAL_SEARCH_TERMS.get(sector_id, [])[:1]
    ]
    sector_terms.extend(custom_keywords[:2])
    sector_terms = list(dict.fromkeys(sector_terms))[:5] or ["工业企业"]
    jobs: list[tuple[str, str, str, str]] = []
    for source_id, source in selected_sources.items():
        for region in region_terms[:2]:
            keyword = sector_terms[len(jobs) % len(sector_terms)]
            jobs.append((source_id, source["terms"][0], region, keyword))
    jobs = jobs[:8]
    leads: list[Lead] = []
    errors: list[str] = []
    if progress_callback:
        progress_callback(0, len(jobs), 0, 0, "正在检索环评、验收、监测和执法公示")

    def fetch_job(job: tuple[str, str, str, str]) -> tuple[tuple[str, str, str, str], list[dict[str, str]]]:
        source_id, source_term, region, keyword = job
        query = f"{region} {keyword} 氟化物 含氟废水 {source_term} 企业"
        url = "https://www.so.com/s?" + urlencode({"q": query})
        return job, parse_360_environmental_results(fetch_html(url, timeout=20))

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_job, job): job for job in jobs}
        for completed, future in enumerate(as_completed(futures), start=1):
            source_id, source_term, region, keyword = futures[future]
            source = selected_sources[source_id]
            try:
                _, results = future.result()
                for result in results:
                    evidence_text = f"{result['title']} {result['description']}"
                    if not any(signal in evidence_text for signal in ["氟化物", "氟离子", "含氟废水"]):
                        continue
                    if region not in evidence_text:
                        continue
                    if not any(signal in evidence_text for signal in [source_term, *source["terms"]]):
                        continue
                    company = extract_environmental_company(evidence_text)
                    if not company:
                        continue
                    _, sector = environmental_document_sector(
                        sectors,
                        f"{company} {evidence_text}",
                        fallback_sector_id,
                    )
                    links = build_search_links(company, region, keyword)
                    evidence_excerpt = re.sub(r"\s+", " ", result["description"]).strip()[:220]
                    leads.append(
                        Lead(
                            company=company,
                            region=region,
                            sector=sector["name"],
                            source=f"公开环保文件检索/{source['name']}",
                            score=min(int(sector["score"]) + int(source["score"]), 100),
                            website=result["url"],
                            use_case=f"{source['name']}出现含氟废水或氟化物证据，需打开原文复核排放口和浓度。",
                            pitch=sector["pitch"],
                            match_reason=evidence_excerpt or result["title"],
                            search_url=result["url"],
                            raw_type=source["name"],
                            qcc_url=links["qcc"],
                            poi_id=source["name"],
                            direction="environmental",
                            process_basis=f"{source['name']}：{result['title']}；{evidence_excerpt}",
                            confidence=f"公开文件/{source['name']}/待复核",
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source['name']}/{region}：{exc}")
            if progress_callback:
                progress_callback(
                    completed,
                    len(jobs),
                    len(leads),
                    len(leads),
                    f"正在检索{source['name']}：{region}",
                )
    deduped: dict[str, Lead] = {}
    for lead in leads:
        existing = deduped.get(lead.company)
        if not existing or lead.score > existing.score:
            deduped[lead.company] = lead
    return list(deduped.values()), errors, len(jobs)


def environmental_evidence_excerpt(text: str, signals: list[str], limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    positions = [compact.find(signal) for signal in signals if signal and signal in compact]
    start = max(0, min(positions) - 90) if positions else 0
    return compact[start : start + limit]


def discover_company_website(company: str) -> str:
    query_url = "https://www.so.com/s?" + urlencode({"q": f'"{company}" 官网'})
    results = parse_360_environmental_results(fetch_html(query_url, timeout=18))
    company_key = re.sub(r"(股份有限公司|有限责任公司|集团有限公司|有限公司)$", "", company)
    excluded_hosts = [
        "qcc.com",
        "tianyancha.com",
        "baidu.com",
        "so.com",
        "sogou.com",
        "163.com",
        "sohu.com",
        "sina.com",
        "b2b",
        "made-in-china.com",
    ]
    for result in results:
        parsed = urlparse(result["url"])
        host = (parsed.hostname or "").lower()
        if not host or any(excluded in host for excluded in excluded_hosts):
            continue
        if company_key[:4] not in f"{result['title']} {result['description']}":
            continue
        return f"{parsed.scheme or 'https'}://{parsed.netloc}/"
    return ""


def collect_environmental_company_websites(
    amap_key: str,
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    seed_leads: list[Lead],
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    candidates: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    sector_by_name = {sector["name"]: sector for sector in sectors.values()}
    fallback_sector = next(iter(sectors.values()))
    for lead in seed_leads[:20]:
        candidates.setdefault(
            lead.company,
            {
                "company": lead.company,
                "region": lead.region,
                "address": lead.address,
                "phone": lead.phone,
                "website": normalize_website(lead.company_website),
                "raw_type": lead.raw_type,
                "sector": sector_by_name.get(lead.sector, fallback_sector),
                "company_keyword": lead.sector,
            },
        )

    region_terms = [region for region in regions if permit_region_code(region)][:4]
    amap_jobs: list[tuple[str, dict[str, Any], str]] = []
    if amap_key:
        for region in region_terms:
            for sector_id, sector in sectors.items():
                keyword = (
                    ENVIRONMENTAL_SEARCH_TERMS.get(sector_id, [])
                    or sector.get("keywords", [])
                )[0]
                amap_jobs.append((region, sector, keyword))
        amap_jobs = amap_jobs[:12]

    def fetch_amap_candidate(
        job: tuple[str, dict[str, Any], str],
    ) -> tuple[tuple[str, dict[str, Any], str], dict[str, Any]]:
        region, _, keyword = job
        return job, amap_search(amap_key, region, keyword, 1, offset=20)

    if amap_jobs:
        with ThreadPoolExecutor(max_workers=min(AMAP_WORKERS, 4)) as executor:
            futures = {executor.submit(fetch_amap_candidate, job): job for job in amap_jobs}
            for future in as_completed(futures):
                region, sector, keyword = futures[future]
                try:
                    _, data = future.result()
                    for poi in data.get("pois") or []:
                        company = str(poi.get("name") or "").strip()
                        website = normalize_website(first_text(poi.get("website")))
                        if not company or not website or not any(
                            suffix in company for suffix in ["有限公司", "股份公司", "集团"]
                        ):
                            continue
                        candidates.setdefault(
                            company,
                            {
                                "company": company,
                                "region": " ".join(
                                    part
                                    for part in [
                                        as_text(poi.get("pname")),
                                        as_text(poi.get("cityname")),
                                        as_text(poi.get("adname")),
                                    ]
                                    if part
                                ),
                                "address": as_text(poi.get("address")),
                                "phone": as_text(poi.get("tel")).replace(";", " / "),
                                "website": website,
                                "raw_type": as_text(poi.get("type")),
                                "sector": sector,
                                "company_keyword": keyword,
                            },
                        )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"官网候选/{region}/{keyword}：{exc}")

    discovery_candidates = [
        candidate for candidate in candidates.values() if not candidate["website"]
    ][:12]
    if discovery_candidates:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(discover_company_website, candidate["company"]): candidate
                for candidate in discovery_candidates
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    candidate["website"] = future.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{candidate['company']}官网定位：{exc}")

    selected_candidates = [
        candidate for candidate in candidates.values() if candidate["website"]
    ][:30]
    if progress_callback:
        progress_callback(
            0,
            len(selected_candidates),
            0,
            0,
            f"已定位 {len(selected_candidates)} 家企业官网，正在扫描环保栏目",
        )

    def inspect_candidate(candidate: dict[str, Any]) -> Lead | None:
        website = candidate["website"]
        sector = candidate["sector"]
        homepage = fetch_html(website, timeout=12)
        pages: list[tuple[str, str]] = [(website, homepage)]
        for link in html_links(homepage):
            combined = f"{link['text']} {link['href']}"
            if not any(word.lower() in combined.lower() for word in ENVIRONMENTAL_WEBSITE_NAV_WORDS):
                continue
            page_url = urljoin(website, link["href"])
            if same_website(page_url, website) and page_url not in [item[0] for item in pages]:
                pages.append((page_url, ""))
            if len(pages) >= 8:
                break

        best: tuple[int, str, str, str, str] | None = None
        detail_checks = 0
        for page_url, cached_page in pages:
            try:
                page = cached_page or fetch_html(page_url, timeout=12)
            except Exception:  # noqa: BLE001
                continue
            page_text = visible_html_text(page)
            confirmed_hits = [
                word for word in ENVIRONMENTAL_WEBSITE_CONFIRMED_WORDS if word in page_text
            ]
            water_fluoride_match = re.search(
                r"(?:废水|污水).{0,100}氟化物|氟化物.{0,100}(?:废水|污水)",
                page_text,
                re.S,
            )
            process_hits = [
                word for word in ENVIRONMENTAL_WEBSITE_PROCESS_WORDS if word in page_text
            ]
            water_hits = [
                word for word in ENVIRONMENTAL_WEBSITE_WATER_WORDS if word in page_text
            ]
            industry_indicators = list(
                dict.fromkeys(
                    [
                        *sector.get("strict_indicators", []),
                        *sector.get("indicators", []),
                        *sector.get("keywords", []),
                        "太阳能电池" if sector["name"] == "电子/半导体/光伏" else "",
                        "异质结" if sector["name"] == "电子/半导体/光伏" else "",
                        "晶硅" if sector["name"] == "电子/半导体/光伏" else "",
                    ]
                )
            )
            industry_hits = [
                word for word in industry_indicators if word and word in page_text
            ]
            level = ""
            score_bonus = 0
            signals: list[str] = []
            if confirmed_hits or water_fluoride_match:
                level = "官网明确确认"
                score_bonus = 42
                signals = confirmed_hits or ["废水与氟化物同段出现"]
            elif process_hits and water_hits:
                level = "官网工艺推断"
                score_bonus = 22
                signals = [*process_hits[:2], *water_hits[:2]]
            elif industry_hits:
                level = "官网行业推断"
                score_bonus = 10
                signals = industry_hits[:3]
            if level:
                excerpt = environmental_evidence_excerpt(page_text, signals)
                candidate_result = (
                    score_bonus,
                    level,
                    page_url,
                    "、".join(signals),
                    excerpt,
                )
                if not best or candidate_result[0] > best[0]:
                    best = candidate_result
            if detail_checks >= 10:
                continue
            for link in html_links(page):
                link_text = f"{link['text']} {link['href']}"
                if not any(
                    word in link_text
                    for word in [
                        *ENVIRONMENTAL_WEBSITE_CONFIRMED_WORDS,
                        *ENVIRONMENTAL_WEBSITE_PROCESS_WORDS,
                        "环境",
                        "环保",
                        "监测",
                        "验收",
                    ]
                ):
                    continue
                detail_url = urljoin(page_url, link["href"])
                if not same_website(detail_url, website) or detail_url in [item[0] for item in pages]:
                    continue
                if detail_url.lower().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")):
                    if any(word in link_text for word in ENVIRONMENTAL_WEBSITE_CONFIRMED_WORDS):
                        excerpt = re.sub(r"\s+", " ", link["text"]).strip()
                        best = (42, "官网明确确认", detail_url, excerpt, excerpt)
                    continue
                pages.append((detail_url, ""))
                detail_checks += 1
                if len(pages) >= 12:
                    break

        if not best:
            return None
        bonus, level, evidence_url, signals_text, excerpt = best
        links = build_search_links(
            candidate["company"],
            candidate["region"],
            candidate["company_keyword"],
        )
        return Lead(
            company=candidate["company"],
            region=candidate["region"],
            sector=sector["name"],
            source="企业官网深度核验",
            score=min(100, int(sector["score"]) + bonus),
            phone=candidate["phone"],
            address=candidate["address"],
            website=evidence_url,
            use_case=(
                "企业官网明确披露含氟废水相关信息。"
                if level == "官网明确确认"
                else (
                    "企业官网同时出现相关含氟工艺与废水处理信息，属于待核验工艺线索。"
                    if level == "官网工艺推断"
                    else "企业官网确认其属于含氟废水高相关行业，但未直接披露废水氟化物。"
                )
            ),
            pitch=sector["pitch"],
            match_reason=f"{level}：{signals_text}；{excerpt}",
            search_url=evidence_url,
            raw_type=candidate["raw_type"] or sector["name"],
            qcc_url=links["qcc"],
            company_website=website,
            poi_id=level,
            direction="environmental",
            process_basis=f"官网证据页面：{evidence_url}；证据摘录：{excerpt}",
            confidence=level,
        )

    leads: list[Lead] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(inspect_candidate, candidate): candidate
            for candidate in selected_candidates
        }
        for index, future in enumerate(as_completed(futures), start=1):
            candidate = futures[future]
            try:
                lead = future.result()
                if lead:
                    leads.append(lead)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{candidate['company']}官网扫描：{exc}")
            if progress_callback:
                progress_callback(
                    index,
                    len(selected_candidates),
                    len(leads),
                    len(leads),
                    f"正在核验企业官网：{candidate['company']}",
                )
    return leads, errors, len(amap_jobs) + len(discovery_candidates) + len(selected_candidates)


def collect_environmental_permits(
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    pages: int,
    progress_callback: Any = None,
) -> tuple[list[Lead], list[str], int]:
    selected_regions = [region for region in regions if permit_region_code(region)]
    if not selected_regions:
        return [], ["含氟废水雷达需要选择大区或填写省级地区，例如：江西、山东、江苏。"], 0
    selected_regions = selected_regions[:12]
    page_limit = max(1, min(pages, 3))
    keyword_items: list[tuple[str, str]] = []
    for sector_id in sectors:
        for keyword in ENVIRONMENTAL_SEARCH_TERMS.get(sector_id, []):
            keyword_items.append((keyword, sector_id))
    fallback_sector_id = next(iter(sectors))
    keyword_items.extend((keyword, fallback_sector_id) for keyword in custom_keywords)
    keyword_items = list(dict.fromkeys(keyword_items))[:12]
    jobs = [
        (region, keyword, sector_id, page_number)
        for region in selected_regions
        for keyword, sector_id in keyword_items
        for page_number in range(1, page_limit + 1)
    ]
    total_jobs = len(jobs)
    records: dict[str, tuple[dict[str, str], str, str]] = {}
    errors: list[str] = []
    if progress_callback:
        progress_callback(0, total_jobs, 0, 0, "正在查询全国排污许可证公开端")

    if jobs:
        first_region, first_keyword, first_sector_id, first_page_number = jobs.pop(0)
        try:
            for record in fetch_permit_records(first_region, first_keyword, first_page_number):
                records.setdefault(
                    record["permit_number"],
                    (record, first_keyword, first_sector_id),
                )
        except RuntimeError as exc:
            if "官方平台当前限制程序直连" in str(exc):
                indexed_leads = indexed_fluoride_permit_leads(selected_regions, sectors)
                warning = (
                    f"官方平台当前限制程序直连，已快速切换到 {len(indexed_leads)} 条"
                    "废水含氟的已核验官方许可索引，可打开许可详情人工复核。"
                )
                if progress_callback:
                    progress_callback(1, 1, len(indexed_leads), len(indexed_leads), warning)
                return indexed_leads, [warning], 1
            errors.append(
                f"排污许可/{first_region}/{first_keyword}/第{first_page_number}页：{exc}"
            )
        except Exception as exc:  # noqa: BLE001
            indexed_leads = indexed_fluoride_permit_leads(selected_regions, sectors)
            if indexed_leads:
                warning = (
                    f"排污许可公开端访问超时或限制直连，已切换到 {len(indexed_leads)} 条"
                    "废水含氟的已核验官方许可索引。"
                )
                if progress_callback:
                    progress_callback(1, 1, len(indexed_leads), len(indexed_leads), warning)
                return indexed_leads, [warning], 1
            errors.append(
                f"排污许可/{first_region}/{first_keyword}/第{first_page_number}页：{exc}"
            )
        if progress_callback:
            progress_callback(
                1,
                total_jobs,
                len(records),
                0,
                f"正在查询排污许可：{first_region} / {first_keyword}",
            )

    def fetch_job(
        job: tuple[str, str, str, int],
    ) -> tuple[tuple[str, str, str, int], list[dict[str, str]]]:
        region, keyword, _, page_number = job
        return job, fetch_permit_records(region, keyword, page_number)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(fetch_job, job): job for job in jobs}
        for completed, future in enumerate(as_completed(futures), start=2):
            region, keyword, sector_id, page_number = futures[future]
            try:
                _, page_records = future.result()
                for record in page_records:
                    records.setdefault(
                        record["permit_number"],
                        (record, keyword, sector_id),
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"排污许可/{region}/{keyword}/第{page_number}页：{exc}")
            if progress_callback:
                progress_callback(
                    completed,
                    total_jobs,
                    len(records),
                    0,
                    f"正在查询排污许可：{region} / {keyword}",
                )

    selected_records = list(records.values())[:40]
    if not selected_records:
        indexed_leads = indexed_fluoride_permit_leads(selected_regions, sectors)
        if indexed_leads:
            warning = (
                f"排污许可公开端未返回可解析记录，已切换到 {len(indexed_leads)} 条"
                "废水含氟的已核验官方许可索引。"
            )
            return indexed_leads, [warning, *errors[:5]], max(1, total_jobs)
    leads: list[Lead] = []

    def build_lead(item: tuple[dict[str, str], str, str]) -> Lead:
        record, keyword, fallback_id = item
        try:
            detail_page = fetch_html(record["url"], timeout=20)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"{record['company']}许可详情无法访问：{exc}") from exc
        if "错误页" in detail_page or "页面暂时无法访问" in detail_page:
            raise RuntimeError(f"{record['company']}许可详情被官方平台暂时限制访问")
        detail = extract_permit_detail(detail_page)
        water_pollutants = detail.get("water_pollutants") or ""
        if not any(signal in water_pollutants for signal in ["氟化物", "氟离子", "以F-计"]):
            raise ValueError("许可废水污染物未明确列出氟化物")
        sector_id, sector, hits = environmental_sector_for_record(
            sectors,
            detail.get("industry") or record["industry"],
            record["company"],
            fallback_id,
        )
        evidence = hits or [keyword]
        score = int(sector["score"]) + 36 + min(12, len(evidence) * 4)
        if record["management"] == "重点管理":
            score += 8
        links = build_search_links(record["company"], record["province"], keyword)
        region = detail.get("region") or "/".join(
            item for item in [record["province"], record["city"]] if item
        )
        process_basis = (
            f"官方许可废水主要污染物：{water_pollutants}；"
            f"许可行业：{detail.get('industry') or record['industry']}"
        )
        return Lead(
            company=record["company"],
            region=region,
            sector=sector["name"],
            source="全国排污许可证管理信息平台",
            score=min(score, 100),
            address=detail.get("address") or "",
            website=record["url"],
            use_case=(
                f"废水许可明确含氟；{record['management']}；"
                f"发证机关：{detail.get('issuer') or '待核验'}"
            ),
            pitch=sector["pitch"],
            match_reason=(
                f"许可证有效期：{record['validity']}；废水污染物：{water_pollutants}"
            ),
            search_url=record["url"],
            raw_type=detail.get("industry") or record["industry"],
            qcc_url=links["qcc"],
            poi_id=record["permit_number"],
            updated_at=record["issue_date"],
            direction="environmental",
            process_basis=process_basis,
            confidence="官方许可/废水含氟",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(build_lead, item): item for item in selected_records}
        for index, future in enumerate(as_completed(futures), start=1):
            try:
                leads.append(future.result())
            except ValueError:
                pass
            except Exception as exc:  # noqa: BLE001
                errors.append(f"排污许可详情解析失败：{exc}")
            if progress_callback:
                progress_callback(
                    total_jobs + index,
                    total_jobs + len(selected_records),
                    len(leads),
                    len(leads),
                    "正在读取企业许可详情和生产地址",
                )
    if not leads:
        indexed_leads = indexed_fluoride_permit_leads(selected_regions, sectors)
        if indexed_leads:
            leads.extend(indexed_leads)
            index_warning = (
                f"官方平台当前限制程序直连，已返回 {len(indexed_leads)} 条"
                "废水含氟的已核验官方许可索引，可打开许可详情人工复核。"
            )
            errors = [
                index_warning,
                *[error for error in errors if "官方平台当前限制程序直连" not in error],
            ]
    return leads, errors, total_jobs + len(selected_records)


def website_notice_kind_from_title(value: str) -> str:
    if any(word in value for word in ["中标", "成交", "候选人公示"]):
        return "award"
    if "招标" in value:
        return "tender"
    return "purchase"


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
    direction = (
        requested_direction
        if requested_direction in {"upstream", "procurement", "environmental", "competitor"}
        else "downstream"
    )
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
    amap_key = (
        ""
        if payload.get("disableAmap")
        else str(os.getenv("AMAP_KEY") or payload.get("amapKey") or "").strip()
    )
    require_amap = bool(payload.get("requireAmap"))
    exclude_suppliers = bool(payload.get("excludeSuppliers", True))
    strict_upstream = bool(payload.get("strictUpstream", True))
    used_fallback = False

    errors: list[str] = []
    if direction == "competitor":
        competitor_sources = [
            str(source)
            for source in (
                payload.get("competitorSources")
                or ["company_website", "1688", "aicaigou", "chemnet"]
            )
        ]
        leads, errors, request_count = collect_competitor_intelligence(
            regions,
            sectors,
            competitor_sources,
            custom_keywords,
            bool(payload.get("competitorDeepScan", True)),
            progress_callback,
        )
        return {
            "leads": [asdict(lead) for lead in leads],
            "errors": errors[:40],
            "meta": {
                "count": len(leads),
                "companyCount": len(leads),
                "phoneCount": len(
                    [lead for lead in leads if lead.company_website]
                ),
                "requestCount": request_count,
                "workers": 4,
                "fastMode": False,
                "direction": direction,
                "regions": regions,
                "sectors": [item["name"] for item in sectors.values()],
                "competitorSources": competitor_sources,
                "mode": "competitor",
            },
        }
    if direction == "environmental":
        environmental_sources = [
            str(source)
            for source in (
                payload.get("environmentalSources")
                or [
                    "permit",
                    "eia",
                    "acceptance",
                    "monitoring",
                    "enforcement",
                    "company_website",
                ]
            )
        ]
        leads: list[Lead] = []
        request_count = 0
        if "permit" in environmental_sources:
            permit_leads, permit_errors, permit_requests = collect_environmental_permits(
                regions,
                sectors,
                custom_keywords,
                pages,
                progress_callback,
            )
            leads.extend(permit_leads)
            errors.extend(permit_errors)
            request_count += permit_requests
        document_sources = [
            source for source in environmental_sources if source in ENVIRONMENTAL_DOCUMENT_SOURCES
        ]
        if document_sources:
            document_leads, document_errors, document_requests = collect_environmental_documents(
                regions,
                sectors,
                document_sources,
                custom_keywords,
                progress_callback,
            )
            leads.extend(document_leads)
            errors.extend(document_errors)
            request_count += document_requests
        if "company_website" in environmental_sources:
            website_leads, website_errors, website_requests = (
                collect_environmental_company_websites(
                    amap_key,
                    regions,
                    sectors,
                    custom_keywords,
                    leads,
                    progress_callback,
                )
            )
            leads.extend(website_leads)
            errors.extend(website_errors)
            request_count += website_requests
        company_deduped: dict[str, Lead] = {}
        for lead in sorted(leads, key=lambda item: item.score, reverse=True):
            existing = company_deduped.get(lead.company)
            if not existing:
                company_deduped[lead.company] = lead
                continue
            if lead.source not in existing.source:
                existing.source = f"{existing.source}；{lead.source}"
                existing.process_basis = f"{existing.process_basis}；补充证据：{lead.process_basis}"
                existing.score = min(100, existing.score + 6)
                if lead.company_website:
                    existing.company_website = lead.company_website
                if lead.confidence and lead.confidence not in existing.confidence:
                    existing.confidence = f"{existing.confidence}；{lead.confidence}"
                if lead.poi_id in {"官网明确确认", "官网工艺推断", "官网行业推断"}:
                    existing.website = lead.website
                    existing.search_url = lead.search_url
                    existing.match_reason = (
                        f"{existing.match_reason}；官网补充：{lead.match_reason}"
                    )
        leads = sorted(company_deduped.values(), key=lambda item: item.score, reverse=True)
        return {
            "leads": [asdict(lead) for lead in leads],
            "errors": errors[:40],
            "meta": {
                "count": len(leads),
                "companyCount": len(leads),
                "phoneCount": 0,
                "permitCount": len([lead for lead in leads if lead.poi_id]),
                "requestCount": request_count,
                "workers": 4,
                "fastMode": False,
                "direction": direction,
                "regions": regions,
                "sectors": [item["name"] for item in sectors.values()],
                "environmentalSources": environmental_sources,
                "mode": "environmental",
            },
        }
    if direction == "procurement":
        notice_type_ids = payload.get("noticeTypes") or ["purchase", "tender", "award"]
        date_window_id = str(payload.get("dateWindow") or "10d")
        procurement_sources = payload.get("procurementSources") or [
            "ggzy",
            "ccgp",
            "zycg",
            "shandong",
            "sichuan",
        ]
        if "public_platform" in procurement_sources:
            procurement_sources = list(
                dict.fromkeys(
                    [
                        *[source for source in procurement_sources if source != "public_platform"],
                        "ggzy",
                        "ccgp",
                        "zycg",
                        "shandong",
                        "sichuan",
                    ]
                )
            )
        leads: list[Lead] = []
        errors = []
        request_count = 0
        if "ggzy" in procurement_sources:
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
        if "ccgp" in procurement_sources:
            ccgp_leads, ccgp_errors, ccgp_requests = collect_ccgp_notices(
                regions,
                sectors,
                custom_keywords,
                notice_type_ids,
                date_window_id,
                progress_callback,
            )
            leads.extend(ccgp_leads)
            errors.extend(ccgp_errors)
            request_count += ccgp_requests
        if "zycg" in procurement_sources:
            zycg_leads, zycg_errors, zycg_requests = collect_zycg_notices(
                sectors,
                custom_keywords,
                notice_type_ids,
                date_window_id,
                progress_callback,
            )
            leads.extend(zycg_leads)
            errors.extend(zycg_errors)
            request_count += zycg_requests
        if "shandong" in procurement_sources:
            shandong_leads, shandong_errors, shandong_requests = collect_shandong_notices(
                regions,
                sectors,
                custom_keywords,
                notice_type_ids,
                date_window_id,
                progress_callback,
            )
            leads.extend(shandong_leads)
            errors.extend(shandong_errors)
            request_count += shandong_requests
        if "sichuan" in procurement_sources:
            sichuan_leads, sichuan_errors, sichuan_requests = collect_sichuan_notices(
                regions,
                sectors,
                custom_keywords,
                notice_type_ids,
                date_window_id,
                progress_callback,
            )
            leads.extend(sichuan_leads)
            errors.extend(sichuan_errors)
            request_count += sichuan_requests
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
            normalized_title = re.sub(r"[\s\-—_（）()【】\[\]：:]+", "", lead.project_title)
            normalized_company = re.sub(r"\s+", "", lead.company)
            key = (
                f"{normalized_company}|{normalized_title}"
                if normalized_title
                else f"{normalized_company}|{lead.website}"
            )
            deduped.setdefault(key, lead)
        leads = list(deduped.values())
        leads = sorted(leads, key=lambda item: item.score, reverse=True)
        if not leads:
            leads = procurement_monitor_entries(
                regions,
                sectors,
                custom_keywords,
                notice_type_ids,
                date_window_id,
            )
            if leads:
                errors.insert(
                    0,
                    "官方平台暂未返回具体公告，已生成中国政府采购网/公共资源交易平台检索入口。",
                )
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
        if direction == "upstream" and not leads and strict_upstream:
            relaxed_leads, relaxed_errors = collect_amap_leads(
                amap_key,
                regions,
                sectors,
                custom_keywords,
                pages,
                max(keyword_limit, 4),
                direction,
                exclude_suppliers,
                False,
                progress_callback,
            )
            if relaxed_leads:
                leads = relaxed_leads
                errors.extend(relaxed_errors)
                errors.append("严格工艺匹配未命中，已放宽为上游候选企业；请打开详情核验实际副产工艺。")
        if not leads:
            leads = fallback_leads(regions, sectors, custom_keywords, direction)
            used_fallback = True
            errors.append("未采集到高德结果，已生成搜索任务清单。")
    else:
        leads = fallback_leads(regions, sectors, custom_keywords, direction)
        used_fallback = True

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
            "mode": (
                "task"
                if used_fallback
                else "amap"
                if amap_key
                else "need_key"
                if require_amap
                else "task"
            ),
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


def persist_search_result(result: dict[str, Any], job_id: str = "") -> dict[str, Any]:
    if result.get("meta", {}).get("mode") in {"task", "need_key"}:
        return {"created": 0, "updated": 0, "total": 0}
    try:
        return save_leads(result.get("leads") or [])
    except Exception as exc:  # noqa: BLE001 - keep collected leads visible when cloud DB is misconfigured.
        message = (
            f"采集已完成，但保存到数据库失败：{exc}。"
            "如果这是 Render 网页版，请检查 TURSO_DATABASE_URL、TURSO_AUTH_TOKEN；"
            "未使用 Turso 时请不要配置这两个变量。"
        )
        result.setdefault("errors", []).insert(0, message)
        log_system_event(
            "error",
            "database",
            message,
            source="turso" if turso_active() else "sqlite",
            details={"jobId": job_id, "mode": result.get("meta", {}).get("mode")},
        )
        return {
            "created": 0,
            "updated": 0,
            "total": 0,
            "error": str(exc),
        }


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
        persistence = persist_search_result(result, job_id)
        result["persistence"] = persistence
        for error in result.get("errors") or []:
            level = "warning" if result.get("leads") else "error"
            log_system_event(
                level,
                "collection",
                str(error),
                source=str(result.get("meta", {}).get("mode") or payload.get("direction") or "collector"),
                details={"direction": payload.get("direction"), "jobId": job_id},
            )
        log_activity(
            "collect",
            "search_job",
            (
                f"{payload.get('direction', 'downstream')} 采集完成："
                f"{len(result.get('leads') or [])} 条，新增 {persistence['created']} 条"
            ),
            details={"jobId": job_id, "meta": result.get("meta", {})},
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
        log_system_event(
            "error",
            "collection",
            str(exc),
            source=str(payload.get("direction") or "collector"),
            details={"jobId": job_id},
        )
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


def monitor_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    direction = str(payload.get("direction") or "downstream")
    regions: list[str] = []
    for region in payload.get("regions") or []:
        regions.extend(REGION_PRESETS.get(str(region), [str(region)]))
    regions = [region for region in dict.fromkeys(regions) if region]
    sector_count = len(payload.get("sectors") or [])
    source_fields = {
        "procurement": "procurementSources",
        "environmental": "environmentalSources",
        "competitor": "competitorSources",
    }
    sources = payload.get(source_fields.get(direction, ""), []) if direction in source_fields else []
    return {
        "direction": direction if direction in DIRECTION_LABELS else "downstream",
        "directionLabel": DIRECTION_LABELS.get(direction, DIRECTION_LABELS["downstream"]),
        "directionOrder": DIRECTION_ORDER.index(direction) if direction in DIRECTION_ORDER else 99,
        "regions": regions,
        "sectorCount": sector_count,
        "sources": sources,
        "summary": (
            f"地区：{'、'.join(regions[:5]) if regions else '未指定'}"
            f"{'等' if len(regions) > 5 else ''}；主题：{sector_count} 项"
            f"{f'；来源：{len(sources)} 个' if sources else ''}"
        ),
    }


def list_monitors() -> list[dict[str, Any]]:
    with DATABASE_LOCK, database_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM monitors ORDER BY enabled DESC, created_at DESC"
        ).fetchall()
    monitors: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload"] or "{}")
        summary = monitor_payload_summary(payload)
        monitors.append(
            {
            "id": row["id"],
            "name": row["name"],
            "enabled": bool(row["enabled"]),
            "intervalHours": row["interval_hours"],
            "payload": payload,
            "direction": summary["direction"],
            "directionLabel": summary["directionLabel"],
            "directionOrder": summary["directionOrder"],
            "summary": summary["summary"],
            "regions": summary["regions"],
            "sectorCount": summary["sectorCount"],
            "sources": summary["sources"],
            "lastRun": row["last_run"],
            "nextRun": row["next_run"],
            "lastResult": row["last_result"],
            "lastError": row["last_error"],
            }
        )
    return monitors


def save_monitor(name: str, payload: dict[str, Any], interval_hours: int) -> int:
    timestamp = now_iso()
    interval_hours = max(1, min(interval_hours, 24 * 30))
    next_run = (datetime.now().astimezone() + timedelta(hours=interval_hours)).isoformat(
        timespec="seconds"
    )
    with DATABASE_LOCK, database_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO monitors (
                name, enabled, interval_hours, payload, next_run, created_at, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?)
            """,
            (
                name.strip() or "未命名监控",
                interval_hours,
                json.dumps(payload, ensure_ascii=False),
                next_run,
                timestamp,
                timestamp,
            ),
        )
    MONITOR_WAKE_EVENT.set()
    return int(cursor.lastrowid)


def _run_monitor(monitor_id: int) -> dict[str, Any]:
    with DATABASE_LOCK, database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM monitors WHERE id = ?",
            (monitor_id,),
        ).fetchone()
    if not row:
        raise ValueError("监控任务不存在")
    payload = json.loads(row["payload"] or "{}")
    started = now_iso()
    try:
        result = collect_leads(payload)
        persistence = (
            save_leads(result.get("leads") or [], monitor_id)
            if result.get("meta", {}).get("mode") not in {"task", "need_key"}
            else {"created": 0, "updated": 0, "total": 0}
        )
        next_run = (
            datetime.now().astimezone() + timedelta(hours=row["interval_hours"])
        ).isoformat(timespec="seconds")
        result_text = (
            f"发现 {len(result.get('leads') or [])} 条，"
            f"新增 {persistence['created']} 条，更新 {persistence['updated']} 条"
        )
        for error in result.get("errors") or []:
            log_system_event(
                "warning",
                "monitor",
                str(error),
                source=row["name"],
                details={"monitorId": monitor_id},
            )
        with DATABASE_LOCK, database_connection() as connection:
            connection.execute(
                """
                UPDATE monitors SET last_run = ?, next_run = ?, last_result = ?,
                    last_error = '', updated_at = ?
                WHERE id = ?
                """,
                (started, next_run, result_text, now_iso(), monitor_id),
            )
            if persistence["created"]:
                connection.execute(
                    """
                    INSERT INTO notifications (
                        type, title, message, monitor_id, created_at
                    ) VALUES ('monitor', ?, ?, ?, ?)
                    """,
                    (
                        f"监控任务发现 {persistence['created']} 条新线索",
                        f"{row['name']}：{result_text}",
                        monitor_id,
                        now_iso(),
                    ),
                )
        return {"result": result_text, "persistence": persistence}
    except Exception as exc:
        log_system_event(
            "error",
            "monitor",
            str(exc),
            source=row["name"],
            details={"monitorId": monitor_id},
        )
        next_run = (
            datetime.now().astimezone() + timedelta(hours=row["interval_hours"])
        ).isoformat(timespec="seconds")
        with DATABASE_LOCK, database_connection() as connection:
            connection.execute(
                """
                UPDATE monitors SET last_run = ?, next_run = ?, last_error = ?,
                    updated_at = ? WHERE id = ?
                """,
                (started, next_run, str(exc), now_iso(), monitor_id),
            )
        raise


def run_monitor(monitor_id: int) -> dict[str, Any]:
    with MONITOR_RUNNING_LOCK:
        if monitor_id in MONITOR_RUNNING:
            raise RuntimeError("该监控任务正在运行，请勿重复启动")
        MONITOR_RUNNING.add(monitor_id)
    try:
        return _run_monitor(monitor_id)
    finally:
        with MONITOR_RUNNING_LOCK:
            MONITOR_RUNNING.discard(monitor_id)


def start_monitor_background(monitor_id: int) -> None:
    with MONITOR_RUNNING_LOCK:
        if monitor_id in MONITOR_RUNNING:
            raise RuntimeError("该监控任务正在运行，请勿重复启动")
        MONITOR_RUNNING.add(monitor_id)

    def worker() -> None:
        try:
            _run_monitor(monitor_id)
        except Exception as exc:  # noqa: BLE001 - keep background failures visible in system center.
            log_system_event(
                "error",
                "monitor",
                str(exc),
                source="手动运行监控",
                details={"monitorId": monitor_id},
            )
        finally:
            with MONITOR_RUNNING_LOCK:
                MONITOR_RUNNING.discard(monitor_id)

    threading.Thread(target=worker, daemon=True).start()


def create_follow_up_notifications() -> None:
    today = date.today().isoformat()
    with DATABASE_LOCK, database_connection() as connection:
        due_rows = connection.execute(
            """
            SELECT id, company, next_follow_up FROM leads
            WHERE next_follow_up != '' AND substr(next_follow_up, 1, 10) <= ?
              AND sales_status NOT IN ('won', 'lost')
            """,
            (today,),
        ).fetchall()
        for row in due_rows:
            exists = connection.execute(
                """
                SELECT 1 FROM notifications
                WHERE type = 'follow_up' AND lead_id = ?
                  AND substr(created_at, 1, 10) = ?
                """,
                (row["id"], today),
            ).fetchone()
            if exists:
                continue
            connection.execute(
                """
                INSERT INTO notifications (
                    type, title, message, lead_id, created_at
                ) VALUES ('follow_up', '销售跟进到期', ?, ?, ?)
                """,
                (
                    f"{row['company']} 的计划跟进时间为 {row['next_follow_up']}",
                    row["id"],
                    now_iso(),
                ),
            )


def monitor_scheduler() -> None:
    while True:
        try:
            ensure_daily_backup()
            create_follow_up_notifications()
            current = now_iso()
            with DATABASE_LOCK, database_connection() as connection:
                due_ids = [
                    row["id"]
                    for row in connection.execute(
                        """
                        SELECT id FROM monitors
                        WHERE enabled = 1 AND next_run != '' AND next_run <= ?
                        """,
                        (current,),
                    ).fetchall()
                ]
            for monitor_id in due_ids:
                try:
                    run_monitor(monitor_id)
                except Exception:
                    pass
        except Exception as exc:
            log_system_event(
                "error",
                "scheduler",
                str(exc),
                source="后台调度器",
            )
        finally:
            MONITOR_WAKE_EVENT.wait(60)
            MONITOR_WAKE_EVENT.clear()


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
        "sales_status",
        "sales_status_label",
        "owner",
        "notes",
        "next_follow_up",
        "first_seen",
        "last_seen",
        "score_details",
        "opportunity_role",
        "liquid_concentration",
        "monthly_volume",
        "impurity_profile",
        "logistics_radius",
        "storage_condition",
        "commercial_value",
        "competitor_industries",
        "competitor_regions",
        "competitor_keywords",
        "competitor_channels",
        "evidence_count",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


class AppHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
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
            json_response(self, {"status": "ok", "version": APP_VERSION})
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
                self.redirect("/login?v=pnvs-login-1")
            return
        if path == "/api/config":
            json_response(
                self,
                {
                    "sectors": SECTOR_LIBRARY,
                    "downstreamSectors": SECTOR_LIBRARY,
                    "upstreamSectors": UPSTREAM_SECTOR_LIBRARY,
                    "competitorSectors": COMPETITOR_SECTOR_LIBRARY,
                    "competitorSources": COMPETITOR_SOURCE_LIBRARY,
                    "environmentalSectors": FLUORIDE_SECTOR_LIBRARY,
                    "procurementSectors": PROCUREMENT_SECTOR_LIBRARY,
                    "procurementNoticeTypes": PROCUREMENT_NOTICE_TYPES,
                    "procurementDateWindows": {
                        key: value[0] for key, value in PROCUREMENT_DATE_WINDOWS.items()
                    },
                    "regionPresets": REGION_PRESETS,
                    "hasEnvAmapKey": bool(os.getenv("AMAP_KEY")),
                    "tursoConfigured": turso_active(),
                    "tursoEnvConfigured": turso_configured(),
                    "tursoError": TURSO_RUNTIME_ERROR,
                },
            )
            return
        if path == "/api/leads":
            query = {
                key: values[0]
                for key, values in parse_qs(urlparse(self.path).query).items()
                if values
            }
            json_response(self, {"leads": list_saved_leads(query)})
            return
        if path == "/api/leads/detail":
            params = {
                key: values[0]
                for key, values in parse_qs(urlparse(self.path).query).items()
                if values
            }
            lead_id_raw = str(params.get("id") or "")
            lead_id = int(lead_id_raw) if lead_id_raw.isdigit() else 0
            lead = get_saved_lead(lead_id)
            if not lead:
                json_response(self, {"error": "线索不存在"}, 404)
                return
            json_response(self, {"lead": lead})
            return
        if path == "/api/dashboard":
            create_follow_up_notifications()
            json_response(self, dashboard_summary())
            return
        if path == "/api/monitors":
            json_response(self, {"monitors": list_monitors()})
            return
        if path == "/api/notifications":
            with DATABASE_LOCK, database_connection() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM notifications
                    ORDER BY is_read ASC, created_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            json_response(
                self,
                {
                    "notifications": [
                        {
                            "id": row["id"],
                            "type": row["type"],
                            "title": row["title"],
                            "message": row["message"],
                            "leadId": row["lead_id"],
                            "monitorId": row["monitor_id"],
                            "isRead": bool(row["is_read"]),
                            "createdAt": row["created_at"],
                        }
                        for row in rows
                    ]
                },
            )
            return
        if path == "/api/system":
            json_response(self, system_overview())
            return
        if path == "/api/backup":
            backup_path = create_database_backup()
            data = backup_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-sqlite3")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{backup_path.name}"',
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/search/status":
            params = {
                key: values[0]
                for key, values in parse_qs(urlparse(self.path).query).items()
                if values
            }
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
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            json_response(self, {"error": "Content-Length 无效"}, 400)
            return
        if length > MAX_REQUEST_BODY:
            json_response(self, {"error": "请求内容过大"}, 413)
            return
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
                json_response(self, {"error": "服务器尚未配置阿里云短信认证服务"}, 503)
                return
            code = f"{secrets.randbelow(1_000_000):06d}" if SMS_DEV_MODE and not sms_configured() else ""
            try:
                if sms_configured():
                    send_aliyun_verify_code(phone)
            except Exception as exc:  # noqa: BLE001
                json_response(self, {"error": f"验证码发送失败：{exc}"}, 502)
                return
            now = time.time()
            with SMS_LOCK:
                SMS_CODES[phone] = {
                    "sentAt": now,
                }
                if code:
                    SMS_CODES[phone].update(
                        {
                            "digest": code_digest(phone, code),
                            "expiresAt": now + SMS_CODE_TTL,
                            "attempts": 0,
                        }
                    )
                SMS_SEND_ATTEMPTS.setdefault(self.client_id(), []).append(now)
            response: dict[str, Any] = {"ok": True, "expiresIn": SMS_CODE_TTL}
            if code:
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
            verified = False
            if sms_configured():
                try:
                    verified = check_aliyun_verify_code(phone, code)
                except Exception as exc:  # noqa: BLE001
                    json_response(self, {"error": f"验证码核验失败：{exc}"}, 502)
                    return
            elif SMS_DEV_MODE:
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
                        verified = True
            if not verified:
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
            result = collect_leads(payload)
            result["persistence"] = persist_search_result(result)
            for error in result.get("errors") or []:
                log_system_event(
                    "warning" if result.get("leads") else "error",
                    "collection",
                    str(error),
                    source=str(
                        result.get("meta", {}).get("mode")
                        or payload.get("direction")
                        or "collector"
                    ),
                    details={"direction": payload.get("direction"), "api": "sync"},
                )
            log_activity(
                "collect",
                "search",
                (
                    f"{payload.get('direction', 'downstream')} 同步采集完成："
                    f"{len(result.get('leads') or [])} 条"
                ),
                details={"meta": result.get("meta", {})},
            )
            json_response(self, result)
            return

        if path == "/api/leads/create":
            try:
                result = create_manual_lead(payload)
            except ValueError as exc:
                json_response(self, {"error": str(exc)}, 400)
                return
            create_follow_up_notifications()
            json_response(self, result, 201)
            return

        if path == "/api/leads/update":
            lead_id = int(payload.get("id") or 0)
            status = str(payload.get("salesStatus") or "new")
            if status not in SALES_STATUSES:
                json_response(self, {"error": "销售状态无效"}, 400)
                return
            owner = str(payload.get("owner") or "").strip()[:80]
            notes = str(payload.get("notes") or "").strip()[:5000]
            next_follow_up = str(payload.get("nextFollowUp") or "").strip()[:40]
            opportunity_role = str(payload.get("opportunityRole") or "").strip()[:20]
            liquid_concentration = str(payload.get("liquidConcentration") or "").strip()[:80]
            monthly_volume = str(payload.get("monthlyVolume") or "").strip()[:80]
            impurity_profile = str(payload.get("impurityProfile") or "").strip()[:500]
            logistics_radius = str(payload.get("logisticsRadius") or "").strip()[:80]
            storage_condition = str(payload.get("storageCondition") or "").strip()[:300]
            commercial_value = str(payload.get("commercialValue") or "").strip()[:80]
            with DATABASE_LOCK, database_connection() as connection:
                existing = connection.execute(
                    "SELECT payload FROM leads WHERE id = ?",
                    (lead_id,),
                ).fetchone()
                if not existing:
                    json_response(self, {"error": "线索不存在"}, 404)
                    return
                score_input = json.loads(existing["payload"] or "{}")
                score_input.update(
                    {
                        "opportunity_role": opportunity_role,
                        "liquid_concentration": liquid_concentration,
                        "monthly_volume": monthly_volume,
                        "impurity_profile": impurity_profile,
                        "logistics_radius": logistics_radius,
                        "storage_condition": storage_condition,
                        "commercial_value": commercial_value,
                    }
                )
                score, score_details = calculate_lead_score(score_input)
                cursor = connection.execute(
                    """
                    UPDATE leads SET sales_status = ?, owner = ?, notes = ?,
                        next_follow_up = ?, is_new = 0, updated_at = ?,
                        opportunity_role = ?, liquid_concentration = ?,
                        monthly_volume = ?, impurity_profile = ?,
                        logistics_radius = ?, storage_condition = ?,
                        commercial_value = ?, score = ?, score_details = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        owner,
                        notes,
                        next_follow_up,
                        now_iso(),
                        opportunity_role,
                        liquid_concentration,
                        monthly_volume,
                        impurity_profile,
                        logistics_radius,
                        storage_condition,
                        commercial_value,
                        score,
                        json.dumps(score_details, ensure_ascii=False),
                        lead_id,
                    ),
                )
            create_follow_up_notifications()
            log_activity(
                "update",
                "lead",
                f"更新线索销售档案，状态：{SALES_STATUSES[status]}",
                lead_id,
                {"owner": owner, "nextFollowUp": next_follow_up},
            )
            json_response(self, {"ok": True})
            return

        if path == "/api/leads/bulk-update":
            ids = [
                int(value)
                for value in payload.get("ids") or []
                if str(value).isdigit()
            ][:1000]
            if not ids:
                json_response(self, {"error": "请选择至少一条线索"}, 400)
                return
            updates: list[str] = []
            values: list[Any] = []
            if "salesStatus" in payload:
                status = str(payload.get("salesStatus") or "")
                if status not in SALES_STATUSES:
                    json_response(self, {"error": "销售状态无效"}, 400)
                    return
                updates.append("sales_status = ?")
                values.append(status)
            if "owner" in payload:
                updates.append("owner = ?")
                values.append(str(payload.get("owner") or "").strip()[:80])
            if "nextFollowUp" in payload:
                updates.append("next_follow_up = ?")
                values.append(str(payload.get("nextFollowUp") or "").strip()[:40])
            if not updates:
                json_response(self, {"error": "没有可更新的字段"}, 400)
                return
            updates.extend(["is_new = 0", "updated_at = ?"])
            values.append(now_iso())
            placeholders = ",".join("?" for _ in ids)
            with DATABASE_LOCK, database_connection() as connection:
                cursor = connection.execute(
                    f"UPDATE leads SET {', '.join(updates)} WHERE id IN ({placeholders})",
                    (*values, *ids),
                )
            log_activity(
                "bulk_update",
                "lead",
                f"批量更新 {cursor.rowcount} 条线索",
                details={"ids": ids, "fields": list(payload.keys())},
            )
            create_follow_up_notifications()
            json_response(self, {"ok": True, "updated": cursor.rowcount})
            return

        if path == "/api/monitors/save":
            monitor_payload = payload.get("payload")
            if not isinstance(monitor_payload, dict):
                json_response(self, {"error": "监控条件无效"}, 400)
                return
            monitor_id = save_monitor(
                str(payload.get("name") or ""),
                monitor_payload,
                int(payload.get("intervalHours") or 24),
            )
            json_response(self, {"ok": True, "id": monitor_id})
            return

        if path == "/api/monitors/toggle":
            monitor_id = int(payload.get("id") or 0)
            enabled = 1 if payload.get("enabled") else 0
            with DATABASE_LOCK, database_connection() as connection:
                cursor = connection.execute(
                    "UPDATE monitors SET enabled = ?, updated_at = ? WHERE id = ?",
                    (enabled, now_iso(), monitor_id),
                )
            if not cursor.rowcount:
                json_response(self, {"error": "监控任务不存在"}, 404)
                return
            MONITOR_WAKE_EVENT.set()
            json_response(self, {"ok": True})
            return

        if path == "/api/monitors/run":
            monitor_id = int(payload.get("id") or 0)
            with DATABASE_LOCK, database_connection() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM monitors WHERE id = ?",
                    (monitor_id,),
                ).fetchone()
            if not exists:
                json_response(self, {"error": "监控任务不存在"}, 404)
                return
            try:
                start_monitor_background(monitor_id)
            except RuntimeError as exc:
                json_response(self, {"error": str(exc)}, 409)
                return
            json_response(self, {"ok": True}, 202)
            return

        if path == "/api/monitors/delete":
            monitor_id = int(payload.get("id") or 0)
            with DATABASE_LOCK, database_connection() as connection:
                connection.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
            json_response(self, {"ok": True})
            return

        if path == "/api/notifications/read":
            notification_id = int(payload.get("id") or 0)
            with DATABASE_LOCK, database_connection() as connection:
                if notification_id:
                    connection.execute(
                        "UPDATE notifications SET is_read = 1 WHERE id = ?",
                        (notification_id,),
                    )
                else:
                    connection.execute("UPDATE notifications SET is_read = 1")
            json_response(self, {"ok": True})
            return

        if path == "/api/system/resolve":
            event_id = int(payload.get("id") or 0)
            with DATABASE_LOCK, database_connection() as connection:
                if event_id:
                    connection.execute(
                        "UPDATE system_events SET resolved = 1 WHERE id = ?",
                        (event_id,),
                    )
                else:
                    connection.execute("UPDATE system_events SET resolved = 1")
            json_response(self, {"ok": True})
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


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        message = traceback.format_exc()
        log_system_event(
            "error",
            "server",
            message[-2000:],
            source="HTTP 服务",
            details={"client": str(client_address)},
        )
        super().handle_error(request, client_address)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    initialize_database()
    threading.Thread(target=monitor_scheduler, daemon=True).start()
    if not APP_PASSWORD:
        print("WARNING: APP_PASSWORD is not set. Login will remain disabled.")
    if not LOGIN_PHONES:
        print("WARNING: LOGIN_PHONES is not set. Only SMS_DEV_MODE can allow local login.")
    if not sms_configured() and not SMS_DEV_MODE:
        print("WARNING: Aliyun PNVS is not configured. Verification codes cannot be sent.")
    if not os.getenv("AMAP_KEY"):
        print("WARNING: AMAP_KEY is not set. Company collection will remain disabled.")
    server = AppServer((args.host, args.port), AppHandler)
    print(f"Calcium chloride buyer finder running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
