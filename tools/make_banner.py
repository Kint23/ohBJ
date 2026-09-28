# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""把参赛 Banner 直接渲染成 1200×675 的 PNG / JPG 文件（无需手动截图）。

设计坐标与 banner/index.html 的 Canvas 版完全一致，
因此 banner 工具里导出的「版式参考图」与实际成品可以逐像素对齐。

用法：
  uv run tools/make_banner.py
  uv run tools/make_banner.py --claim "众里寻他千百度，一图说尽帝京事"
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 675
FONTS = Path("C:/Windows/Fonts")

PAPER = (246, 241, 230)
PAPER2 = (239, 231, 215)
INK = (43, 36, 28)
INK2 = (107, 95, 77)
SEAL = (156, 43, 37)
LINE = (221, 208, 182)
GOLD = (184, 145, 47)
MUTED = (140, 130, 113)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    p = FONTS / name
    if not p.exists():
        for alt in ("simkai.ttf", "msyh.ttc", "simsun.ttc", "simhei.ttf"):
            q = FONTS / alt
            if q.exists():
                return ImageFont.truetype(str(q), size)
        raise SystemExit(f"找不到可用中文字体：{p}")
    return ImageFont.truetype(str(p), size)


def sp_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont, gap: float) -> float:
    if not s:
        return 0.0
    return sum(draw.textlength(c, font=f) for c in s) + gap * (len(s) - 1)


def sp_text(draw, xy, s, f, fill, gap=0.0, stroke=0):
    x, y = xy
    for c in s:
        draw.text((x, y), c, font=f, fill=fill, anchor="la",
                  stroke_width=stroke, stroke_fill=fill)
        x += draw.textlength(c, font=f) + gap
    return x


def fit(draw, s, path, max_w, start, gap, min_size=18):
    size = start
    while size > min_size:
        f = font(path, size)
        if sp_w(draw, s, f, gap) <= max_w:
            return f, size
        size -= 1
    return font(path, min_size), min_size


def cover_fit(path: str, w: int, h: int) -> Image.Image:
    """Open `path` and scale-crop it to exactly w x h (like CSS background-size: cover)."""
    src = Image.open(path).convert("RGB")
    sw, sh = src.size
    scale = max(w / sw, h / sh)
    src = src.resize((max(1, round(sw * scale)), max(1, round(sh * scale))), Image.LANCZOS)
    bw, bh = src.size
    left, top = (bw - w) // 2, (bh - h) // 2
    return src.crop((left, top, left + w, top + h))


def base_canvas(bg: str | None, mix: float) -> Image.Image:
    """Plain paper, or an AI background washed toward the paper colour by `mix`."""
    if not bg:
        return Image.new("RGB", (W, H), PAPER)
    if not Path(bg).exists():
        raise SystemExit(f"找不到背景图：{bg}")
    im = cover_fit(bg, W, H)
    if mix > 0:
        im = Image.blend(im, Image.new("RGB", (W, H), PAPER), min(1.0, max(0.0, mix)))
    return im


VEIL_RGB = (253, 250, 244)


