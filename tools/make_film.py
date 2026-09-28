# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""AI 影像生成器：ComfyUI 出静帧 + Pillow 逐帧亚像素运镜 + ffmpeg 编码。

为什么不用 ffmpeg 的 zoompan：
  zoompan 的 x/y 只能取整数，1080p 下每帧位移不足 1 像素时会在两个整数间反复跳，
  视觉上就是"震动/哆嗦"。这里改成用 Pillow 的仿射变换做**浮点采样**（bicubic），
  位移与缩放都是连续量，再套 smoothstep 缓动，运镜才是顺的。

两个"片子"：
  intro       片头 4 幕：紫禁城全景 → 角楼石桥 → 书斋古籍 → 明代城图（带标题卡 + 主张句）
  atmosphere  氛围 10 幕：从 30 处明代地点里选 10 处，每幕带「明清名 → 今名」字幕

用法：
  uv run tools/make_film.py --program intro
  uv run tools/make_film.py --program atmosphere
  uv run tools/make_film.py --program all --concat        # 再拼成一条「开场」
  uv run tools/make_film.py --program atmosphere --no-gen # 复用已有静帧

产物：
  banner/film-*.png                  AI 静帧（2400x1344，原生超采样）
  video/片头-帝京寻踪.mp4
  video/氛围-帝京寻踪.mp4
  video/开场-帝京寻踪.mp4            （--concat 时生成）
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(errors="replace")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import comfy_generate  # noqa: E402

OUT_W, OUT_H = 1920, 1080
MASTER_W, MASTER_H = 2400, 1350  # 16:9 母版；k=1.25 时正好等于母版全幅
GEN_W, GEN_H = 2400, 1344  # 必须是 16 的倍数
FPS = 25

FONTS = Path("C:/Windows/Fonts")
KAI, HEI, SUN = "simkai.ttf", "msyh.ttc", "simsun.ttc"

PAPER = (246, 241, 230)
INK = (43, 36, 28)
INK2 = (96, 85, 68)
SEAL = (156, 43, 37)
GOLD = (184, 145, 47)
LINE = (221, 208, 182)

STYLE = (
    " Traditional Chinese ink-and-colour painting in the style of a Ming dynasty literati album "
    "leaf. Aged xuan-paper texture with faint fibre grain, muted restrained palette of ink black, "
    "ochre, vermilion and antique gold, soft diffused daylight, elegant and quiet, gongbi line "
    "work combined with xieyi washes, subtle ink bleed at the edges. "
    "no text, no letters, no words, no readable characters, no watermark, no signature, no seal, "
    "no people, no modern buildings, no cars, no photography"
)


@dataclass
class Scene:
    name: str
    seed: int
    prompt: str
    caption: tuple[str, str] | None = None
    dur: float = 4.0
    k0: float = 1.25
    k1: float = 1.10
    pan: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # panx0,pany0,panx1,pany1 ∈ [-1,1]
    still: str | None = None  # 直接指定已有静帧（如页面截图），跳过生成


