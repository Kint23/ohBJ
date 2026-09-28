# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""用 ComfyUI 生成 4 张明代北京画面，再用 ffmpeg 合成 1920x1080 的 AI 片头。

  uv run tools/make_intro.py                # 生成缺失的静帧并合成
  uv run tools/make_intro.py --force        # 重新生成静帧
  uv run tools/make_intro.py --no-gen       # 只用已有的静帧，直接合成

产物：
  banner/intro-scene-1..4.png      AI 静帧（1920x1088）
  banner/intro-title.png           标题层（RGBA）
  banner/intro-claim.png           主张层（RGBA）
  docs/片头-帝京寻踪.mp4            1920x1080 / 25fps，约 13.6 秒
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(errors="replace")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import comfy_generate  # noqa: E402

W, H = 1920, 1080
GEN_W, GEN_H = 1920, 1088  # 必须是 16 的倍数
FPS = 25
CLIP_SEC = 4.0
FADE_SEC = 0.8

FONTS = Path("C:/Windows/Fonts")
KAI = "simkai.ttf"
HEI = "msyh.ttc"
SUN = "simsun.ttc"

PAPER = (246, 241, 230)
INK = (43, 36, 28)
INK2 = (96, 85, 68)
SEAL = (156, 43, 37)
GOLD = (184, 145, 47)
LINE = (221, 208, 182)

SCENES = [
    (
        "intro-scene-1",
        1101,
        "Traditional Chinese ink-and-colour painting in the style of a Ming dynasty literati "
        "album leaf, wide panoramic view of the Forbidden City in Beijing at dawn. Golden-yellow "
        "glazed tile roofs with upturned eaves, deep vermilion palace walls, a multi-tiered "
        "paifang gate, ancient cypress and pine trees, white marble balustrades, a wide calm "
        "moat mirroring the buildings. Layers of soft misty Western Hills behind, rendered with a "
        "few dry brushstrokes. Aged xuan-paper texture with faint fibre grain, muted restrained "
        "palette of ink black, ochre, vermilion and antique gold, soft diffused daylight, quiet "
        "and elegant, gongbi line work combined with xieyi washes, subtle ink bleed at the edges. "
        "no text, no letters, no words, no readable characters, no watermark, no signature, "
        "no seal, no people, no modern buildings, no cars, no photography",
    ),
    (
        "intro-scene-2",
        1202,
        "Traditional Chinese ink-and-colour painting, close view of the corner tower of the "
        "Forbidden City in Beijing: an ornate multi-storey pavilion with layered golden-yellow "
        "glazed eaves and dougong brackets above a deep vermilion wall, standing on a white "
        "marble base beside a calm moat. An arched marble bridge with carved stone balustrades "
        "crosses the water, reflection broken by ripples. A tall pine frames one side. Pale misty "
        "background, aged xuan-paper texture, muted palette of ink black, ochre, vermilion and "
        "antique gold, soft diffused light, elegant and quiet, gongbi line work with xieyi "
        "washes. "
        "no text, no letters, no words, no readable characters, no watermark, no signature, "
        "no seal, no people, no modern buildings, no cars, no photography",
    ),
    (
        "intro-scene-3",
        1303,
        "Traditional Chinese literati still-life painting, overhead view of a scholar's desk in a "
        "Ming dynasty study: an open woodblock-printed book on aged xuan paper, its pages filled "
        "with vertical columns of small blurred brush marks that only suggest text, a bamboo "
        "writing brush resting beside a dark inkstone, a small vermilion seal and its paste box, "
        "a few dried pine sprigs. Soft window light from one side, dust motes in the air, muted "
        "palette of ink black, ochre, vermilion and antique gold, aged paper texture with folds, "
        "elegant restrained composition, subtle ink bleed. "
        "no readable text, no letters, no legible words, no watermark, no signature, no people, "
        "no photography",
    ),
    (
        "intro-scene-4",
        1404,
        "Traditional Chinese ink-and-colour painting of a hand-drawn Ming dynasty map of Beijing "
        "on aged xuan paper: the square city wall drawn as a flat stylised plan with gate towers, "
        "the imperial palace courtyards as neat nested rectangles at the centre, rivers and "
        "bridges marked with thin ink lines, faint unreadable ink labels scattered around, a small "
        "compass dial in one corner, folds, water stains and worn edges of the paper. Flat "
        "top-down view, muted palette of ink black, ochre and vermilion, aged paper texture, "
        "traditional Chinese cartography aesthetic. "
        "no readable text, no legible characters, no watermark, no signature, no people, "
        "no photography",
    ),
]


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


