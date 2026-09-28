# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""自检：places.json / routes.json 的一致性与合理性。

用法：uv run tools/validate.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# Windows 控制台默认 GBK，print 不支持的字符（如 ✔）会 UnicodeEncodeError 把自检整个搞崩。
# 改成「无法编码就替换」，保证自检不会因为输出字符而失败。
try:
    sys.stdout.reconfigure(errors="replace")      # Python 3.7+
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

BEIJING = (115.2, 117.7, 39.4, 40.6)  # lng_min, lng_max, lat_min, lat_max（含门头沟/通州）
REQUIRED = ["id", "ming_name", "volume", "modern_name", "address", "lng", "lat",
            "coord_precision", "confidence", "cluster", "visit_minutes", "tags",
            "excerpt", "vernacular"]

errors: list[str] = []
warns: list[str] = []


def check(cond: bool, msg: str, hard: bool = True) -> None:
    if cond:
        return
    (errors if hard else warns).append(msg)


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp, dl = p2 - p1, math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def main() -> int:
    places_doc = json.loads((DATA / "places.json").read_text(encoding="utf-8"))
    routes = json.loads((DATA / "routes.json").read_text(encoding="utf-8"))
    places = places_doc["places"]
    by_name = {p["ming_name"]: p for p in places}

    check(len(places) == 30, f"地点数应为 30，实际 {len(places)}")
    check(len(by_name) == len(places), "ming_name 存在重复")

    for p in places:
        for f in REQUIRED:
            check(f in p and p[f] not in ("", None), f"{p.get('ming_name')}: 缺字段 {f}")
        check(BEIJING[0] < p["lng"] < BEIJING[1] and BEIJING[2] < p["lat"] < BEIJING[3],
              f"{p['ming_name']}: 坐标 {p['lng']},{p['lat']} 超出北京范围")
        check(p["confidence"] in {"high", "medium", "low"}, f"{p['ming_name']}: confidence 非法")
        check(p["coord_precision"] in {"point", "area"}, f"{p['ming_name']}: precision 非法")
        check(len(p["excerpt"]) > 60, f"{p['ming_name']}: 原文摘录过短", hard=False)
        check(len(p["vernacular"]) > 30, f"{p['ming_name']}: 今译过短")
        # 繁体/简体摘录一致性（长度相近）
        check(abs(len(p["excerpt"]) - len(p["excerpt_simp"])) < len(p["excerpt"]) * 0.2,
              f"{p['ming_name']}: 繁简摘录长度差异过大", hard=False)
        for poem in p["poems"]:
            check(bool(poem.get("title")), f"{p['ming_name']}: 诗缺标题")

    # 行程引用完整性
    def check_stops(stops, where):
        for s in stops:
            name = s["ming_name"] if isinstance(s, dict) else s
            check(name in by_name, f"{where}: 引用了不存在的地名「{name}」")

    ref_ids = set()
    for circle in routes["walk_circles"]:
        check_stops(circle["stops"], f"步行圈 {circle['id']}")
        ref_ids.update(circle["stops"])
    for it in routes["itineraries"]:
        for day in it["plan"]:
            if day.get("stops"):
                check_stops(day["stops"], f"{it['id']} day{day['day']}")
                ref_ids.update(s["ming_name"] for s in day["stops"])
        if it.get("addons"):
            check_stops(it["addons"], f"{it['id']} addons")
            ref_ids.update(s["ming_name"] for s in it["addons"])

    # 行程标注的直连距离与实际直线距离对比
    def straight(names):
        pts = [(by_name[n]["lng"], by_name[n]["lat"]) for n in names if n in by_name]
        return sum(haversine(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) / 1000

    for circle in routes["walk_circles"]:
        real = straight(circle["stops"])
        print(f"  步行圈 {circle['name']:<8} 标注 {circle['est_km']:>5} km ｜ 直线 {real:5.2f} km")
        check(abs(real - circle["est_km"]) <= max(1.0, circle["est_km"] * 0.35),
              f"步行圈 {circle['name']}: 标注 {circle['est_km']}km，实际直线合计 {real:.2f}km", hard=False)

    for it in routes["itineraries"]:
        tot_label, tot_real = it["est_km"], 0.0
        for day in it["plan"]:
            names = day.get("stops") and [s["ming_name"] for s in day["stops"]]
            if not names:
                if day.get("reuse"):
                    src = day["reuse"].split(".")
                    src_it = [x for x in routes["itineraries"] if x["id"] == src[0]][0]
                    src_day = [d for d in src_it["plan"] if "day%d" % d["day"] == src[1]][0]
                    names = [s["ming_name"] for s in src_day["stops"]]
                    tot_real += straight(names)
                continue
            real = straight(names)
            tot_real += straight(names)
            print(f"  {it['id']} day{day['day']:<2} 标注 {day['est_km']:>5} km ｜ 直线 {real:5.2f} km")
        print(f"  {it['id']} 合计     标注 {tot_label:>5} km ｜ 直线 {tot_real:5.2f} km")

    unused = [p["ming_name"] for p in places if p["ming_name"] not in ref_ids]
    print(f"地点 {len(places)}；被行程引用 {len(ref_ids)}；未被引用 {len(unused)}")
    if unused:
        print("  未纳入任何行程：" + "、".join(unused))
    print(f"诗 {sum(p['poem_count'] for p in places)} 首（页面收录 {sum(len(p['poems']) for p in places)} 首全文）")
    print(f"沿革 {sum(1 for p in places if p['history_note'])} 条；"
          f"坐标核验 {sum(1 for p in places if p['coord_source'] == 'verified-by-ak')} 条")

    for w in warns:
        print("WARN  " + w)
    for e in errors:
        print("ERROR " + e)
    print("\n" + ("全部通过 [OK]" if not errors else f"发现 {len(errors)} 处问题 [FAIL]"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
