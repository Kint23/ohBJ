# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""把古地图（如《京师五城图》）做成 16:9 的开场帧，可交叉淡化进实机首帧。

设计要点
- **multiply 混合**把古图自身的白底换成项目的宣纸色，接缝完全消失（不会出现白色矩形）。
- 母版 2400×1350，用**浮点仿射**做运镜（避免 ffmpeg zoompan 的整数取整抖动）。
- 最后 `fade` 秒与实机第一帧交叉淡化，实现「古图 → 今图」的过渡。

预览：
  uv run tools/intro_card.py --image 京师五城图.jpg --out banner/_intro_preview.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

sys.stdout.reconfigure(errors="replace")

OUT_W, OUT_H = 1920, 1080
MASTER_W, MASTER_H = 2400, 1350
FONTS = Path("C:/Windows/Fonts")
KAI, HEI, SUN = "simkai.ttf", "msyh.ttc", "simsun.ttc"

PAPER = (246, 241, 230)
INK = (43, 36, 28)
INK2 = (96, 85, 68)
SEAL = (156, 43, 37)
GOLD = (184, 145, 47)
LINE = (214, 200, 172)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    p = FONTS / name
    if not p.exists():
        for alt in (KAI, HEI, SUN):
            if (FONTS / alt).exists():
                return ImageFont.truetype(str(FONTS / alt), size)
        raise SystemExit(f"找不到中文字体：{p}")
    return ImageFont.truetype(str(p), size)


def sp_w(d, s: str, f, gap: float) -> float:
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


def fit_height(src: Image.Image, h: int) -> Image.Image:
    w = max(1, round(src.width * h / src.height))
    return src.resize((w, h), Image.Resampling.LANCZOS)


def compose_master(image_path: str, *, title: str = "京師五城圖",
                   subtitle: str = "明·北京城坊巷圖",
                   lines: tuple[str, str] = ("四百年前的北京，画在纸上",
                                             "今天，我们把它叠回地图")) -> Image.Image:
    """合成 2400×1350 的母版：左文右图，纸色统一。"""
    src = Image.open(image_path).convert("RGB")
    # multiply：古图的白底 -> 项目宣纸色，于是没有可见的白色矩形边界
    src = ImageChops.multiply(src, Image.new("RGB", src.size, PAPER))

    master = Image.new("RGB", (MASTER_W, MASTER_H), PAPER)
    d = ImageDraw.Draw(master)

    # 纸纹（确定性伪随机）
    seed = 20260929
    for _ in range(16000):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x = (seed >> 8) % MASTER_W
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        y = (seed >> 8) % MASTER_H
        a = 5 + (seed % 12)
        d.point((x, y), fill=(PAPER[0] - a, PAPER[1] - a, PAPER[2] - a))

    # 双线内框（与 Banner 同一套语言）
    d.rectangle([56, 56, MASTER_W - 56, MASTER_H - 56], outline=LINE, width=2)
    d.rectangle([72, 72, MASTER_W - 72, MASTER_H - 72], outline=(206, 184, 126), width=2)

    # 右：古图
    mh = 1160
    mp = fit_height(src, mh)
    mx, my = MASTER_W - mp.width - 130, (MASTER_H - mh) // 2
    master.paste(mp, (mx, my))

    # 左：朱印 + 标题 + 副题 + 两行说明
    lx = 148
    sx, sy, ss = lx, 158, 96
    d.rounded_rectangle([sx, sy, sx + ss, sy + ss], radius=10, fill=SEAL)
    fs = font(SUN, 42)
    for i, ch in enumerate("帝京"):
        cw = d.textlength(ch, font=fs)
        d.text((sx + ss / 2 - cw / 2, sy + 16 + i * 36), ch, font=fs,
               fill=(255, 255, 255), anchor="la")

    ft = font(KAI, 120)
    sp_text(d, (lx, 300), title, ft, INK, gap=16, stroke=2)

    d.line([lx, 486, lx + 470, 486], fill=GOLD, width=4)

    fb = font(HEI, 42)
    sp_text(d, (lx, 516), subtitle, fb, INK2, gap=8)

    fl = font(KAI, 42)
    for i, ln in enumerate(lines):
        sp_text(d, (lx, 636 + i * 76), ln, fl, INK2, gap=3)

    fp = font(HEI, 26)
    d.text((lx, 1218), "《京師五城圖》· 明清北京坊巷舆圖", font=fp, fill=(150, 138, 120), anchor="la")
    return master


def kb_frame(master: Image.Image, k: float, px: float, py: float) -> Image.Image:
    """浮点仿射取景（BICUBIC 平移 + LANCZOS 降采样），位移连续不抖动。"""
    tw = max(1, int(round(OUT_W * k)))
    th = max(1, int(round(OUT_H * k)))
    mx = (master.width - tw) / 2.0
    my = (master.height - th) / 2.0
    cx = master.width / 2.0 + px * max(0.0, mx)
    cy = master.height / 2.0 + py * max(0.0, my)
    data = (1.0, 0.0, cx - tw / 2.0, 0.0, 1.0, cy - th / 2.0)
    win = master.transform((tw, th), Image.AFFINE, data, resample=Image.Resampling.BICUBIC)
    if (tw, th) == (OUT_W, OUT_H):
        return win
    return win.resize((OUT_W, OUT_H), Image.Resampling.LANCZOS)


def render_frames(master: Image.Image, first_app: Image.Image, out_dir: Path, *,
                  secs: float = 5.0, fade: float = 0.8, fps: int = 25,
                  k0: float = 1.25, k1: float = 1.17,
                  pan=(0.0, 0.0, 0.45, -0.15), quality: int = 92):
    """生成开场帧；最后 `fade` 秒与实机首帧交叉淡化。返回 [(路径, 秒), ...]。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    hold = max(0.1, secs - fade)
    n = int(round(secs * fps))
    frames: list[tuple[Path, float]] = []
    for i in range(n):
        t = i / fps
        u = smoothstep(i / max(1, n - 1))
        k = k0 + (k1 - k0) * u
        px = pan[0] + (pan[2] - pan[0]) * u
        py = pan[1] + (pan[3] - pan[1]) * u
        frame = kb_frame(master, k, px, py)
        if t >= hold:
            a = min(1.0, (t - hold) / fade)
            frame = Image.blend(frame, first_app.convert("RGB"), a)
        p = out_dir / f"i{i:06d}.jpg"
        frame.save(p, "JPEG", quality=quality, optimize=True)
        frames.append((p, t))
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="京师五城图.jpg")
    ap.add_argument("--out", default="banner/_intro_preview.png")
    ap.add_argument("--title", default="京師五城圖")
    ap.add_argument("--subtitle", default="明·北京城坊巷圖")
    args = ap.parse_args()

    master = compose_master(args.image, title=args.title, subtitle=args.subtitle)
    preview = kb_frame(master, 1.25, 0.0, 0.0)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    preview.save(args.out, "PNG")
    print(f"{args.out}  {preview.size[0]}x{preview.size[1]}  (母版 {master.size[0]}x{master.size[1]})",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
