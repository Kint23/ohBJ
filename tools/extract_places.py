# /// script
# requires-python = ">=3.10"
# dependencies = ["zhconv"]
# ///
"""帝京景物略 / 京城古迹考 / 日下舊聞考 结构化抽取

产出：
  data/entries.json   帝京景物略 127 目（含正文、关联诗、地名互见）
  data/guben.json     京城古迹考 条目（清代"臣按…今查…"沿革文本）
  data/digest.txt     30 条策展条目的正文摘要（供人工撰写今译）

用法：
  uv run tools/extract_places.py
  uv run tools/extract_places.py --digest 200
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from zhconv import convert as _zh  # type: ignore[import-not-found]

    def to_simp(s: str) -> str:
        return _zh(s, "zh-cn")

except ImportError:  # 无网络/未安装时降级为原文，不影响结构
    def to_simp(s: str) -> str:
        return s
    print("[warn] zhconv 不可用，name_simp == name（未做繁简归一）", file=sys.stderr)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

VOL_RE = re.compile(r"^卷([一二三四五六七八])\s*(.+?)\s*$")
POEM_RE = re.compile(r"^(.{0,18}?)【(.{1,30}?)】\s*$")
PUNCT = set("，。：；、？！（）「」〈〉【】《》…—　")
BLACKLIST = {"帝京景物略", "序", "略例", "京城古迹考", "静海励宗万撰"}


def read(name: str) -> list[str]:
    p = ROOT / name
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


def is_heading(line: str) -> bool:
    """短、无缩进、无标点、无'·' 的独立行 = 目录标题。"""
    t = line.strip()
    if line != t:                 # 有缩进 → 正文
        return False
    if not (2 <= len(t) <= 12):
        return False
    if t in BLACKLIST:
        return False
    if any(ch in PUNCT for ch in t):
        return False
    if "·" in t or "・" in t:     # 作者行 "唐·韓愈"
        return False
    return True


def parse_poem_header(line: str) -> dict | None:
    m = POEM_RE.match(line.strip())
    if not m:
        return None
    prefix, title = m.group(1).strip(), m.group(2).strip()
    dynasty = origin = author = ""
    if prefix:
        parts = [p for p in prefix.split("·") if p]
        if len(parts) >= 2:
            dynasty, author = parts[0], parts[-1]
            origin = parts[1] if len(parts) >= 3 else ""
        else:
            author = parts[0]
    return {"dynasty": dynasty, "origin": origin, "author": author, "title": title, "text": ""}


def parse_djjwl() -> list[dict]:
    lines = read("帝京景物略.txt")
    entries: list[dict] = []
    volume = volume_name = ""
    cur: dict | None = None
    poem: dict | None = None
    buf: list[str] = []

    def flush_text() -> None:
        nonlocal buf
        if cur is not None and buf:
            chunk = "".join(buf).strip()
            if chunk:
                cur["_chunks"].append(chunk)
        buf = []

    def flush_poem() -> None:
        nonlocal poem
        if poem is not None:
            poem["text"] = "\n".join(poem.pop("_lines", [])).strip()
            if cur is not None and poem["text"]:
                cur["poems"].append(poem)
        poem = None

    for idx, raw in enumerate(lines):
        m = VOL_RE.match(raw.strip())
        if m:
            flush_poem(); flush_text()
            volume, volume_name = f"卷{m.group(1)}", m.group(2)
            cur = None
            continue

        ph = parse_poem_header(raw)
        if ph:
            flush_poem(); flush_text()
            ph["_lines"] = []
            poem = ph
            continue

        if is_heading(raw):
            flush_poem(); flush_text()
            if raw.strip() == "略例":
                continue
            cur = {
                "volume": volume,
                "volume_name": volume_name,
                "name": raw.strip(),
                "line": idx + 1,
                "_chunks": [],
                "poems": [],
            }
            entries.append(cur)
            continue

        if not raw.strip():
            continue

        if poem is not None:
            poem["_lines"].append(raw.strip())
        else:
            buf.append(raw)

    flush_poem(); flush_text()

    for i, e in enumerate(entries, start=1):
        text = "\n".join(e.pop("_chunks"))
        e["id"] = f"djjwl-{i:03d}"
        e["text"] = text
        e["text_simp"] = to_simp(text)
        e["name_simp"] = to_simp(e["name"])
        e["scope"] = "京外" if e["volume"] == "卷八" else "京畿"
        e["text_len"] = len(text)

    # 地名互见：本目正文中提到其他目的名称
    names = {(e["name_simp"], e["name"]) for e in entries}
    for e in entries:
        body = e["text_simp"]
        hits = {s for s, _ in names if s != e["name_simp"] and len(s) >= 2 and s in body}
        e["mentions_simp"] = sorted(hits)

    return entries


def parse_jingcheng() -> list[dict]:
    """京城古迹考：条目 + '臣按…今查…' 沿革文本。"""
    lines = read("京城古迹考.txt")
    out: list[dict] = []
    zone = ""
    cur: dict | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if cur is not None:
            cur["text"] = "\n".join(buf).strip()
        buf = []

    for idx, raw in enumerate(lines):
        t = raw.strip()
        if not t:
            continue
        if t in {"东城", "南城", "西城", "北城"}:
            flush(); cur = None; zone = t
            continue
        if is_heading(raw):
            flush()
            cur = {"zone": zone, "name": t, "name_simp": to_simp(t), "line": idx + 1, "text": ""}
            out.append(cur)
            continue
        buf.append(raw)

    flush()
    for c in out:
        c["text_simp"] = to_simp(c["text"])
        c["text_len"] = len(c["text"])
    return [c for c in out if c["text_len"] > 20]


def best_match(name_simp: str, pool: list[dict], key: str) -> dict | None:
    for c in pool:
        if c[key] == name_simp:
            return c
    for c in pool:
        k = c[key]
        if len(name_simp) >= 2 and (name_simp in k or k in name_simp) and abs(len(k) - len(name_simp)) <= 2:
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", type=int, default=0, help="打印策展条目正文前 N 字")
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)

    entries = parse_djjwl()
    guben = parse_jingcheng()
    (DATA / "entries.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (DATA / "guben.json").write_text(
        json.dumps(guben, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    n_poems = sum(len(e["poems"]) for e in entries)
    print(f"帝京景物略：{len(entries)} 目，诗 {n_poems} 首")
    for v in [f"卷{c}" for c in "一二三四五六七八"]:
        sub = [e for e in entries if e["volume"] == v]
        print(f"  {v} {sub[0]['volume_name'] if sub else '':<6} {len(sub):>3} 目")
    print(f"京城古迹考：{len(guben)} 条沿革")

    mp = DATA / "curated" / "mapping.json"
    if mp.exists():
        raw_map = json.loads(mp.read_text(encoding="utf-8"))
        curated = raw_map["places"] if isinstance(raw_map, dict) else raw_map
        hit = miss = 0
        for c in curated:
            c["entry_simp"] = c.get("entry_simp") or to_simp(c["entry"])
            e = best_match(c["entry_simp"], entries, "name_simp")
            g = best_match(c["entry_simp"], guben, "name_simp")
            c["_entry_id"] = e["id"] if e else None
            c["_guben"] = g["name"] if g else None
            hit += bool(e)
            miss += not e
        print(f"策展映射：{len(curated)} 条，命中 {hit}，未命中 {miss}")
        for c in curated:
            if not c["_entry_id"]:
                print(f"  [未命中] {c['entry']}", file=sys.stderr)
        (DATA / "curated" / "mapping.resolved.json").write_text(
            json.dumps(curated, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        if args.digest:
            lines_out = []
            for c in curated:
                e = next((x for x in entries if x["id"] == c["_entry_id"]), None)
                if not e:
                    continue
                body = re.sub(r"\s+", "", e["text_simp"])[: args.digest]
                lines_out.append(
                    f"### {c['entry']} → {c['modern_name']} [{e['volume']}{e['volume_name']}]"
                    f" 诗{len(e['poems'])}首\n{body}\n"
                )
            (DATA / "digest.txt").write_text("\n".join(lines_out), encoding="utf-8")
            print(f"摘要写入 data/digest.txt（每目 {args.digest} 字）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
