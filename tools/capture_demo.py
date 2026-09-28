# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright"]
# ///
"""用 Playwright 驱动真实页面，逐个状态截 1920x1080 图，供合成「演示视频」。

    uv run tools/capture_demo.py
    uv run tools/capture_demo.py --base http://localhost:8080/ --headless

为什么默认 headful：百度地图 JSAPI GL 走 WebGL，headless 下容易拿不到渲染结果。
窗口会短暂出现在桌面上，截完自动关闭。

产出：
  banner/demo/01..07-*.png        各状态截图（1920x1080）
  banner/demo/manifest.json       供 tools/make_film.py --program demo 使用
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

OUTDIR = Path("banner/demo")
W, H = 1920, 1080


def find_chromium() -> str | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not base.exists():
        return None
    cands = sorted((d for d in base.glob("chromium-*") if d.is_dir()), reverse=True)
    for d in cands:
        for exe in ("chrome-win/chrome.exe", "chrome-win64/chrome.exe"):
            p = d / exe
            if p.exists():
                return str(p)
    return None


def click_text(page, container: str, text: str, timeout: int = 6000) -> bool:
    """点掉 container 下文本等于 text 的那个子元素（按索引点，避免严格模式问题）。"""
    try:
        loc = page.locator(f"{container} > *")
        n = loc.count()
        for i in range(n):
            el = loc.nth(i)
            try:
                t = (el.inner_text(timeout=800) or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if t == text or text in t:
                el.click(timeout=timeout)
                return True
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"   ! click_text({container}, {text}) 失败: {exc}", flush=True)
        return False


def settle(page, ms: int = 1400) -> None:
    page.wait_for_timeout(ms)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080/")
    ap.add_argument("--headless", action="store_true", help="无头模式（WebGL 可能不可用）")
    ap.add_argument("--outdir", default=str(OUTDIR))
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    shots: list[dict] = []

    exe = find_chromium()
    print(f"chromium: {exe or '(playwright 默认)'}", flush=True)

    with sync_playwright() as pw:
        launch_kw = {"headless": args.headless}
        if exe:
            launch_kw["executable_path"] = exe
        launch_kw["args"] = [
            f"--window-size={W},{H}",
            "--force-device-scale-factor=1",
            "--hide-scrollbars",
            "--disable-features=CalculateNativeWinOcclusion",
        ]
        browser = pw.chromium.launch(**launch_kw)
        ctx = browser.new_context(viewport={"width": W, "height": H},
                                  device_scale_factor=1, locale="zh-CN")
        page = ctx.new_page()

        print(f"open {args.base}", flush=True)
        page.goto(args.base, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_function(
                "() => window.DJJWL && ['ready','failed'].includes(DJJWL.mapState)",
                timeout=120000)
        except Exception as exc:  # noqa: BLE001
            print(f"   ! 等地图就绪超时: {exc}", flush=True)
        state = page.evaluate("() => window.DJJWL ? DJJWL.mapState : null")
        print(f"   mapState = {state}", flush=True)
        settle(page, 3000)

        def shot(name: str, left: str, right: str, wait: int = 1600) -> None:
            settle(page, wait)
            p = outdir / f"{name}.png"
            page.screenshot(path=str(p))
            shots.append({"file": p.name, "ming": left, "modern": right})
            print(f"  [shot] {p.name}", flush=True)

        # 1 首页全景
        shot("01", "一图三十处", "《帝京景物略》127 目，落点 30 处古迹")

        # 2 三重筛选
        click_text(page, "#volumeFilter", "三城南内外")
        click_text(page, "#tagFilter", "寺庙")
        shot("02", "三重筛选", "按卷次 · 类型 · 步行圈收窄范围")

        # 3 点开一处
        click_text(page, "#volumeFilter", "三城南内外")  # 取消卷次筛选，恢复全量
        click_text(page, "#placeList", "憫忠寺")
        shot("03", "点开一处", "明清地名 → 今日地名，附坐标来源")

        # 4 原文与诗
        page.evaluate("""() => { const d = document.querySelector('#detail');
            if (d) d.scrollTop = Math.min(d.scrollHeight, 520); }""")
        shot("04", "原文与诗", "原书记载 · 关联诗篇 · 白话今译 · 清人核访")

        # 5 智能排线
        click_text(page, "#placeList", "太學石鼓")
        try:
            page.select_option("#p3Dur", "180")
            page.select_option("#p3Tag", label="寺庙")
            page.select_option("#p3Mode", "walking")
        except Exception as exc:  # noqa: BLE001
            print(f"   ! 设置排线参数失败: {exc}", flush=True)
        page.click("#p3Btn")
        shot("05", "一句话排线", "半日想逛寺庙，智能排线给出 3 个取舍方案", wait=2500)

        # 6 真实里程
        try:
            btns = page.locator("#p3Out button")
            if btns.count():
                btns.first.click()
        except Exception as exc:  # noqa: BLE001
            print(f"   ! 应用方案失败: {exc}", flush=True)
        settle(page, 1200)
        try:
            page.select_option("#routeMode", "mixed")
            page.click("#planBtn")
            page.wait_for_timeout(9000)
        except Exception as exc:  # noqa: BLE001
            print(f"   ! 生成路线失败: {exc}", flush=True)
        shot("06", "真实里程", "百度路线规划算出每段真实里程与耗时", wait=2500)

        # 7 AI 导游
        try:
            page.click("#gxToggle")
            settle(page, 900)
            page.fill("#gxInput", "半天想逛寺庙，怎么走？")
            page.press("#gxInput", "Enter")
            page.wait_for_timeout(5000)
        except Exception as exc:  # noqa: BLE001
            print(f"   ! 导游交互失败: {exc}", flush=True)
        shot("07", "AI 导游", "本地意图解析 + 真实地图工具调用", wait=2500)

        (outdir / "manifest.json").write_text(
            json.dumps(shots, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nmanifest: {outdir / 'manifest.json'}  ({len(shots)} 张)", flush=True)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
