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
import re
import secrets
import ssl
import time
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
    if isinstance(input_regions, str):
        parts = re.split(r"[,，\s]+", input_regions)
    else:
        parts = input_regions

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


def selected_sectors(ids: list[str] | None) -> dict[str, dict[str, Any]]:
    if not ids:
        ids = ["snow", "desiccant", "water", "concrete", "trader"]
    return {sector_id: SECTOR_LIBRARY[sector_id] for sector_id in ids if sector_id in SECTOR_LIBRARY}


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
            "citylimit": "false",
            "offset": str(offset),
            "page": str(page),
            "extensions": "all",
            "output": "json",
        }
    )
    url = f"https://restapi.amap.com/v3/place/text?{query}"
    req = Request(url, headers={"User-Agent": "BuyerLeadFinder/1.0"})
    with urlopen(req, timeout=timeout, context=DEFAULT_SSL_CONTEXT) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def fallback_leads(regions: list[str], sectors: dict[str, dict[str, Any]], custom_keywords: list[str]) -> list[Lead]:
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
                        use_case=sector["uses"],
                        pitch=sector["pitch"],
                        match_reason=f"建议批量搜索：{region} + {keyword}",
                        search_url=links["baidu"],
                        website=links["amap"],
                        qcc_url=links["qcc"],
                    )
                )
    return leads


def collect_amap_leads(
    amap_key: str,
    regions: list[str],
    sectors: dict[str, dict[str, Any]],
    custom_keywords: list[str],
    pages: int,
) -> tuple[list[Lead], list[str]]:
    leads: list[Lead] = []
    errors: list[str] = []
    seen: set[str] = set()
    pages = max(1, min(pages, 10))

    for region in regions:
        for sector in sectors.values():
            keywords = list(sector["keywords"])
            for custom in custom_keywords:
                custom = custom.strip()
                if custom and custom not in keywords:
                    keywords.append(custom)
            for keyword in keywords:
                for page in range(1, pages + 1):
                    try:
                        data = amap_search(amap_key, region, keyword, page)
                    except Exception as exc:  # noqa: BLE001 - show concise collection errors to user.
                        errors.append(f"{region}/{keyword}/第{page}页：{exc}")
                        break

                    if data.get("status") != "1":
                        info = data.get("info") or "高德接口返回失败"
                        errors.append(f"{region}/{keyword}：{info}")
                        break

                    pois = data.get("pois") or []
                    if not pois:
                        break

                    for poi in pois:
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
                        dedupe_key = f"{name}|{address}"
                        if dedupe_key in seen:
                            continue
                        seen.add(dedupe_key)
                        score, reason = lead_score(name, raw_type, int(sector["score"]), bool(phone))
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
                                use_case=sector["uses"],
                                pitch=sector["pitch"],
                                match_reason=f"{keyword}；{reason}",
                                search_url=links["amap"],
                                raw_type=raw_type,
                                qcc_url=links["qcc"],
                            )
                        )
                    time.sleep(0.08)
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


def collect_leads(payload: dict[str, Any]) -> dict[str, Any]:
    regions = normalize_regions(payload.get("regions"))
    sectors = selected_sectors(payload.get("sectors"))
    custom_keywords = [
        item.strip()
        for item in re.split(r"[,，\n]+", str(payload.get("customKeywords") or ""))
        if item.strip()
    ]
    pages = int(payload.get("pages") or 1)
    amap_key = str(os.getenv("AMAP_KEY") or payload.get("amapKey") or "").strip()
    require_amap = bool(payload.get("requireAmap"))

    errors: list[str] = []
    if require_amap and not amap_key:
        leads = []
        errors.append("要显示具体公司和电话，必须填写高德 Web 服务 API Key；否则只能生成开发任务清单。")
    elif amap_key:
        leads, errors = collect_amap_leads(amap_key, regions, sectors, custom_keywords, pages)
        if not leads:
            leads = fallback_leads(regions, sectors, custom_keywords)
            errors.append("未采集到高德结果，已生成搜索任务清单。")
    else:
        leads = fallback_leads(regions, sectors, custom_keywords)

    if payload.get("includeProcurement", True) and not require_amap:
        leads.extend(procurement_links(regions))

    leads = sorted(leads, key=lambda item: item.score, reverse=True)
    return {
        "leads": [asdict(lead) for lead in leads],
        "errors": errors[:40],
        "meta": {
            "count": len(leads),
            "companyCount": len([lead for lead in leads if lead.source == "高德 POI"]),
            "phoneCount": len([lead for lead in leads if lead.phone]),
            "regions": regions,
            "sectors": [item["name"] for item in sectors.values()],
            "mode": "amap" if amap_key else "need_key" if require_amap else "task",
        },
    }


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
            json_response(self, {"status": "ok"})
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
                    "regionPresets": REGION_PRESETS,
                    "hasEnvAmapKey": bool(os.getenv("AMAP_KEY")),
                },
            )
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