def apply_veil(img: Image.Image, bands) -> Image.Image:
    """Vertical alpha ramps of paper-white: bands = [(y0, y1, alpha@y0, alpha@y1), ...].

    Gradients (not hard rectangles) are used so no seam shows across the artwork.
    """
    if not bands:
        return img
    h = img.size[1]
    mask = Image.new("L", (1, h), 0)
    px = mask.load()
    for y0, y1, a0, a1 in bands:
        span = max(1, y1 - y0)
        for y in range(max(0, int(y0)), min(h, int(y1))):
            t = (y - y0) / span
            px[0, y] = max(px[0, y], int(round(a0 + (a1 - a0) * t)))
    mask = mask.resize(img.size, Image.NEAREST)
    return Image.composite(Image.new("RGB", img.size, VEIL_RGB), img, mask)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim", default="探索明代北京城，百度细说帝京事")
    ap.add_argument("--title", default="帝京寻踪")
    ap.add_argument("--subtitle", default="明代北京古今地图")
    ap.add_argument("--outdir", default="banner")
    ap.add_argument("--bg", default=None, help="ComfyUI 生成的背景图（自动 cover 裁到 1200x675）")
    ap.add_argument("--bg-mix", type=float, default=0.62, help="纸色叠加强度：0=原图，1=纯纸色")
    ap.add_argument("--veil-top", type=int, default=0, help="顶部白色柔化遮罩 0-255")
    ap.add_argument("--veil-bottom", type=int, default=0, help="底部白色柔化遮罩 0-255")
    ap.add_argument("--stem", default="帝京寻踪-banner-1200x675", help="输出文件名主干")
    args = ap.parse_args()

    KAI, HEI, SUN = "simkai.ttf", "msyh.ttc", "simsun.ttc"
    img = base_canvas(args.bg, args.bg_mix)
    bands = []
    if args.veil_top:
        bands.append((0, 214, args.veil_top, 0))
    if args.veil_bottom:
        bands.append((404, 566, 0, args.veil_bottom))
        bands.append((566, H, args.veil_bottom, args.veil_bottom))
    img = apply_veil(img, bands)
    d = ImageDraw.Draw(img)

    # 纸纹（确定性伪随机）
    seed = 20260929
    for _ in range(9000):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x = (seed >> 8) % W
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        y = (seed >> 8) % H
        a = 6 + (seed % 14)
        d.point((x, y), fill=(PAPER[0] - a, PAPER[1] - a, PAPER[2] - a))

    # 右上角同心弧（呼应古地图罗盘/水波）
    for k in range(5):
        r = 34 + k * 22
        col = (int(PAPER[0] * 0.72 + GOLD[0] * 0.28), int(PAPER[1] * 0.74 + GOLD[1] * 0.26), int(PAPER[2] * 0.78 + GOLD[2] * 0.22))
        d.arc([W - 120 - r, 96 - r, W - 120 + r, 96 + r], 99, 261, fill=col, width=2)

    # 双线内框
    d.rectangle([28, 28, W - 28, H - 28], outline=LINE, width=1)
    d.rectangle([35, 35, W - 35, H - 35], outline=(206, 184, 126), width=1)

    # 朱印「帝京」
    d.rounded_rectangle([72, 74, 72 + 86, 74 + 86], radius=8, fill=SEAL)
    fs = font(SUN, 38)
    for i, ch in enumerate("帝京"):
        cw = d.textlength(ch, font=fs)
        d.text((72 + 43 - cw / 2, 74 + 14 + i * 32), ch, font=fs, fill=(255, 255, 255), anchor="la")

    # 标题 / 副题
    ft = font(KAI, 62)
    sp_text(d, (188, 84), args.title, ft, INK, gap=6, stroke=1)
    fb = font(HEI, 22)
    sp_text(d, (190, 149), args.subtitle, fb, INK2, gap=4)
    d.line([190, 186, W - 90, 186], fill=LINE, width=1)

    # ---- 对照卡 ----
    cx, cy, cw, ch = 96, 224, W - 192, 214
    d.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=12, fill=(255, 253, 248), outline=LINE, width=1)

    f15 = font(HEI, 15)
    d.text((cx + 34, 256), "明代《帝京景物略》卷一 · 城北内外", font=f15, fill=MUTED, anchor="la")
    f46 = font(KAI, 46)
    d.text((cx + 34, 288), "太學石鼓", font=f46, fill=INK, anchor="la")

    ax, ay = cx + 300, cy + 84
    d.line([ax, ay, ax + 92, ay], fill=GOLD, width=3)
    d.polygon([(ax + 94, ay), (ax + 76, ay - 10), (ax + 76, ay + 10)], fill=GOLD)

    d.text((ax + 130, 256), "今日百度地图地点", font=f15, fill=MUTED, anchor="la")
    f40 = font(HEI, 40)
    d.text((ax + 130, 288), "北京孔庙和国子监博物馆", font=f40, fill=SEAL, anchor="la")
    f14 = font(HEI, 14)
    d.text((ax + 130, 337), "BD-09 116.42027, 39.95288 ｜ 已用百度地点检索核验", font=f14, fill=MUTED, anchor="la")
    f17 = font(HEI, 17)
    d.text((cx + 34, 383), "点开即见：原书记载 · 关联诗词 · 白话今译 · 清人《京城古迹考》的实地核访", font=f17, fill=(58, 49, 38), anchor="la")

    # ---- 数据条 ----
    x, y = 96, 496
    f15b = font(HEI, 15)
    for t in ["30 处明代地点", "519 首关联诗篇", "127 目古籍语料", "8 个步行圈 + 2 / 3 / 5 日行程"]:
        w = d.textlength(t, font=f15b) + 30
        d.rounded_rectangle([x, y - 24, x + w, y + 10], radius=17, fill=PAPER2, outline=LINE, width=1)
        d.text((x + 15, y - 12), t, font=f15b, fill=INK2, anchor="la")
        x += w + 12

    # ---- 主张（自动缩放填满宽度）----
    fclaim, size = fit(d, args.claim, KAI, W - 192, 72, 3, 22)
    sp_text(d, (96, 578 - size * 0.78), args.claim, fclaim, INK, gap=3, stroke=1)

    # ---- 页脚 ----
    d.text((96, 605), "基于百度地图 JSAPI GL / WebAPI ｜ 数据源《帝京景物略》(1635)、《京城古迹考》、《日下舊聞考》",
           font=f14, fill=MUTED, anchor="la")
    ftag = font(HEI, 14)
    tag = "开发者创作大赛 · 创意应用"
    d.text((W - 60 - d.textlength(tag, font=ftag), 605), tag, font=ftag, fill=SEAL, anchor="la")

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    png = out / f"{args.stem}.png"
    jpg = out / f"{args.stem}.jpg"
    img.save(png, "PNG", optimize=True)
    img.convert("RGB").save(jpg, "JPEG", quality=92, optimize=True)

    for p in (png, jpg):
        print(f"{p}  {p.stat().st_size / 1024:.0f} KB")
    print(f"尺寸 {img.size[0]}x{img.size[1]} ｜ 主张字号 {size}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
