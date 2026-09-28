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
    (10.0, "按卷次、类型逐层收窄"),
    (16.0, "点开一处：明清地名 → 今日地名"),
    (18.6, "白话今译 · AI 辅助译文"),
    (22.6, "原书记载 · 《帝京景物略》"),
    (26.0, "关联诗篇，附作者与朝代"),
    (29.6, "清人《京城古迹考》的实地核访"),
    (33.0, "只说一句：半天想逛寺庙"),
    (38.5, "智能排线给出 3 个取舍不同的方案"),
    (42.5, "一键调用百度路线规划"),
    (51.5, "真实里程与耗时：短腿走路，远腿坐车"),
    (55.0, "也可以直接问「明代北京城 AI 导游」"),
]

# 目标地点：要有白话今译 + 原书记载 + 很多关联诗篇 + 清人实地核访（四段齐全）
# 憫忠寺只有 1 首诗且无清人核访，所以改用盧溝橋（卷三，16 首，有核访）
TARGET = "盧溝橋"


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
            self.note(f"! {container} 里找不到「{text}」")
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

    def list_count(self):
        return self.js("() => document.querySelectorAll('#placeList li').length")

    def section_offsets(self, sel: str = "#detail") -> dict:
        """各 .d-sec 段落相对于滚动容器的绝对偏移（用真实几何算，不猜数字）。"""
        return self.js(f"""() => {{
          const d = document.querySelector('{sel}');
          if (!d) return {{}};
          const dt = d.getBoundingClientRect().top;
          const out = {{}};
          d.querySelectorAll('.d-sec').forEach(s => {{
            const h = s.querySelector('h3');
            const key = (h ? h.textContent : '').trim().slice(0, 4);
            out[key] = Math.round(s.getBoundingClientRect().top - dt + d.scrollTop);
          }});
          out._max = d.scrollHeight;
          return out;
        }}""") or {}

    def scroll_to_section(self, key: str, sel: str = "#detail") -> bool:
        offs = self.section_offsets(sel)
        self.note(f"detail 段落偏移 = {offs}")
        hit = [v for k, v in offs.items() if not k.startswith("_") and key[:2] in k]
        if not hit:
            return False
        self.scroll(sel, max(0, hit[0] - 14))
        return True

    def select_place(self, name: str) -> bool:
        """点开一个地点，并**校验**详情面板真的渲染了（否则重试、再不行用 JS 兑底）。"""
        for attempt in (1, 2):
            self.click_text("#placeList", name)
            self.page.wait_for_timeout(700)
            title = self.js("() => { const t = document.querySelector('#detail .d-title');"
                            " return t ? t.textContent.trim() : null; }")
            self.note(f"尝试 {attempt}: detail 标题 = {title!r}")
            if title and name[:2] in title:
                return True
        # JS 兑底：直接触发 li 的 onclick（app.js 里 li.onclick = select(id,true)）
        ok = self.js(
            "() => {\n"
            "  const li = [...document.querySelectorAll('#placeList li')]\n"
            "    .find(x => x.textContent.includes('" + name + "'));\n"
            "  if (!li) return false;\n"
            "  li.onclick(); return true;\n"
            "}"
        )
        self.page.wait_for_timeout(700)
        self.note(f"JS 兑底 onclick = {ok}")
        return bool(ok)


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


def probe(base: str) -> int:
    """不录像，只跑一遍关键交互与几何量，用于录制前的快速自检。"""
    from playwright.sync_api import sync_playwright

    exe = find_chromium()
    with sync_playwright() as pw:
        kw = {"headless": False}
        if exe:
            kw["executable_path"] = exe
        kw["args"] = [f"--window-size={W},{H}", "--force-device-scale-factor=1",
                      "--hide-scrollbars"]
        browser = pw.chromium.launch(**kw)
        ctx = browser.new_context(viewport={"width": W, "height": H},
                                  device_scale_factor=1, locale="zh-CN")
        page = ctx.new_page()
        page.goto(base, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function(
            "() => window.DJJWL && ['ready','failed'].includes(DJJWL.mapState)", timeout=120000)
        r = Rec(page, time.monotonic(), 0.0)

        def count():
            return r.js("() => document.querySelectorAll('#placeList li').length")

        print(f"地点数 初始 = {count()}", flush=True)
        r.click_text("#volumeFilter", "三·城南內外")
        page.wait_for_timeout(700)
        r.click_text("#tagFilter", "寺院")
        page.wait_for_timeout(700)
        print(f"地点数 筛选后 = {count()}", flush=True)
        r.click_text("#volumeFilter", "三·城南內外")
        page.wait_for_timeout(700)
        r.click_text("#tagFilter", "寺院")
        page.wait_for_timeout(700)
        print(f"地点数 重置后 = {count()}", flush=True)

        print(f"select_place('{TARGET}') = {r.select_place(TARGET)}", flush=True)
        print(f"段落偏移 = {r.section_offsets()}", flush=True)
        r.js("() => { document.querySelectorAll('#detail details.poem')"
             ".forEach((d, i) => { if (i < 2) d.open = true; }); }")
        page.wait_for_timeout(500)
        print(f"展开诗后偏移 = {r.section_offsets()}", flush=True)
        ctx.close()
        browser.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080/")
    ap.add_argument("--outdir", default="video")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--probe", action="store_true", help="只自检交互，不录像")
    args = ap.parse_args()

    if args.probe:
        return probe(args.base)

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

            # 9-16s 移到城南 + 筛选（用真实 chip 文案；点完再取消，保证目标地点在清单里）
            r.js("() => { const m = DJJWL.getMap();"
                 " if (m) m.centerAndZoom(new BMapGL.Point(116.401, 39.888), 14.5); }")
            r.until(10.3)
            n0 = r.list_count()
            r.click_text("#volumeFilter", "三·城南內外")
            r.until(12.0)
            n1 = r.list_count()
            r.click_text("#tagFilter", "寺院")
            r.until(13.6)
            n2 = r.list_count()
            r.note(f"筛选：{n0} -> {n1} -> {n2}")
            r.click_text("#volumeFilter", "三·城南內外")
            r.until(14.8)
            r.click_text("#tagFilter", "寺院")
            r.until(15.6)
            r.note(f"重置后 = {r.list_count()}")

            # 16-18.6s 点开目标地点（带校验 + JS 兑底）
            r.until(16.0)
            if not r.select_place(TARGET):
                r.note(f"!! {TARGET} 未能选中")
            r.until(17.8)
            r.js("() => { const m = DJJWL.getMap(); if (m) m.setZoom(15.0); }")
            r.note(f"打开 {TARGET}")

            # 18.6-32s 详情逐段：白话今译 → 原书记载 → 关联诗篇（展开）→ 清人核访
            r.until(18.8)
            r.scroll_to_section("白话")
            r.until(22.6)
            r.scroll_to_section("原书")
            r.until(26.0)
            r.js("() => { document.querySelectorAll('#detail details.poem')"
                 ".forEach((d, i) => { if (i < 2) d.open = true; }); }")
            r.until(26.6)
            r.scroll_to_section("关联")
            r.until(29.6)
            r.scroll_to_section("清人")
            r.until(31.6)
            r.note("阅读详情")

            # 32-38s 智能排线
            try:
                page.select_option("#p3Dur", "180")
                page.select_option("#p3Mode", "walking")
            except Exception as exc:  # noqa: BLE001
                r.note(f"! 排线参数失败: {exc}")
            r.until(33.0)
            r.scroll("#p3Panel", 0)
            r.until(33.8)
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