INTRO = [
    Scene(
        "intro-1",
        1101,
        "Wide panoramic view of the Forbidden City in Beijing at dawn. Golden-yellow glazed tile "
        "roofs with upturned eaves, deep vermilion palace walls, a multi-tiered paifang gate, "
        "ancient cypress and pine trees, white marble balustrades, a wide calm moat mirroring the "
        "buildings. Layers of soft misty Western Hills behind, rendered with a few dry "
        "brushstrokes." + STYLE,
        dur=4.0, k0=1.25, k1=1.10, pan=(0.0, 0.0, 0.35, -0.25),
    ),
    Scene(
        "intro-2",
        1202,
        "Close view of the corner tower of the Forbidden City in Beijing: an ornate multi-storey "
        "pavilion with layered golden-yellow glazed eaves and dougong brackets above a deep "
        "vermilion wall, standing on a white marble base beside a calm moat. An arched marble "
        "bridge with carved stone balustrades crosses the water, reflection broken by ripples. "
        "A tall pine frames one side. Pale misty background." + STYLE,
        dur=4.0, k0=1.25, k1=1.10, pan=(-0.35, 0.2, 0.15, -0.2),
    ),
    Scene(
        "intro-3",
        1303,
        "Overhead view of a scholar's desk in a Ming dynasty study: an open woodblock-printed book "
        "on aged xuan paper, its pages filled with vertical columns of small blurred brush marks "
        "that only suggest text, a bamboo writing brush resting beside a dark inkstone, a small "
        "vermilion seal and its paste box, a few dried pine sprigs. Soft window light from one "
        "side." + STYLE,
        dur=4.0, k0=1.08, k1=1.24, pan=(0.3, 0.15, -0.2, -0.2),
    ),
    Scene(
        "intro-4",
        1404,
        "A hand-drawn Ming dynasty map of Beijing on aged xuan paper: the square city wall drawn as "
        "a flat stylised plan with gate towers, the imperial palace courtyards as neat nested "
        "rectangles at the centre, rivers and bridges marked with thin ink lines, faint unreadable "
        "ink labels scattered around, a small compass dial in one corner, folds, water stains and "
        "worn edges of the paper. Flat top-down view." + STYLE,
        dur=4.0, k0=1.24, k1=1.08, pan=(-0.2, 0.25, 0.25, -0.15),
    ),
]

ATMO = [
    Scene(
        "atmo-01",
        2101,
        "The Biyong hall of the Imperial College in Beijing: a square pavilion with a double-eaved "
        "dark-tile roof standing on an island in a round water pool, surrounded by ancient cypress "
        "trees and white marble balustrades, stone drums beside the courtyard." + STYLE,
        caption=("太學石鼓", "北京孔庙和国子监博物馆"),
        dur=5.0, k0=1.25, k1=1.09, pan=(0.0, 0.15, -0.3, -0.2),
    ),
    Scene(
        "atmo-02",
        2102,
        "A tall whitewashed Tibetan-style stupa with a bell-shaped body and a slender gilded finial, "
        "rising above the grey tiled roofs and old trees of an old Beijing neighbourhood, soft "
        "morning haze." + STYLE,
        caption=("白塔寺", "妙应寺白塔"),
        dur=5.0, k0=1.25, k1=1.08, pan=(0.2, 0.3, -0.2, -0.3),
    ),
    Scene(
        "atmo-03",
        2103,
        "A wide calm lake in the old city with weeping willows along the bank, a low stone arch "
        "bridge, lotus leaves floating on the water, the silhouette of a drum tower in the "
        "distance." + STYLE,
        caption=("十刹海", "什刹海（前海）"),
        dur=5.0, k0=1.25, k1=1.10, pan=(-0.35, 0.0, 0.3, 0.15),
    ),
    Scene(
        "atmo-04",
        2104,
        "An ancient Buddhist temple courtyard: a low hall with a grey tiled roof and dark wooden "
        "columns behind a bronze incense burner, two old lilac trees in bloom, weathered stone "
        "lions, quiet emptiness." + STYLE,
        caption=("憫忠寺", "法源寺"),
        dur=5.0, k0=1.24, k1=1.08, pan=(0.25, 0.2, -0.25, -0.25),
    ),
    Scene(
        "atmo-05",
        2105,
        "A long stone arch bridge with many small carved stone lions along its balustrade, crossing "
        "a wide slow river at dusk, faint hills and a pale sky beyond." + STYLE,
        caption=("盧溝橋", "卢沟桥"),
        dur=5.0, k0=1.25, k1=1.07, pan=(-0.3, 0.1, 0.35, -0.1),
    ),
    Scene(
        "atmo-06",
        2106,
        "The entrance of a Daoist temple: an ornate wooden paifang archway with painted brackets, a "
        "pair of stone lions, ancient pines and deep red walls, morning light." + STYLE,
        caption=("白雲觀", "白云观"),
        dur=5.0, k0=1.08, k1=1.24, pan=(-0.2, -0.2, 0.25, 0.2),
    ),
    Scene(
        "atmo-07",
        2107,
        "A slender octagonal brick pagoda of many storeys rising above grey courtyard roofs and "
        "bare old trees, soft haze, quiet monochrome sky." + STYLE,
        caption=("天寧寺", "天宁寺塔"),
        dur=5.0, k0=1.25, k1=1.09, pan=(0.1, 0.3, -0.25, -0.3),
    ),
    Scene(
        "atmo-08",
        2108,
        "An autumn mountain temple on a wooded slope: tiered halls with grey tiled roofs among red "
        "maple and dark pines, terraced stone paths winding upward, mist filling the valley." + STYLE,
        caption=("碧雲寺", "西山·碧云寺"),
        dur=5.0, k0=1.25, k1=1.08, pan=(-0.3, 0.25, 0.3, -0.2),
    ),
    Scene(
        "atmo-09",
        2109,
        "A riverside temple gate beside a canal with a carved stone bridge, weeping willows, a "
        "moored wooden boat, low hills and reeds in the distance." + STYLE,
        caption=("萬壽寺", "万寿寺（北京艺术博物馆）"),
        dur=5.0, k0=1.24, k1=1.09, pan=(0.3, 0.0, -0.3, 0.2),
    ),
    Scene(
        "atmo-10",
        2110,
        "A large temple hall with a wide grey tiled roof on a raised stone platform, ancient trees, "
        "old stone stele tablets bearing faint illegible inscriptions, a quiet empty courtyard at "
        "golden hour." + STYLE,
        caption=None,  # 末幕留白给结尾主张句，否则左下角字幕会与它挤在同一横带
        dur=5.5, k0=1.25, k1=1.08, pan=(0.0, 0.2, 0.3, -0.25),
    ),
]

