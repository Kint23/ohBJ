# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright"]
# ///
"""用 Playwright 的**上下文录制**真录 3 分钟正片（不是截图拼接）。

    uv run tools/record_demo.py                 # 录制约 180s，headful（地图 GL 需要）
    uv run tools/record_demo.py --list-only     # 只打印脚本时间轴，不录制

产出：
  video/_raw.webm        原始录像（16:9 / 1920x1080）
  video/正片字幕.srt      与录像时间轴对齐的字幕（时间已扣除片头加载段）
  video/_timeline.json   各步骤时间戳与片头修剪量，供 post 脚本使用
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

W, H = 1920, 1080
VIDEO_EXTRA_TAIL = 3.0  # 结尾多留几秒再停

# 脚本时间轴：秒 -> 字幕（时间指"片头修剪后"的成片时间）
SUBS = [
    (0.0, "明代人写下的北京，今天还在吗？"),
    (7.0, "《帝京景物略》以八卷录下 127 处风物、1346 首诗"),
    (17.0, "其中 30 处，有明确的古今传承"),
    (27.0, "按卷次、类型、步行圈，逐层收窄"),
    (46.0, "点开一处：明清地名 → 今日地名"),
    (58.0, "原书记载，并附坐标来源与可信度"),
    (76.0, "关联诗篇与白话文今译"),
    (93.0, "清人《京城古迹考》对同一地点的实地核访"),
    (105.0, "也可按诗题、作者直接检索"),
    (115.0, "只说一句：半天想逛寺庙"),
    (125.0, "智能排线给出 3 个取舍不同的方案"),
    (141.0, "一键调用百度路线规划，算出真实里程"),
    (159.0, "短腿走路，远腿坐车"),
    (171.0, "或者，直接问「明代北京城 AI 导游」"),
]
END_AT = 178.0


def find_chromium() -> str | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not base.exists():
        return None
    for d in sorted((x for x in base.glob("chromium-*") if x.is_dir()), reverse=True):
        for exe in ("chrome-win64/chrome.exe", "chrome-win/chrome.exe"):
            p = d / exe
            if p.exists():
                return str(p)
    return None


class Rec:
    """带时间轴的记录器：所有动作按绝对时间点执行，保证与字幕对齐。"""

    def __init__(self, page, t0: float, offset: float):
        self.page = page
        self.t0 = t0
        self.offset = offset  # 片头加载耗时，字幕时间要减掉
        self.log: list[str] = []

    @property
    def now(self) -> float:
        return time.monotonic() - self.t0 - self.offset

    def until(self, t: float) -> None:
        dt = t - self.now
        if dt > 0:
            self.page.wait_for_timeout(int(dt * 1000))

    def note(self, text: str) -> None:
        self.log.append(f"{self.now:7.1f}s  {text}")
        print(f"{self.now:7.1f}s  {text}", flush=True)

    def js(self, code: str):
        try:
            return self.page.evaluate(code)
        except Exception as exc:  # noqa: BLE001
            self.note(f"! js 失败: {exc}")
            return None

    def box_click(self, selector: str) -> bool:
        """先把鼠标移过去再点，看起来更像人在操作。"""
        try:
            loc = self.page.locator(selector).first
            bb = loc.bounding_box(timeout=5000)
            if not bb:
                return False
            x, y = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
            self.page.mouse.move(x, y, steps=22)
            self.page.wait_for_timeout(260)
            loc.click(timeout=5000)
            return True
        except Exception as exc:  # noqa: BLE001
            self.note(f"! 点击 {selector} 失败: {exc}")
            return False

    def click_text(self, container: str, text: str) -> bool:
        try:
            loc = self.page.locator(f"{container} > *")
            for i in range(loc.count()):
                el = loc.nth(i)
                try:
                    t = (el.inner_text(timeout=700) or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                if text in t:
                    bb = el.bounding_box()
                    if bb:
                        self.page.mouse.move(bb["x"] + bb["width"] / 2,
                                             bb["y"] + bb["height"] / 2, steps=22)
                        self.page.wait_for_timeout(240)
                    el.click(timeout=5000)
                    return True
            return False
        except Exception as exc:  # noqa: BLE001
            self.note(f"! 点击文本 {container}/{text} 失败: {exc}")
            return False

    def click_nth(self, container: str, idx: int) -> bool:
        try:
            el = self.page.locator(f"{container} > *").nth(idx)
            bb = el.bounding_box()
            if bb:
                self.page.mouse.move(bb["x"] + bb["width"] / 2,
                                     bb["y"] + bb["height"] / 2, steps=22)
                self.page.wait_for_timeout(240)
            el.click(timeout=5000)
            return True
        except Exception as exc:  # noqa: BLE001
            self.note(f"! 点击第 {idx} 个 {container} 失败: {exc}")
            return False

    def scroll_detail(self, to: float) -> None:
        self.js(f"() => {{ const d = document.querySelector('#detail');"
                f" if (d) d.scrollTo({{ top: {to}, behavior: 'smooth' }}); }}")

    def map_zoom(self, z: float) -> None:
        self.js(f"() => {{ const m = DJJWL.getMap(); if (m) m.setZoom({z}); }}")

    def map_pan(self, dx: int, dy: int) -> None:
        self.js(f"() => {{ const m = DJJWL.getMap(); if (m) m.panBy({dx}, {dy}); }}")


def write_srt(subs: list[tuple[float, str]], out: Path) -> None:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, (t, text) in enumerate(subs):
        end = subs[i + 1][0] - 0.4 if i + 1 < len(subs) else END_AT
        lines.append(f"{i + 1}\n{ts(t)} --> {ts(max(end, t + 1.2))}\n{text}\n")
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080/")
    ap.add_argument("--outdir", default="video")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    if args.list_only:
        for t, s in SUBS:
            print(f"{int(t) // 60:d}:{int(t) % 60:02d}  {s}")
        print(f"结尾落版 {int(END_AT)}s")
        return 0

    from playwright.sync_api import sync_playwright

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    exe = find_chromium()
    print(f"chromium: {exe or '(默认)'}", flush=True)

    with tempfile.TemporaryDirectory() as td:
        recdir = Path(td) / "rec"
        recdir.mkdir()
        with sync_playwright() as pw:
            kw = {"headless": False}
            if exe:
                kw["executable_path"] = exe
            kw["args"] = [f"--window-size={W},{H}", "--force-device-scale-factor=1",
                          "--hide-scrollbars", "--disable-features=CalculateNativeWinOcclusion"]
            browser = pw.chromium.launch(**kw)
            ctx = browser.new_context(
                viewport={"width": W, "height": H}, device_scale_factor=1, locale="zh-CN",
                record_video_dir=str(recdir),
                record_video_size={"width": W, "height": H},
            )
            t_created = time.monotonic()
            page = ctx.new_page()
            page.goto(args.base, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_function(
                    "() => window.DJJWL && ['ready','failed'].includes(DJJWL.mapState)",
                    timeout=120000)
            except Exception as exc:  # noqa: BLE001
                print(f"! 等地图就绪超时: {exc}", flush=True)
            offset = time.monotonic() - t_created
            print(f"地图就绪耗时 {offset:.1f}s（成片会剪掉）", flush=True)
            page.wait_for_timeout(1500)

            # 让标题在录到的第一帧就出现：一次性挂出落版层
            page.evaluate("""() => {
              const d = document.createElement('div');
              d.id = '__endcard';
              d.style.cssText = 'position:fixed;inset:0;display:none;z-index:99999;' +
                'align-items:center;justify-content:center;background:rgba(246,241,230,0.94);' +
                'font-family:KaiTi,楷体,serif;color:#2b241c;font-size:104px;letter-spacing:14px';
              d.textContent = '帝京寻踪 · 明代北京古今地图';
              document.body.appendChild(d);
            }""")

            r = Rec(page, t_created, offset)
            r.note("start")

            # ---- 0-6s 全景，轻微平移
            r.until(1.0)
            for dx, dy in ((-60, 0), (-60, -30), (-60, -30)):
                r.map_pan(dx, dy)
                r.until(r.now + 0.9)
            r.note("pan 全景")

            # ---- 6-16s 拉远看全城
            for z in (12.5, 12, 11.6, 11.3, 11.1, 11.0):
                r.map_zoom(z)
                r.until(r.now + 1.0)
            r.note("zoom out 全城")

            # ---- 16-26s 推到城南
            r.js("() => { const m = DJJWL.getMap(); if (m) m.centerAndZoom(new BMapGL.Point(116.401, 39.888), 14); }")
            r.until(20.5)
            r.map_zoom(15)
            r.until(24.5)
            r.map_zoom(15.6)
            r.note("推到城南")

            # ---- 26-45s 三重筛选
            r.until(27.5)
            r.click_text("#volumeFilter", "卷三")
            r.until(31.0)
            r.click_nth("#tagFilter", 0)
            r.until(34.5)
            r.click_nth("#clusterFilter", 2)
            r.until(38.5)
            r.js("() => document.querySelector('#map').dispatchEvent(new Event('mouseup'))")
            r.click_nth("#volumeFilter", 0)
            r.until(42.0)
            r.click_nth("#tagFilter", 0)
            r.until(45.0)
            r.note("三重筛选")

            # ---- 45-58s 点开憫忠寺
            r.click_text("#placeList", "憫忠寺")
            r.until(50.0)
            r.js("() => { const m = DJJWL.getMap(); if (m) m.setZoom(15.5); }")
            r.until(56.0)
            r.note("打开憫忠寺")

            # ---- 58-105s 详情面板逐段滚动
            r.scroll_detail(420)
            r.until(74.0)
            r.scroll_detail(1100)
            r.until(91.0)
            r.scroll_detail(2000)
            r.until(103.5)
            r.note("阅读详情")

            # ---- 105-115s 搜索
            r.until(105.5)
            r.box_click("#q")
            r.page.fill("#q", "石鼓")
            r.until(109.0)
            r.page.fill("#q", "于谦")
            r.until(112.5)
            r.page.fill("#q", "")
            r.until(114.5)
            r.note("搜索")

            # ---- 115-140s 智能排线
            try:
                r.page.select_option("#p3Dur", "180")
            except Exception:  # noqa: BLE001
                pass
            r.until(116.5)
            try:
                r.page.select_option("#p3Mode", "walking")
            except Exception:  # noqa: BLE001
                pass
            r.until(118.0)
            r.box_click("#p3Btn")
            r.until(124.0)
            r.js("() => { const el = document.querySelector('#p3Out');"
                 " if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }")
            r.until(130.0)
            r.js("() => { const el = document.querySelector('#p3Panel');"
                 " if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }")
            r.until(137.0)
            r.note("智能排线 3 方案")

            # ---- 140-168s 应用方案 + 真实路线
            try:
                btns = r.page.locator("#p3Out button")
                n = btns.count()
                r.note(f"#p3Out 按钮数 = {n}")
                if n:
                    bb = btns.first.bounding_box()
                    if bb:
                        r.page.mouse.move(bb["x"] + bb["width"] / 2,
                                          bb["y"] + bb["height"] / 2, steps=22)
                        r.page.wait_for_timeout(300)
                    btns.first.click()
            except Exception as exc:  # noqa: BLE001
                r.note(f"! 应用方案失败: {exc}")
            r.until(145.0)
            try:
                r.page.select_option("#routeMode", "mixed")
            except Exception:  # noqa: BLE001
                pass
            r.until(148.0)
            r.js("() => { const el = document.querySelector('#routePanel');"
                 " if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }")
            r.until(150.0)
            r.box_click("#planBtn")
            r.until(166.0)
            r.js("() => { const el = document.querySelector('#routeResult');"
                 " if (el) el.scrollIntoView({ behavior: 'smooth', block: 'end' }); }")
            r.until(169.0)
            r.note("生成真实路线")

            # ---- 170-178s AI 导游
            r.box_click("#gxToggle")
            r.until(171.5)
            try:
                r.page.fill("#gxInput", "半天想逛寺庙，怎么走？")
                r.page.press("#gxInput", "Enter")
            except Exception as exc:  # noqa: BLE001
                r.note(f"! 导游提问失败: {exc}")
            r.until(177.0)
            r.note("AI 导游")

            # ---- 落版
            r.js("() => { const d = document.querySelector('#__endcard');"
                 " if (d) d.style.display = 'flex'; }")
            r.until(END_AT + VIDEO_EXTRA_TAIL)
            r.note("endcard")

            video = page.video
            ctx.close()
            browser.close()

        webms = sorted(recdir.glob("*.webm"))
        if not webms:
            raise SystemExit("没有拿到录像文件")
        raw = outdir / "_raw.webm"
        shutil.move(str(webms[0]), str(raw))
        print(f"raw: {raw}  {raw.stat().st_size / 1024 / 1024:.1f} MB", flush=True)

    srt = outdir / "正片字幕.srt"
    write_srt(SUBS, srt)
    (outdir / "_timeline.json").write_text(json.dumps(
        {"trim_start": round(offset, 3), "end_at": END_AT,
         "subs": [{"t": t, "text": s} for t, s in SUBS]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"srt: {srt}", flush=True)
    print("\n".join(r.log[-6:]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