def make_title_overlay() -> Path:
    """「帝京寻踪」标题层，居中，带柔化纸底。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    bw, bh = 1000, 460
    bx, by = (W - bw) // 2, (H - bh) // 2
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=18,
                        fill=(PAPER[0], PAPER[1], PAPER[2], 168))
    d.rectangle([bx + 16, by + 16, bx + bw - 16, by + bh - 16],
                outline=(LINE[0], LINE[1], LINE[2], 190), width=1)

    # 朱印「帝京」
    sx, sy, ss = W // 2 - 43, by + 52, 86
    d.rounded_rectangle([sx, sy, sx + ss, sy + ss], radius=8, fill=SEAL)
    fs = font(SUN, 38)
    for i, ch in enumerate("帝京"):
        cw = d.textlength(ch, font=fs)
        d.text((sx + ss / 2 - cw / 2, sy + 14 + i * 32), ch, font=fs,
               fill=(255, 255, 255), anchor="la")

    ft = font(KAI, 116)
    tw = sp_w(d, "帝京寻踪", ft, 18)
    sp_text(d, ((W - tw) / 2, by + 176), "帝京寻踪", ft, INK, gap=18, stroke=2)

    fb = font(HEI, 34)
    sb = "明代北京古今地图"
    bwt = sp_w(d, sb, fb, 8)
    sp_text(d, ((W - bwt) / 2, by + 340), sb, fb, INK2, gap=8)

    d.line([W / 2 - 210, by + 316, W / 2 + 210, by + 316], fill=GOLD, width=2)

    p = Path("banner/intro-title.png")
    im.save(p, "PNG")
    return p


def make_claim_overlay() -> Path:
    """底部主张句层。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    f1 = font(KAI, 58)
    claim = "探索明代北京城，百度细说帝京事"
    cw = sp_w(d, claim, f1, 4)
    cx = (W - cw) / 2
    cy = H - 178

    band = Image.new("RGBA", (W, 240), (PAPER[0], PAPER[1], PAPER[2], 150))
    im.alpha_composite(band, (0, H - 240))

    sp_text(d, (cx, cy), claim, f1, INK, gap=4, stroke=1)

    f2 = font(HEI, 26)
    sub = "基于百度地图 JSAPI GL ｜ 数据源《帝京景物略》(1635)"
    sw = sp_w(d, sub, f2, 2)
    sp_text(d, ((W - sw) / 2, cy + 86), sub, f2, INK2, gap=2)

    f3 = font(HEI, 24)
    tag = "开发者创作大赛 · 创意应用"
    d.text((W - 110, H - 62), tag, font=f3, fill=SEAL, anchor="ra")

    p = Path("banner/intro-claim.png")
    im.save(p, "PNG")
    return p


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("找不到 ffmpeg，请先安装或加入 PATH")
    return exe


def run(cmd) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-25:]
        raise SystemExit("ffmpeg 失败：\n" + "\n".join(tail))


def build_scene_clip(exe: str, still: Path, out: Path) -> None:
    """一张静帧 -> 4 秒缓慢推近的 1080p 片段。"""
    frames = int(CLIP_SEC * FPS)
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"zoompan=z='min(1+0.0016*on,1.16)':d=1"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
        f"format=yuv420p"
    )
    run([exe, "-y", "-loop", "1", "-t", str(CLIP_SEC), "-i", str(still),
         "-vf", vf, "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(out)])


def assemble(exe: str, clips, title_png: Path, claim_png: Path, out: Path) -> None:
    n = len(clips)
    total = n * CLIP_SEC - (n - 1) * FADE_SEC
    # xfade 链
    parts, prev = [], "[0:v]"
    for i in range(1, n):
        label = f"[x{i}]"
        offset = i * CLIP_SEC - i * FADE_SEC
        parts.append(f"{prev}[{i}:v]xfade=transition=fade:duration={FADE_SEC}:offset={offset:.3f}{label}")
        prev = label
    chain = ";".join(parts)

    ti = n
    ci = n + 1
    fade_t = f"{chain};" if chain else ""
    # 标题：0.6s 淡入，4.6s 淡出；主张：末尾 4.4s 淡入
    fade_t += (
        f"[{ti}:v]format=rgba,fade=t=in:st=0.6:d=0.9:alpha=1,"
        f"fade=t=out:st=4.7:d=0.9:alpha=1[t1];"
        f"[{ci}:v]format=rgba,fade=t=in:st={total - 4.4:.3f}:d=0.9:alpha=1[t2];"
        f"{prev}[t1]overlay=0:0:format=auto[v1];"
        f"[v1][t2]overlay=0:0:format=auto[v]"
    )

    cmd = [exe, "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    cmd += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(title_png)]
    cmd += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(claim_png)]
    cmd += ["-filter_complex", fade_t, "-map", "[v]",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart", str(out)]
    run(cmd)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="重新生成静帧")
    ap.add_argument("--no-gen", action="store_true", help="跳过生成，只用已有静帧")
    ap.add_argument("--out", default="docs/片头-帝京寻踪.mp4")
    args = ap.parse_args()

    stills = []
    for name, seed, prompt in SCENES:
        p = Path("banner") / f"{name}.png"
        if args.force or not p.exists():
            if args.no_gen:
                raise SystemExit(f"缺少静帧：{p}（去掉 --no-gen 即可生成）")
            print(f"[gen] {name} ...", flush=True)
            comfy_generate.generate(prompt, str(p), width=GEN_W, height=GEN_H,
                                    steps=4, seed=seed, prefix=name)
        else:
            print(f"[reuse] {p}", flush=True)
        stills.append(p)

    title_png = make_title_overlay()
    claim_png = make_claim_overlay()
    print(f"[png] {title_png}  {claim_png}", flush=True)

    exe = ffmpeg()
    with tempfile.TemporaryDirectory() as td:
        clips = []
        for i, s in enumerate(stills, 1):
            c = Path(td) / f"clip{i}.mp4"
            print(f"[clip] {s} -> {c.name}", flush=True)
            build_scene_clip(exe, s, c)
            clips.append(c)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        print(f"[mix] -> {out}", flush=True)
        assemble(exe, clips, title_png, claim_png, out)

    total = len(stills) * CLIP_SEC - (len(stills) - 1) * FADE_SEC
    print(f"{out}  {out.stat().st_size / 1024 / 1024:.1f} MB  |  {W}x{H} {FPS}fps  {total:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
