# /// script
# requires-python = ">=3.10"
# dependencies = ["zhconv"]
# ///
"""合并 抽取结果 + 策展映射 + 今译  →  data/places.json（前端唯一数据源）

WGS-84 → BD-09 坐标转换在此完成，前端拿到的即为百度坐标系。

用法：
  uv run tools/build_places.py
  uv run tools/build_places.py --excerpt 900 --poems 8
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CUR = DATA / "curated"

A = 6378245.0
EE = 0.00669342162296594323
X_PI = math.pi * 3000.0 / 180.0


def _out_of_china(lng: float, lat: float) -> bool:
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _t_lat(lng: float, lat: float) -> float:
    r = (
        -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat
        + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    )
    r += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    r += (160.0 * math.sin(lat / 12.0 * math.pi) + 320.0 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return r


def _t_lng(lng: float, lat: float) -> float:
    r = (
        300.0 + lng + 2.0 * lat + 0.1 * lng * lng
        + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    )
    r += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    r += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return r


def wgs84_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    """WGS-84 → BD-09（先转 GCJ-02 火星坐标，再转百度坐标）。"""
    if _out_of_china(lng, lat):
        return round(lng, 6), round(lat, 6)
    d_lat, d_lng = _t_lat(lng - 105.0, lat - 35.0), _t_lng(lng - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    magic = 1 - EE * math.sin(rad) ** 2
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((A * (1 - EE)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (A / sqrt_magic * math.cos(rad) * math.pi)
    gcj_lng, gcj_lat = lng + d_lng, lat + d_lat
    z = math.sqrt(gcj_lng * gcj_lng + gcj_lat * gcj_lat) + 0.00002 * math.sin(gcj_lat * X_PI)
    theta = math.atan2(gcj_lat, gcj_lng) + 0.000003 * math.cos(gcj_lng * X_PI)
    return round(z * math.cos(theta) + 0.0065, 6), round(z * math.sin(theta) + 0.006, 6)


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excerpt", type=int, default=1100, help="正文摘录上限（字）")
    ap.add_argument("--poems", type=int, default=8, help="每地收录全文诗数上限")
    ap.add_argument("--history", type=int, default=600, help="沿革文本上限（字）")
    args = ap.parse_args()

    entries = {e["id"]: e for e in json.loads((DATA / "entries.json").read_text(encoding="utf-8"))}
    guben = json.loads((DATA / "guben.json").read_text(encoding="utf-8"))
    mapping = json.loads((CUR / "mapping.json").read_text(encoding="utf-8"))["places"]
    vern = {v["entry"]: v["vernacular"] for v in json.loads((CUR / "vernacular.json").read_text(encoding="utf-8"))["places"]}
    verified = {}
    vp = DATA / "coords_verified.json"
    if vp.exists():
        verified = {v["id"]: v for v in json.loads(vp.read_text(encoding="utf-8"))["places"]}

    def find_entry(name_simp: str) -> dict | None:
        for e in entries.values():
            if e["name_simp"] == name_simp:
                return e
        for e in entries.values():
            k = e["name_simp"]
            if len(name_simp) >= 2 and (name_simp in k or k in name_simp) and abs(len(k) - len(name_simp)) <= 2:
                return e
        return None

    def find_guben(name_simp: str) -> dict | None:
        for g in guben:
            if g["name_simp"] == name_simp:
                return g
        for g in guben:
            k = g["name_simp"]
            if len(name_simp) >= 2 and (name_simp in k or k in name_simp) and abs(len(k) - len(name_simp)) <= 2:
                return g
        return None

    out: list[dict] = []
    missing: list[str] = []
    for m in mapping:
        name_simp = maybe_simp(m["entry"])
        e = find_entry(name_simp)
        if not e:
            missing.append(m["entry"])
            continue
        lng, lat = wgs84_to_bd09(*m["wgs84"])
        coord_source = "seed-approx"
        if e["id"] in verified:
            v = verified[e["id"]]
            if v.get("lng") and v.get("lat") and v.get("distance_m", 9999) < 3000:
                lng, lat, coord_source = v["lng"], v["lat"], "verified-by-ak"
        g = find_guben(name_simp)
        poems_all = e["poems"]
        out.append({
            "id": e["id"],
            "ming_name": e["name"],
            "ming_name_simp": e["name_simp"],
            "volume": e["volume"],
            "volume_name": e["volume_name"],
            "scope": e["scope"],
            "modern_name": m["modern_name"],
            "address": m["address"],
            "lng": lng,
            "lat": lat,
            "wgs84_seed": m["wgs84"],
            "coord_source": coord_source,
            "coord_precision": m["precision"],
            "confidence": m["confidence"],
            "cluster": m["cluster"],
            "visit_minutes": m["visit_minutes"],
            "tags": m["tags"],
            "excerpt": squash(e["text"])[: args.excerpt],
            "excerpt_simp": squash(e["text_simp"])[: args.excerpt],
            "text_len": e["text_len"],
            "poem_count": len(poems_all),
            "poems": [
                {"dynasty": p["dynasty"], "origin": p["origin"], "author": p["author"],
                 "title": p["title"], "text": squash(p["text"])[:600]}
                for p in poems_all[: args.poems]
            ],
            "poem_titles": [p["title"] for p in poems_all],
            "vernacular": vern.get(m["entry"], ""),
            "history_note": squash(g["text"])[: args.history] if g else "",
            "history_source": f"《京城古迹考》·{g['zone']}" if g else "",
            "mentions": e["mentions_simp"][:12],
        })

    volumes = []
    for v in [f"卷{c}" for c in "一二三四五六七八"]:
        sub = [e for e in entries.values() if e["volume"] == v]
        if sub:
            volumes.append({"id": v, "name": sub[0]["volume_name"], "count": len(sub),
                            "scope": sub[0]["scope"]})

    tz = timezone(timedelta(hours=8))
    doc = {
        "meta": {
            "title": "帝京寻踪 · 明代北京古今地图",
            "generated": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S%z"),
            "place_count": len(out),
            "source_entries": len(entries),
            "source_poems": sum(len(e["poems"]) for e in entries.values()),
            "crs": "BD-09",
            "sources": [
                "《帝京景物略》明·刘侗、于奕正（崇祯八年，1635）",
                "《京城古迹考》清·励宗万",
                "《日下舊聞考》清·于敏中等",
            ],
            "notice": "古籍原文为公有领域文本；今译为 AI 辅助译文，仅供参考。坐标以 BD-09 存储，标注 seed-approx 的坐标为人工估计，请以地图为准。",
        },
        "volumes": volumes,
        "clusters": sorted({p["cluster"] for p in out}),
        "tags": sorted({t for p in out for t in p["tags"]}),
        "places": out,
    }
    (DATA / "places.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"places.json: {len(out)} 地 / {sum(p['poem_count'] for p in out)} 诗 / "
          f"{sum(1 for p in out if p['history_note'])} 条沿革 / "
          f"{sum(1 for p in out if p['coord_source']=='verified-by-ak')} 坐标已核验")
    if missing:
        print("未匹配:", missing)
    return 0


def maybe_simp(s: str) -> str:
    try:
        from zhconv import convert  # type: ignore[import-not-found]
        return convert(s, "zh-cn")
    except ImportError:
        return s


if __name__ == "__main__":
    raise SystemExit(main())
