# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""用 places.json 的真实坐标同步 routes.json 的距离/时长字段。

手工维护 est_km 一定会随坐标更新而漂移（30 条坐标核验后就发生过）。
这个脚本把 routes.json 里的派生字段全部改成从数据算出来的：

  * walk_circles[].est_km   = 相邻站点直线距离之和
  * walk_circles[].hours    = 步行时间 + 各站建议停留
  * itineraries[].plan[].est_km / walk_minutes = 同上（reuse 的天数从来源天复制）
  * itineraries[].stops_total / est_km = 各天汇总

用法：
  uv run tools/sync_routes.py            # 写入
  uv run tools/sync_routes.py --check    # 只报告差异，不写入（CI/自检用）
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SPEED_KMH = {"walking": 4.2, "riding": 12, "driving": 22, "transit": 18}
DETOUR = 1.3          # 路网绕行系数，与 app/planner.js 保持一致
TRANSIT_WAIT_MIN = 8  # 公交每段另加等车/换乘时间


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp, dl = p2 - p1, math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告差异")
    ap.add_argument("--round-hours", type=float, default=0.5, help="hours 取整粒度")
    args = ap.parse_args()

    places = {p["ming_name"]: p for p in json.loads((DATA / "places.json").read_text(encoding="utf-8"))["places"]}
    routes_path = DATA / "routes.json"
    routes = json.loads(routes_path.read_text(encoding="utf-8"))

    changes: list[str] = []

    def measure(names: list[str]) -> tuple[float, int]:
        """→ (直线合计 km, 建议停留合计 分钟)；缺站点会抛错而不是静默算错。"""
        missing = [n for n in names if n not in places]
        if missing:
            raise SystemExit(f"routes.json 引用了不存在的地点: {missing}")
        pts = [(places[n]["lng"], places[n]["lat"]) for n in names]
        km = sum(haversine(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) / 1000
        visit = sum(places[n]["visit_minutes"] for n in names)
        return km, visit

    def travel_min(km: float, mode: str = "walking") -> int:
        v = SPEED_KMH.get(mode, 4.2)
        return round(km * DETOUR / v * 60) + (TRANSIT_WAIT_MIN if mode == "transit" else 0)

    def set_if(obj: dict, key: str, value, label: str) -> None:
        if obj.get(key) != value:
            changes.append(f"{label}.{key}: {obj.get(key)} → {value}")
            obj[key] = value

    # ---- 半日步行圈 ----
    for c in routes["walk_circles"]:
        km, visit = measure(c["stops"])
        km = round(km, 1)
        set_if(c, "est_km", km, f"walk_circles[{c['id']}]")
        hours = round((travel_min(km) + visit) / 60 / args.round_hours) * args.round_hours
        set_if(c, "hours", hours, f"walk_circles[{c['id']}]")

    # ---- 行程：先算非 reuse 的天，再复制给 reuse 的天 ----
    computed: dict[str, dict] = {}
    for it in routes["itineraries"]:
        for day in it["plan"]:
            if day.get("reuse"):
                continue
            names = [s["ming_name"] for s in day["stops"]]
            km, visit = measure(names)
            km = round(km, 1)
            mode = day.get("mode", "walking")
            trav = travel_min(km, mode)
            set_if(day, "est_km", km, f"{it['id']}.day{day['day']}")
            if day.pop("walk_minutes", None) is not None:
                changes.append(f"{it['id']}.day{day['day']}.walk_minutes → travel_minutes（按出行方式计）")
            set_if(day, "travel_minutes", trav, f"{it['id']}.day{day['day']}")
            computed[f"{it['id']}.day{day['day']}"] = {"est_km": km, "travel_minutes": trav}

    for it in routes["itineraries"]:
        total_km = 0.0
        total_stops = 0
        for day in it["plan"]:
            if day.get("reuse"):
                src = computed.get(day["reuse"])
                if src:
                    set_if(day, "est_km", src["est_km"], f"{it['id']}.day{day['day']}(reuse)")
                    if day.pop("walk_minutes", None) is not None:
                        changes.append(f"{it['id']}.day{day['day']}(reuse).walk_minutes → travel_minutes")
                    set_if(day, "travel_minutes", src["travel_minutes"], f"{it['id']}.day{day['day']}(reuse)")
                names = reuse_names(it, day["reuse"], routes)
            else:
                names = [s["ming_name"] for s in day["stops"]]
            total_km += day["est_km"]
            total_stops += len(names)
        if it.get("addons"):
            total_stops += len(it["addons"])
        set_if(it, "est_km", round(total_km, 1), it["id"])
        set_if(it, "stops_total", total_stops, it["id"])

    if not changes:
        print("routes.json 已是最新，无需改动")
        return 0

    print(f"需同步 {len(changes)} 处：")
    for ch in changes:
        print("  " + ch)
    if args.check:
        print("\n--check 模式，未写入")
        return 1

    routes_path.write_text(json.dumps(routes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已写入 {routes_path.relative_to(ROOT)}")
    return 0


def reuse_names(it: dict, ref: str, routes: dict) -> list[str]:
    """解析 reuse（例 'd2.day1'）取出该天的站点名。"""
    parts = str(ref).split(".")
    src_it = next((x for x in routes["itineraries"] if x["id"] == parts[0]), None)
    if not src_it:
        return []
    day_no = int(str(parts[1]).replace("day", ""))
    src_day = next((d for d in src_it["plan"] if d["day"] == day_no), None)
    if not src_day:
        return []
    if src_day.get("reuse"):
        return reuse_names(src_it, src_day["reuse"], routes)
    return [s["ming_name"] for s in src_day.get("stops", [])]


if __name__ == "__main__":
    sys.exit(main())
