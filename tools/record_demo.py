# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "pillow"]
# ///
"""抓高质量帧，合成 60 秒正片（1920x1080），字幕直接烧入。

为什么不用 Playwright 内置的 `record_video_dir`：
  它的 VP8 编码固定只有 **~0.8 Mbps**，1080p 的文字与地图细节会被压糊；
  后面再高码率重编码只是把这个"糊"原样搬运，补不回细节。
  改用 CDP `Page.startScreencast`（JPEG q95）逐帧抓图，按**真实时间戳**编码，
  源头质量提高一个量级，成片才真的清晰。

用法：
  uv run tools/record_demo.py                  # 录制 + 直接出成片（CDP 采集）
  uv run tools/record_demo.py --capture video  # 退回 Playwright 内置录制（旧行为）
  uv run tools/record_demo.py --probe          # 只自检交互，不录像
  uv run tools/record_demo.py --list-only      # 只打印时间轴
  uv run tools/record_demo.py --srt-only       # 只导出字幕（不录像）

产出：
  video/正片-帝京寻踪.mp4    成片（字幕已烧入）
  video/_timeline.json      各步骤时间戳与片头修剪量
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(errors="replace")

from PIL import Image  # noqa: E402

import intro_card  # noqa: E402
import make_subs  # noqa: E402

W, H = 1920, 1080
FPS = 25
END_AT = 60.0
EXTRA_TAIL = 2.0
INTRO_SEC = 5.0   # 古地图开场时长
INTRO_FADE = 0.8  # 与实机首帧交叉淡化时长

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

# 目标地点要「四段齐全」：憫忠寺只有 1 首诗且无清人核访，改用盧溝橋（卷三/16 首/有核访）
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
    """按绝对时间点执行动作，保证与字幕对齐。"""

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

    def scroll(self, selector: str, top: float) -> None:
        self.js(f"() => {{ const e = document.querySelector('{selector}');"
                f" if (e) e.scrollTo({{ top: {top}, behavior: 'smooth' }}); }}")

    def list_count(self):
        return self.js("() => document.querySelectorAll('#placeList li').length")

    def section_offsets(self, sel: str = "#detail") -> dict:
        """各 .d-sec 段落相对于滚动容器的绝对偏移（读真实几何，不猜数字）。"""
        return self.js(f"""() => {{
          const d = document.querySelector('{sel}');
          if (!d) return {{}};
          const dt = d.getBoundingClientRect().top;
          const out = {{}};
          d.querySelectorAll('.d-sec').forEach(s => {{
            const h = s.querySelector('h3');
            out[(h ? h.textContent : '').trim().slice(0, 4)] =
              Math.round(s.getBoundingClientRect().top - dt + d.scrollTop);
          }});
          return out;
        }}""") or {}

    def scroll_to_section(self, key: str, sel: str = "#detail") -> bool:
        offs = self.section_offsets(sel)
        self.note(f"段落偏移 = {offs}")
        hit = [v for k, v in offs.items() if key[:2] in k]
        if not hit:
            return False
        self.scroll(sel, max(0, hit[0] - 14))
        return True

    def select_place(self, name: str) -> bool:
        """点开地点并**校验**详情真的渲染了；失败重试，再不行用 JS 兜底。"""
        for attempt in (1, 2):
            self.click_text("#placeList", name)
            self.page.wait_for_timeout(700)
            title = self.js("() => { const t = document.querySelector('#detail .d-title');"
                            " return t ? t.textContent.trim() : null; }")
            self.note(f"尝试 {attempt}: detail 标题 = {title!r}")
            if title and name[:2] in title:
                return True
        ok = self.js(
            "() => {\n"
            "  const li = [...document.querySelectorAll('#placeList li')]\n"
            "    .find(x => x.textContent.includes('" + name + "'));\n"
            "  if (!li) return false;\n"
            "  li.onclick(); return true;\n"
            "}"
        )
        self.page.wait_for_timeout(700)
        self.note(f"JS 兜底 onclick = {ok}")
        return bool(ok)


class Screencast:
    """CDP 逐帧抓图（JPEG），质量远高于 Playwright 内置录像。"""

    def __init__(self, page, frames_dir: Path, quality: int = 95):
        self.page = page
        self.dir = frames_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.quality = quality
        self.stamps: list[float] = []
        self.count = 0
        self.bytes = 0
        self.t_first = time.monotonic()
        self.session = page.context.new_cdp_session(page)
        self.session.on("Page.screencastFrame", self._on_frame)

    def _on_frame(self, params) -> None:
        try:
            blob = base64.b64decode(params["data"])
            (self.dir / f"f{self.count:06d}.jpg").write_bytes(blob)
            self.count += 1
            self.bytes += len(blob)
            self.stamps.append(time.monotonic() - self.t_first)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                self.session.send("Page.screencastFrameAck",
                                  {"sessionId": params["sessionId"]})
            except Exception:  # noqa: BLE001
                pass

    def start(self) -> None:
        self.t_first = time.monotonic()
        self.session.send("Page.startScreencast", {
            "format": "jpeg", "quality": self.quality,
            "maxWidth": W, "maxHeight": H, "everyNthFrame": 1,
        })

    def stop(self) -> None:
        try:
            self.session.send("Page.stopScreencast")
        except Exception:  # noqa: BLE001
            pass
        (self.dir / "stamps.json").write_text(
            json.dumps({"stamps": self.stamps}), encoding="utf-8")
        span = (self.stamps[-1] - self.stamps[0]) if len(self.stamps) > 1 else 0.0
        fps = (len(self.stamps) - 1) / span if span > 0 else 0.0
        avg = self.bytes / self.count / 1024 if self.count else 0
        print(f"采集：{self.count} 帧 / {span:.1f}s  ≈ {fps:.1f} fps，"
              f"平均 {avg:.0f} KB/帧（合计 {self.bytes / 1024 / 1024:.0f} MB）", flush=True)


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("找不到 ffmpeg")
    return exe


def filter_path(p: Path) -> str:
    """滤镜参数里的路径不能带盘符冒号（会被当成选项分隔符），尽量转成相对路径。"""
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except Exception:  # noqa: BLE001
        return p.resolve().as_posix().replace(":", "\\:")


def run(cmd, quiet: bool = False) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-25:]
        raise SystemExit("ffmpeg 失败：\n" + "\n".join(tail))
    if not quiet:
        print(" ".join(str(c) for c in cmd[:3]) + " ... ok", flush=True)


def sub_times(shift: float = 0.0) -> list[tuple[float, float, str]]:
    out = []
    for i, (t, text) in enumerate(SUBS):
        end = SUBS[i + 1][0] - 0.3 if i + 1 < len(SUBS) else END_AT
        out.append((t + shift, max(end, t + 1.0) + shift, text))
    return out


def write_srt(out: Path) -> None:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = [f"{i + 1}\n{ts(a)} --> {ts(b)}\n{t}\n"
             for i, (a, b, t) in enumerate(sub_times())]
    out.write_text("\n".join(lines), encoding="utf-8")


def resample(frames: list[tuple[Path, float]], total: float, fps: int
             ) -> list[tuple[Path, float]]:
    """把变帧率时间轴重采样到均匀 fps 网格（用「最近的前一帧」填充）。

    为什么必须重采样：Chrome 的投射**只在画面变化时发帧**，读详情这类静止段
    可能有几秒的空隙。若直接按“实测间隔”写时长再给单帧封顶，
    这些空隙会被压编，总时长远远不够（实测 65s 只剩 37.6s、视频被截断）。
    重采样到固定网格后，每帧恰好 1/fps，总时长精确。
    """
    if not frames:
        return []
    frames = sorted(frames, key=lambda x: x[1])
    n = int(round(total * fps))
    out: list[tuple[Path, float]] = []
    j = 0
    for i in range(n):
        t = i / fps
        while j + 1 < len(frames) and frames[j + 1][1] <= t + 1e-6:
            j += 1
        out.append((frames[j][0], t))
    return out


def write_concat(frames: list[tuple[Path, float]], listfile: Path,
                 fps: int = FPS) -> None:
    """写 concat 清单（已重采样，每帧等长）。

    注意：路径必须写**绝对路径**。concat 解复用器会把相对路径按
    “清单文件所在目录”再解析一次，与 Path 的相对基准叠加就会变成
    `video/_frames/video/_frames/xxx.jpg` 这种拼接错误。
    """
    d = 1.0 / fps
    lines = []
    for p, _t in frames:
        lines.append(f"file '{p.resolve().as_posix()}'")
        lines.append(f"duration {d:.5f}")
    # concat 解复用器要求最后再重复一次文件名（否则末帧可能被丢弃）
    lines.append(f"file '{frames[-1][0].resolve().as_posix()}'")
    listfile.write_text("\n".join(lines) + "\n", encoding="utf-8")


def encode_frames(frames: list[tuple[Path, float]], out_path: Path, *, total: float,
                  ass: Path, music: Path, crf: int, gain: float, listfile: Path) -> None:
    """JPEG 帧序列 -> 成片，一次完成烧字幕 + 混音。"""
    grid = resample(frames, total, FPS)
    if len(grid) < 10:
        raise SystemExit(f"可用帧太少（{len(grid)}）")
    write_concat(grid, listfile, FPS)
    print(f"编码：输入 {len(frames)} 帧 -> 重采样 {len(grid)} 帧 / {total:.1f}s，CRF {crf}",
          flush=True)
    fade_out = max(0.0, total - 4.0)
    run([ffmpeg(), "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-i", str(music),
         "-filter_complex",
         f"[0:v]ass={filter_path(ass)}[v];"
         f"[1:a]volume={gain},afade=t=in:st=0:d=1.2,"
         f"afade=t=out:st={fade_out:.1f}:d=4[a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
         "-x264-params", "keyint=50:min-keyint=25",
         "-fps_mode", "cfr", "-r", str(FPS), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
         "-movflags", "+faststart", str(out_path)])


def encode_from_webm(raw: Path, out_path: Path, *, trim: float, ass: Path,
                     music: Path, crf: int, gain: float) -> None:
    fade_out = max(0.0, END_AT - 4.0)
    run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{trim:.3f}", "-i", str(raw),
         "-i", str(music),
         "-filter_complex",
         f"[0:v]ass={filter_path(ass)}[v];"
         f"[1:a]volume={gain},afade=t=in:st=0:d=1.2,"
         f"afade=t=out:st={fade_out:.1f}:d=4[a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
         "-fps_mode", "cfr", "-r", str(FPS), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-t", f"{END_AT:.3f}",
         "-movflags", "+faststart", str(out_path)])


def probe(base: str) -> int:
    """不录像，只跑一遍关键交互与几何量，用于录制前快速自检。"""
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
            "() => window.DJJWL && ['ready','failed'].includes(DJJWL.mapState)",
            timeout=120000)
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
        ctx.close()
        browser.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080/")
    ap.add_argument("--outdir", default="video")
    ap.add_argument("--capture", choices=["cdp", "video"], default="cdp")
    ap.add_argument("--quality", type=int, default=90, help="JPEG 质量（CDP 采集）")
    ap.add_argument("--crf", type=int, default=15, help="成片 CRF，越小越清晰越大")
    ap.add_argument("--gain", type=float, default=0.78, help="配乐音量")
    ap.add_argument("--font-size", type=int, default=46)
    ap.add_argument("--intro-image", dest="intro_image", default="京师五城图.jpg",
                    help="古地图开场图（默认京师五城图.jpg）")
    ap.add_argument("--intro-secs", dest="intro_secs", type=float, default=INTRO_SEC)
    ap.add_argument("--no-intro", dest="no_intro", action="store_true",
                    help="不做古地图开场")
    ap.add_argument("--no-encode", action="store_true", help="只录不编码")
    ap.add_argument("--keep-frames", dest="keep_frames", action="store_true",
                    help="采集帧留在 video/_frames（约 300 MB），便于换 CRF 重编码")
    ap.add_argument("--encode-only", dest="encode_only", action="store_true",
                    help="复用 video/_frames 重新编码，不重录")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--probe", action="store_true", help="只自检交互，不录像")
    ap.add_argument("--srt-only", dest="srt_only", action="store_true",
                    help="只导出字幕文件，不录像")
    args = ap.parse_args()

    if args.probe:
        return probe(args.base)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.list_only:
        for t, s in SUBS:
            print(f"{int(t) // 60:d}:{int(t) % 60:02d}  {s}")
        print(f"落版 {END_AT:.0f}s")
        return 0

    if args.srt_only:
        p = outdir / "正片字幕.srt"
        write_srt(p)
        print(f"{p}  {len(SUBS)} 条（中间产物，成片里的字幕已烧入）", flush=True)
        return 0

    from playwright.sync_api import sync_playwright

    out_mp4 = outdir / "正片-帝京寻踪.mp4"
    music = outdir / "music.wav"
    ass = outdir / "subs.ass"

    if args.encode_only:
        fdir = outdir / "_frames"
        plan = json.loads((fdir / "plan.json").read_text(encoding="utf-8"))
        frames = [(fdir / n, t) for n, t in plan["frames"]]
        shift = float(plan.get("shift", 0.0))
        total = float(plan["total"])
        make_subs.write_ass(sub_times(shift), ass, width=W, height=H, size=args.font_size)
        encode_frames(frames, out_mp4, total=total, ass=ass, music=music,
                      crf=args.crf, gain=args.gain, listfile=fdir / "frames.txt")
        print(f"{out_mp4}  {out_mp4.stat().st_size / 1024 / 1024:.1f} MB  (CRF {args.crf})",
              flush=True)
        return 0

    exe = find_chromium()
    print(f"chromium: {exe or '(默认)'}   采集方式: {args.capture}", flush=True)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        recdir = tmp / "rec"
        recdir.mkdir()
        with sync_playwright() as pw:
            kw = {"headless": False}
            if exe:
                kw["executable_path"] = exe
            kw["args"] = [f"--window-size={W},{H}", "--force-device-scale-factor=1",
                          "--hide-scrollbars",
                          "--disable-features=CalculateNativeWinOcclusion"]
            browser = pw.chromium.launch(**kw)
            ctx_kw = dict(viewport={"width": W, "height": H}, device_scale_factor=1,
                          locale="zh-CN")
            if args.capture == "video":
                ctx_kw["record_video_dir"] = str(recdir)
                ctx_kw["record_video_size"] = {"width": W, "height": H}
            ctx = browser.new_context(**ctx_kw)
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

            sc = None
            if args.capture == "cdp":
                sc = Screencast(page, (outdir / "_frames") if args.keep_frames
                                else (tmp / "frames"), quality=args.quality)
                sc.start()

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

            # 5-9s 拉远看全城
            for z in (12.4, 11.9, 11.5, 11.2, 11.0):
                r.js(f"() => {{ const m = DJJWL.getMap(); if (m) m.setZoom({z}); }}")
                r.until(r.now + 0.75)
            r.note("zoom out 全城")

            # 9-16s 移到城南 + 筛选（真实 chip 文案；点完再取消）
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

            # 16-18.6s 点开目标地点
            r.until(16.0)
            if not r.select_place(TARGET):
                r.note(f"!! {TARGET} 未能选中")
            r.until(17.8)
            r.js("() => { const m = DJJWL.getMap(); if (m) m.setZoom(15.0); }")
            r.note(f"打开 {TARGET}")

            # 18.6-32s 详情逐段
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
            r.until(END_AT + EXTRA_TAIL)
            r.note("endcard")

            if sc:
                sc.stop()
            ctx.close()
            browser.close()

        # ---- 组装帧时间轴：古地图开场 + 实机画面 ----
        total = END_AT
        shift = 0.0
        frames: list[tuple[Path, float]] = []
        use_intro = (not args.no_intro) and args.capture == "cdp" and sc is not None
        if use_intro:
            img = Path(args.intro_image)
            if img.exists():
                shift = args.intro_secs
                total = END_AT + shift
                idx0 = next((i for i, t in enumerate(sc.stamps)
                             if t >= offset - 1e-3), None)
                if idx0 is None:
                    raise SystemExit("拿不到实机首帧，无法做开场过渡")
                first_app = Image.open(sc.dir / f"f{idx0:06d}.jpg")
                master = intro_card.compose_master(str(img))
                frames += intro_card.render_frames(
                    master, first_app, sc.dir, secs=args.intro_secs,
                    fade=INTRO_FADE, fps=FPS)
                print(f"开场：{args.intro_secs:.1f}s 《{img.stem}》（末 {INTRO_FADE}s 淡入实机）",
                      flush=True)
            else:
                print(f"! 找不到开场图 {img}，跳过开场", flush=True)
        elif args.capture != "cdp":
            print("! --capture video 模式不支持开场，已跳过", flush=True)

        if sc is not None:
            frames += [(sc.dir / f"f{i:06d}.jpg", t - offset + shift)
                       for i, t in enumerate(sc.stamps)
                       if t >= offset - 1e-3 and t - offset + shift <= total + 0.35]
        frames.sort(key=lambda x: x[1])
        (sc.dir / "plan.json").write_text(json.dumps(
            {"total": total, "shift": shift,
             "frames": [[p.name, round(t, 4)] for p, t in frames]},
            ensure_ascii=False), encoding="utf-8")

        # 字幕：直接从代码里的时间轴写 ASS（按 shift 平移）
        make_subs.write_ass(sub_times(shift), ass, width=W, height=H,
                            size=args.font_size)

        if args.no_encode:
            print("--no-encode：跳过编码", flush=True)
            return 0

        if args.capture == "cdp":
            if not frames:
                raise SystemExit("没有可用帧")
            encode_frames(frames, out_mp4, total=total, ass=ass, music=music,
                          crf=args.crf, gain=args.gain, listfile=sc.dir / "frames.txt")
        else:
            webms = sorted(recdir.glob("*.webm"))
            if not webms:
                raise SystemExit("没有拿到录像文件")
            raw = outdir / "_raw.webm"
            shutil.move(str(webms[0]), str(raw))
            encode_from_webm(raw, out_mp4, trim=offset, ass=ass,
                             music=music, crf=args.crf, gain=args.gain)

    (outdir / "_timeline.json").write_text(json.dumps(
        {"trim_start": round(offset, 3), "end_at": END_AT, "capture": args.capture,
         "crf": args.crf, "subs": [{"t": t, "text": s} for t, s in SUBS]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    size = out_mp4.stat().st_size / 1024 / 1024
    print(f"{out_mp4}  {size:.1f} MB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