PROGRAMS = {"intro": INTRO, "atmosphere": ATMO}
DEMO_MANIFEST = Path("banner/demo/manifest.json")


def demo_scenes() -> list[Scene]:
    """把 tools/capture_demo.py 截的页面截图变成一套「幕」。"""
    if not DEMO_MANIFEST.exists():
        raise SystemExit(f"缺少 {DEMO_MANIFEST}，请先运行 uv run tools/capture_demo.py")
    items = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    return [
        Scene(name=Path(it["file"]).stem, seed=0, prompt="",
              caption=(it["ming"], it["modern"]), dur=5.5,
              still=str(Path("banner/demo") / it["file"]))
        for it in items
    ]


# ---------------------------------------------------------------- helpers


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    p = FONTS / name
    if not p.exists():
        for alt in (KAI, HEI, SUN):
            if (FONTS / alt).exists():
                return ImageFont.truetype(str(FONTS / alt), size)
        raise SystemExit(f"找不到中文字体：{p}")
    return ImageFont.truetype(str(p), size)


def sp_w(d: ImageDraw.ImageDraw, s: str, f, gap: float) -> float:
    if not s:
        return 0.0
    return sum(d.textlength(c, font=f) for c in s) + gap * (len(s) - 1)


def sp_text(d, xy, s, f, fill, gap=0.0, stroke=0):
    x, y = xy
    for c in s:
        d.text((x, y), c, font=f, fill=fill, anchor="la",
               stroke_width=stroke, stroke_fill=fill)
        x += d.textlength(c, font=f) + gap
    return x


