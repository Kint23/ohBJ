# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""用百度地图 WebAPI 核验 30 个地点的坐标（BD-09）。

AK 从环境变量读取，不要把 AK 写进代码或提交到仓库：
    $env:BAIDU_MAP_AK = "<你的服务端 AK>"      # PowerShell，仅当前会话
    uv run tools/verify_coords.py --apply

流程：地点检索 v3（query=今日地名, region=北京）→ 失败则地理编码 v3（address）
输出：data/coords_verified.json（含与种子坐标的距离），--apply 时同时回填 places.json
注意：地点检索配额小（未认证 100/日，个人认证 2000/日），脚本自动缓存、限速、去重。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "cache" / "webapi.json"

PLACE_URL = "https://api.map.baidu.com/place/v2/search"
GEO_URL = "https://api.map.baidu.com/geocoding/v3/"


def haversine(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def get_json(url: str, params: dict, cache: dict, key: str) -> dict:
    if key in cache:
        return cache[key]
    qs = urllib.parse.urlencode({**params, "output": "json"})
    req = urllib.request.Request(
        f"{url}?{qs}",
        headers={"User-Agent": "djjwl-ancient-map/1.0", "Referer": "https://lbsyun.baidu.com/"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    cache[key] = data
    time.sleep(0.35)  # 限速，保护配额
    return data


def search_place(ak: str, name: str, city: str, cache: dict) -> tuple[float, float, str] | None:
    d = get_json(PLACE_URL, {
        "query": name, "region": city, "city_limit": "true", "ak": ak, "scope": "2", "page_size": "5",
    }, cache, f"place|{name}|{city}")
    if d.get("status") == 0:
        for r in d.get("results", []):
            loc = r.get("location") or {}
            if loc.get("lat") and loc.get("lng"):
                return float(loc["lng"]), float(loc["lat"]), r.get("name", "")
    elif d.get("status") not in (0, 2):
        print(f"    [place status={d.get('status')} {d.get('message','')}]")
    return None


def geocode(ak: str, address: str, city: str, cache: dict) -> tuple[float, float, str] | None:
    d = get_json(GEO_URL, {"address": address, "city": city, "ak": ak}, cache, f"geo|{address}|{city}")
    if d.get("status") == 0:
        loc = d["result"]["location"]
        return float(loc["lng"]), float(loc["lat"]), d["result"].get("level", "")
    print(f"    [geo status={d.get('status')} {d.get('message','')}]")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ak", default=os.environ.get("BAIDU_MAP_AK", ""))
    ap.add_argument("--apply", action="store_true", help="回填 places.json")
    ap.add_argument("--city", default="北京")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（省配额）")
    args = ap.parse_args()

    if not args.ak:
        print("缺少 AK：请设置环境变量 BAIDU_MAP_AK，或用 --ak 传入。")
        print("（AK 属敏感凭据，请勿写入文件或提交仓库）")
        return 2

    doc = json.loads((DATA / "places.json").read_text(encoding="utf-8"))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    results, ok, bad = [], 0, 0
    targets = doc["places"][: args.limit] if args.limit else doc["places"]
    for i, p in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {p['ming_name']} → {p['modern_name']}")
        hit = search_place(args.ak, p["modern_name"], args.city, cache) or geocode(
            args.ak, p["address"], args.city, cache
        )
        if not hit:
            print("    未命中")
            bad += 1
            continue
        lng, lat, matched = hit
        dist = haversine(p["lng"], p["lat"], lng, lat)
        flag = "OK " if dist < 800 else ("远!" if dist < 3000 else "冲突")
        print(f"    {flag} {matched} → {lat:.6f},{lng:.6f}  距种子 {dist:.0f} m")
        results.append({"id": p["id"], "ming_name": p["ming_name"], "modern_name": p["modern_name"],
                        "matched": matched, "lng": lng, "lat": lat, "distance_m": round(dist)})
        ok += 1

    (DATA / "coords_verified.json").write_text(
        json.dumps({"places": results}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"\n核验完成：命中 {ok}，未命中 {bad} → data/coords_verified.json")

    if args.apply:
        idx = {r["id"]: r for r in results if r["distance_m"] < 3000}
        for p in doc["places"]:
            if p["id"] in idx:
                p["lng"], p["lat"] = idx[p["id"]]["lng"], idx[p["id"]]["lat"]
                p["coord_source"] = "verified-by-ak"
        (DATA / "places.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已回填 places.json（{len(idx)} 条，距离 >3000m 的视为冲突不采用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
