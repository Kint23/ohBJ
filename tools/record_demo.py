# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright"]
# ///
"""用 Playwright 的**上下文录制**真录 60 秒正片（不是截图拼接）。

    uv run tools/record_demo.py                 # 录制约 60s，headful（地图 GL 需要）
    uv run tools/record_demo.py --list-only     # 只打印时间轴，不录制

产出：
  video/_raw.webm        原始录像（1920x1080）
  video/正片字幕.srt      **唯一一套字幕**，时间已扣除片头加载段
  video/_timeline.json    各步骤时间戳与片头修剪量
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
VIDEO_EXTRA_TAIL = 2.0
END_AT = 60.0

# 时间轴：秒 -> 字幕（指"片头修剪后"的成片时间）
SUBS = [
    (0.0, "明代人写下的北京，今天还在吗？"),
    (5.0, "127 处风物，落点 30 处古迹"),
    (10.0, "按卷次、类型、步行圈逐层收窄"),
    (15.0, "点开一处：明清地名 → 今日地名"),
    (19.0, "原书记载，逐字可对"),
    (24.0, "关联诗篇 · 白话今译"),
    (28.0, "清人《京城古迹考》的实地核访"),
    (32.0, "只说一句：半天想逛寺庙"),
    (39.0, "智能排线给出 3 个取舍不同的方案"),
    (43.0, "一键调用百度路线规划"),
    (50.0, "真实里程与耗时：短腿走路，远腿坐车"),
    (55.5, "也可以直接问「明代北京城 AI 导游」"),
]


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
        self.offset = offset
        self.log: list[str] = []

    @property
    def now(self) -> float:
        return time.monotonic() - self.t0 - self.offset

    def until(self, t: float) -> None:
        dt = t - self.now
        if dt > 0:
            self.page.wait_for_timeout(int(dt * 1000))

    def note(self, text: str) -> None:
        self.log.append(f"{self.now:6.1f}s  {text}")
        print(f"{self.now:6.1f}s  {text}", flush=True)

    def js(self, code: str):
        try:
            return self.page.evaluate(code)
        except Exception as exc:  # noqa: BLE001
            self.note(f"! js 失败: {exc}")
            return None

    def _hover(self, bb) -> None:
        if bb:
            self.page.mouse.move(bb["x"] + bb["width"] / 2,
                                 bb["y"] + bb["height"] / 2, steps=18)
            self.page.wait_for_timeout(200)

    def box_click(self, selector: str) -> bool:
        try:
            loc = self.page.locator(selector).first
            self._hover(loc.bounding_box(timeout=5000))
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
                    self._hover(el.bounding_box())
                    el.click(timeout=5000)
                    return True
            return False
        except Exception as exc:  # noqa: BLE001
            self.note(f"! 点击文本 {container}/{text} 失败: {exc}")
            return False

    def click_nth(self, container: str, idx: int) -> bool:
        try:
            el = self.page.locator(f"{container} > *").nth(idx)
            self._hover(el.bounding_box())
            el.click(timeout=5000)
            return True
        except Exception as exc:  # noqa: BLE001
            self.note(f"! 点击第 {idx} 个 {container} 失败: {exc}")
            return False

    def scroll(self, selector: str, top: float, block: str = "start") -> None:
        self.js(f"() => {{ const e = document.querySelector('{selector}');"
                f" if (e) e.scrollTo({{ top: {top}, behavior: 'smooth' }}); }}")


def write_srt(subs: list[tuple[float, str]], out: Path) -> None:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, (t, text) in enumerate(subs):
        end = subs[i + 1][0] - 0.3 if i + 1 < len(subs) else END_AT
        lines.append(f"{i + 1}\n{ts(t)} --> {ts(max(end, t + 1.0))}\n{text}\n")
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
        print(f"落版 {END_AT:.0f}s")
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
                          "--hide-scrollbars",
                          "--disable-features=CalculateNativeWinOcclusion"]
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
            print(f"地图就绪 {offset:.1f}s（成片剪掉）", flush=True)
            page.wait_for_timeout(1200)

            # 落版层（最后才显示）
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

            # 0-5s 全景轻推
            r.until(0.6)
            for dx, dy in ((-50, 0), (-50, -26), (-40, -20)):
                r.js(f"() => {{ const m = DJJWL.getMap(); if (m) m.panBy({dx}, {dy}); }}")
                r.until(r.now + 0.75)
            r.note("pan 全景")

            # 5-9s 拉远看全城 30 个标记
            for z in (12.4, 11.9, 11.5, 11.2, 11.0):
                r.js(f"() => {{ const m = DJJWL.getMap(); if (m) m.setZoom({z}); }}")
                r.until(r.now + 0.75)
            r.note("zoom out 全城")

            # 9-15s 移到城南 + 两重筛选
            r.js("() => { const m = DJJWL.getMap();"
                 " if (m) m.centerAndZoom(new BMapGL.Point(116.401, 39.888), 14.5); }")
            r.until(10.2)
            r.click_text("#volumeFilter", "卷三")
            r.until(12.6)
            r.click_nth("#tagFilter", 0)
            r.until(14.6)
            r.note("筛选")

            # 15-19s 点开憫忠寺
            r.until(15.2)
            r.click_text("#placeList", "憫忠寺")
            r.until(17.2)
            r.js("() => { const m = DJJWL.getMap(); if (m) m.setZoom(15.4); }")
            r.note("打开憫忠寺")

            # 19-31s 详情逐段
            r.until(19.4)
            r.scroll("#detail", 430)
            r.until(23.6)
            r.scroll("#detail", 1150)
            r.until(27.6)
            r.scroll("#detail", 2050)
            r.until(30.8)
            r.note("阅读详情")

            # 31-38s 智能排线
            try:
                page.select_option("#p3Dur", "180")
                page.select_option("#p3Mode", "walking")
            except Exception as exc:  # noqa: BLE001
                r.note(f"! 排线参数失败: {exc}")
            r.until(32.4)
            r.scroll("#p3Panel", 0)
            r.until(33.2)
            r.box_click("#p3Btn")
            r.note("生成 3 方案")

            # 38-42s 展示 3 个方案
            r.until(38.6)
            r.js("() => { const e = document.querySelector('#p3Out');"
                 " if (e) e.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }")
            r.until(41.4)
            r.note("展示方案")

            # 42-49s 应用方案 + 混合模式 + 生成真实路线
            r.until(42.4)
            try:
                btns = page.locator("#p3Out button")
                n = btns.count()
                r.note(f"#p3Out 按钮数 = {n}")
                if n:
                    r._hover(btns.first.bounding_box())
                    btns.first.click()
            except Exception as exc:  # noqa: BLE001
                r.note(f"! 应用方案失败: {exc}")
            r.until(44.2)
            try:
                page.select_option("#routeMode", "mixed")
            except Exception:  # noqa: BLE001
                pass
            r.until(45.6)
            r.scroll("#routePanel", 0)
            r.until(46.4)
            r.box_click("#planBtn")
            r.note("生成真实路线")

            # 49-55s 路线结果
            r.until(52.4)
            r.js("() => { const e = document.querySelector('#routeResult');"
                 " if (e) e.scrollIntoView({ behavior: 'smooth', block: 'end' }); }")
            r.until(54.6)
            r.note("路线结果")

            # 55-58s AI 导游
            r.box_click("#gxToggle")
            r.until(56.2)
            try:
                page.fill("#gxInput", "半天想逛寺庙，怎么走？")
                page.press("#gxInput", "Enter")
            except Exception as exc:  # noqa: BLE001
                r.note(f"! 导游提问失败: {exc}")
            r.until(58.2)
            r.note("AI 导游")

            # 58-60s 落版
            r.js("() => { const d = document.querySelector('#__endcard');"
                 " if (d) d.style.display = 'flex'; }")
            r.until(END_AT + VIDEO_EXTRA_TAIL)
            r.note("endcard")

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
    print(f"srt: {srt}  ({len(SUBS)} 条)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