def smoothstep(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


def cover(im: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = im.size
    s = max(w / sw, h / sh)
    im = im.resize((max(1, round(sw * s)), max(1, round(sh * s))), Image.Resampling.LANCZOS)
    bw, bh = im.size
    left, top = (bw - w) // 2, (bh - h) // 2
    return im.crop((left, top, left + w, top + h))


def kb_frame(master: Image.Image, k: float, px: float, py: float) -> Image.Image:
    """浮点仿射取景：k 为取样窗口相对输出的倍率（>=1 表示缩小 -> 放大主体）。

    两步走：先在母版上做**纯平移**的亚像素取样（BICUBIC 插值），再用 LANCZOS 降采样到输出。
    - 平移量是浮点数 -> 不存在整数取整造成的"哆嗦"
    - LANCZOS 负责降采样 -> 屋脊细线不会闪烁
    （AFFINE 只支持 NEAREST/BILINEAR/BICUBIC，没有 LANCZOS，所以要分两步）
    """
    tw = max(1, int(round(OUT_W * k)))
    th = max(1, int(round(OUT_H * k)))
    mx = (MASTER_W - tw) / 2.0
    my = (MASTER_H - th) / 2.0
    cx = MASTER_W / 2.0 + px * max(0.0, mx)
    cy = MASTER_H / 2.0 + py * max(0.0, my)
    data = (1.0, 0.0, cx - tw / 2.0, 0.0, 1.0, cy - th / 2.0)
    win = master.transform((tw, th), Image.AFFINE, data, resample=Image.Resampling.BICUBIC)
    if (tw, th) == (OUT_W, OUT_H):
        return win
    return win.resize((OUT_W, OUT_H), Image.Resampling.LANCZOS)


def paste_faded(frame: Image.Image, layer: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        frame.paste(layer, (0, 0), layer)
        return frame
    tmp = layer.copy()
    tmp.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    frame.paste(tmp, (0, 0), tmp)
    return frame


# ---------------------------------------------------------------- overlays


def title_overlay() -> Image.Image:
    im = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    bw, bh = 1000, 460
    bx, by = (OUT_W - bw) // 2, (OUT_H - bh) // 2
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=18, fill=(*PAPER, 168))
    d.rectangle([bx + 16, by + 16, bx + bw - 16, by + bh - 16], outline=(*LINE, 190), width=1)

    sx, sy, ss = OUT_W // 2 - 43, by + 52, 86
    d.rounded_rectangle([sx, sy, sx + ss, sy + ss], radius=8, fill=SEAL)
    fs = font(SUN, 38)
    for i, ch in enumerate("帝京"):
        cw = d.textlength(ch, font=fs)
        d.text((sx + ss / 2 - cw / 2, sy + 14 + i * 32), ch, font=fs, fill=(255, 255, 255),
               anchor="la")

    ft = font(KAI, 116)
    tw = sp_w(d, "帝京寻踪", ft, 18)
    sp_text(d, ((OUT_W - tw) / 2, by + 176), "帝京寻踪", ft, INK, gap=18, stroke=2)
    d.line([OUT_W / 2 - 210, by + 316, OUT_W / 2 + 210, by + 316], fill=GOLD, width=2)

    fb = font(HEI, 34)
    sb = "明代北京古今地图"
    bwt = sp_w(d, sb, fb, 8)
    sp_text(d, ((OUT_W - bwt) / 2, by + 340), sb, fb, INK2, gap=8)
    return im


def claim_overlay() -> Image.Image:
    im = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    band = Image.new("RGBA", (OUT_W, 240), (*PAPER, 150))
    im.alpha_composite(band, (0, OUT_H - 240))

    f1 = font(KAI, 58)
    claim = "探索明代北京城，百度细说帝京事"
    cw = sp_w(d, claim, f1, 4)
    sp_text(d, ((OUT_W - cw) / 2, OUT_H - 178), claim, f1, INK, gap=4, stroke=1)

    f2 = font(HEI, 26)
    sub = "基于百度地图 JSAPI GL ｜ 数据源《帝京景物略》(1635)"
    sw = sp_w(d, sub, f2, 2)
    sp_text(d, ((OUT_W - sw) / 2, OUT_H - 92), sub, f2, INK2, gap=2)

    f3 = font(HEI, 24)
    d.text((OUT_W - 110, OUT_H - 62), "开发者创作大赛 · 创意应用", font=f3, fill=SEAL, anchor="ra")
    return im


def caption_overlay(ming: str, modern: str) -> Image.Image:
    """左下角「明清名 → 今名」纸签。"""
    im = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f1, f2, fa = font(KAI, 58), font(HEI, 31), font(HEI, 40)
    pad, gap = 36, 28
    w1 = sp_w(d, ming, f1, 6)
    w2 = sp_w(d, modern, f2, 2)
    wa = d.textlength("→", font=fa)
    bw = int(pad * 2 + w1 + gap + wa + gap + w2)
    bh = 124
    bx, by = 96, OUT_H - bh - 96
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=10, fill=(*PAPER, 176))
    d.rectangle([bx, by, bx + 7, by + bh], fill=(*SEAL, 210))
    d.rectangle([bx + 14, by + 10, bx + bw - 10, by + bh - 10],
                outline=(*LINE, 170), width=1)
    cy = by + bh / 2
    sp_text(d, (bx + pad + 14, cy - 40), ming, f1, INK, gap=6, stroke=1)
    x = bx + pad + 14 + w1 + gap
    d.text((x, cy), "→", font=fa, fill=GOLD, anchor="lm")
    x += wa + gap
    d.text((x, cy), modern, font=f2, fill=SEAL, anchor="lm")
    return im


# ---------------------------------------------------------------- pipeline


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("找不到 ffmpeg，请安装或加入 PATH")
    return exe


def ensure_stills(scenes, force: bool, no_gen: bool) -> list[Path]:
    out = []
    for s in scenes:
        if s.still:
            p = Path(s.still)
            if not p.exists():
                raise SystemExit(f"缺少静帧：{p}")
            print(f"[use] {p.name}", flush=True)
            out.append(p)
            continue
        p = Path("banner") / f"film-{s.name}.png"
        if force or not p.exists():
            if no_gen:
                raise SystemExit(f"缺少静帧：{p}（去掉 --no-gen 即可生成）")
            print(f"[gen] {p.name} ...", flush=True)
            comfy_generate.generate(s.prompt, str(p), width=GEN_W, height=GEN_H,
                                    steps=4, seed=s.seed, prefix=f"film-{s.name}", quiet=True)
        else:
            print(f"[reuse] {p.name}", flush=True)
        out.append(p)
    return out


def build(scenes, stills, out_path: Path, *, fade: float, title=False, claim=True,
          captions=False, fps=FPS, motion: float = 1.0):
    n = len(scenes)
    starts, t = [], 0.0
    for s in scenes:
        starts.append(t)
        t += s.dur - fade
    total = t + fade
    frames = int(round(total * fps))
    print(f"[film] {out_path}  {n} 幕  {total:.1f}s  {frames} 帧", flush=True)

    mw, mh = (OUT_W, OUT_H) if motion <= 0 else (MASTER_W, MASTER_H)
    masters = [cover(Image.open(p).convert("RGB"), mw, mh) for p in stills]
    caps = [caption_overlay(*s.caption) if (captions and s.caption) else None for s in scenes]
    title_img = title_overlay() if title else None
    claim_img = claim_overlay() if claim else None

    overlays = []
    if title_img is not None:
        overlays.append((title_img, 0.6, min(total - 0.4, 5.4), 0.9))
    if claim_img is not None:
        overlays.append((claim_img, total - 4.4, total - 0.05, 0.9))

    cmd = [ffmpeg(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OUT_W}x{OUT_H}",
           "-r", str(fps), "-i", "-", "-an",
           "-c:v", "libx264", "-preset", "slow", "-crf", "18",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        for fi in range(frames):
            ts = fi / fps
            parts = []
            for i, s in enumerate(scenes):
                a, b = starts[i], starts[i] + s.dur
                if ts < a - 1e-6 or ts > b + 1e-6:
                    continue
                w = 1.0
                if i > 0:
                    w = min(w, (ts - a) / fade)
                if i < n - 1:
                    w = min(w, (b - ts) / fade)
                if w > 1e-4:
                    u = smoothstep((ts - a) / s.dur)
                    k = 1.0 + (s.k0 + (s.k1 - s.k0) * u - 1.0) * motion
                    px = (s.pan[0] + (s.pan[2] - s.pan[0]) * u) * motion
                    py = (s.pan[1] + (s.pan[3] - s.pan[1]) * u) * motion
                    parts.append((w, kb_frame(masters[i], k, px, py)))
            if not parts:
                parts = [(1.0, kb_frame(masters[-1], scenes[-1].k1, 0.0, 0.0))]
            if len(parts) == 1:
                frame = parts[0][1]
            else:
                parts.sort(key=lambda p: -p[0])
                frame = Image.blend(parts[1][1], parts[0][1], parts[0][0])

            if caps and len(parts) >= 1:
                dom = parts[0]
                idx = min(range(n), key=lambda i: abs(
                    (ts - starts[i]) - scenes[i].dur / 2))
                cap = caps[idx]
                if cap is not None:
                    a = starts[idx]
                    c0, c1 = a + 0.55, a + scenes[idx].dur - fade - 0.15
                    if c0 <= ts <= c1:
                        al = min(1.0, (ts - c0) / 0.45, (c1 - ts) / 0.45)
                        if al > 0.01:
                            frame = paste_faded(frame, cap, al)

            for ov, t0, t1, fa in overlays:
                if t0 <= ts <= t1:
                    al = min(1.0, (ts - t0) / fa, (t1 - ts) / fa)
                    if al > 0.01:
                        frame = paste_faded(frame, ov, al)

            proc.stdin.write(frame.tobytes())
            if (fi + 1) % 100 == 0:
                print(f"  {fi + 1}/{frames}", flush=True)
    except BrokenPipeError:
        err = proc.stderr.read().decode("utf-8", "replace")
        raise SystemExit("ffmpeg 中断：\n" + err[-2000:]) from None
    finally:
        if proc.stdin:
            proc.stdin.close()

    rc = proc.wait()
    if rc != 0:
        err = proc.stderr.read().decode("utf-8", "replace")
        raise SystemExit(f"ffmpeg 退出码 {rc}：\n{err[-2000:]}")
    size = out_path.stat().st_size / 1024 / 1024
    print(f"{out_path}  {size:.1f} MB  |  {OUT_W}x{OUT_H} {fps}fps  {total:.1f}s", flush=True)
    return total


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    cmd = [ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    lst.unlink(missing_ok=True)
    if r.returncode != 0:
        raise SystemExit("concat 失败：\n" + (r.stderr or "")[-1500:])
    print(f"{out}  {out.stat().st_size / 1024 / 1024:.1f} MB  （{' + '.join(p.name for p in parts)}）",
          flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", choices=["intro", "atmosphere", "demo", "all"], default="all")
    ap.add_argument("--force", action="store_true", help="重新生成静帧")
    ap.add_argument("--no-gen", action="store_true", help="只用已有静帧")
    ap.add_argument("--concat", action="store_true", help="再拼成一条「开场」")
    ap.add_argument("--concat-only", action="store_true", help="只拼接已有影片，不渲染")
    ap.add_argument("--outdir", default="video")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    made = []

    if args.concat_only:
        parts = [p for p in (outdir / "片头-帝京寻踪.mp4", outdir / "氛围-帝京寻踪.mp4") if p.exists()]
        if len(parts) < 2:
            raise SystemExit("没有可拼接的影片（需要片头 + 氛围两条都存在）")
        concat(parts, outdir / "开场-帝京寻踪.mp4")
        return 0

    if args.program in ("intro", "all"):
        stills = ensure_stills(INTRO, args.force, args.no_gen)
        p = outdir / "片头-帝京寻踪.mp4"
        build(INTRO, stills, p, fade=0.8, title=True, claim=True, captions=False)
        made.append(p)

    if args.program in ("atmosphere", "all"):
        stills = ensure_stills(ATMO, args.force, args.no_gen)
        p = outdir / "氛围-帝京寻踪.mp4"
        build(ATMO, stills, p, fade=1.0, title=False, claim=True, captions=True)
        made.append(p)

    if args.program == "demo":
        scenes = demo_scenes()
        stills = ensure_stills(scenes, args.force, args.no_gen)
        p = outdir / "演示-帝京寻踪.mp4"
        # 页面截图不做推镜，否则 UI 文字会发虚
        build(scenes, stills, p, fade=0.6, title=False, claim=True, captions=True, motion=0.0)
        made.append(p)

    if args.concat or args.program == "all":
        parts = [p for p in (outdir / "片头-帝京寻踪.mp4", outdir / "氛围-帝京寻踪.mp4") if p.exists()]
        if len(parts) > 1:
            concat(parts, outdir / "开场-帝京寻踪.mp4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
