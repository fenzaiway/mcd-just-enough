"""把 examples/demo-4adults.txt 渲染成 README 用的终端截图 SVG。

用法：python3 tools/demo_svg.py
产物：docs/demo.svg

中日韩字符在 SVG 里拿不到「等宽 2 格」的保证，直接整行输出会让表格竖线错位。
因此表格行按字符显示宽度逐格定位：每格单独一个 <text>，竖线按累计列宽摆放，
这样无论字体回退到什么 CJK 字体，竖线都对齐。
"""

from __future__ import annotations

import html
import pathlib
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "examples" / "demo-4adults.txt"
OUT = ROOT / "docs" / "demo.svg"

PAD_X = 26
PAD_TOP = 74
LINE_H = 19
FONT_SIZE = 12.5
CELL = FONT_SIZE * 0.6  # 等宽字体的单字符宽度
WIDTH = 900
BOX = "│┌┬┐├┼┤└┴┘─"


def char_width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def columns(line: str) -> list[tuple[int, str]]:
    """按显示列宽返回 [(起始列, 片段)]，供逐格定位。"""
    parts: list[tuple[int, str]] = []
    col = 0
    buffer = ""
    start = 0
    for char in line:
        width = char_width(char)
        if char in BOX:
            if buffer:
                parts.append((start, buffer))
                buffer = ""
            parts.append((col, char))
            col += width
            start = col
            continue
        if not buffer:
            start = col
        buffer += char
        col += width
    if buffer:
        parts.append((start, buffer))
    return parts


def color_for(line: str) -> tuple[str, str]:
    if line.startswith("🍟"):
        return "#f6c344", "600"
    if line.startswith("#"):
        return "#8b949e", "400"
    if "实付" in line and "价格来源" in line:
        return "#7ee787", "600"
    if line.startswith("说明"):
        return "#8b949e", "600"
    if line.strip().startswith("-"):
        return "#a5b4c3", "400"
    if line.startswith("就餐"):
        return "#e6edf3", "600"
    if any(char in line for char in BOX):
        return "#8b949e", "400"
    if line.startswith("[") and "█" in line:
        return "#58a6ff", "400"
    return "#d0d7de", "400"


def main() -> None:
    lines = SRC.read_text("utf-8").splitlines()
    height = PAD_TOP + len(lines) * LINE_H + 26
    body = []
    y = PAD_TOP
    for line in lines:
        fill, weight = color_for(line)
        if any(char in line for char in BOX) and line.strip():
            for col, text in columns(line):
                x = PAD_X + col * CELL
                body.append(
                    f'<text x="{x:.1f}" y="{y}" fill="{fill}" font-weight="{weight}" '
                    f'xml:space="preserve">{html.escape(text)}</text>'
                )
        else:
            body.append(
                f'<text x="{PAD_X}" y="{y}" fill="{fill}" font-weight="{weight}" '
                f'xml:space="preserve">{html.escape(line)}</text>'
            )
        y += LINE_H

    body_block = "\n  ".join(body)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" font-family="'SF Mono', Menlo, Consolas, 'PingFang SC', 'Microsoft YaHei', monospace" font-size="{FONT_SIZE}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0d1117"/>
      <stop offset="1" stop-color="#0b0f14"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="{WIDTH}" height="{height}" rx="12" fill="url(#bg)"/>
  <rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="12" fill="none" stroke="#30363d"/>
  <rect x="0" y="0" width="{WIDTH}" height="40" rx="12" fill="#161b22"/>
  <rect x="0" y="30" width="{WIDTH}" height="10" fill="#161b22"/>
  <circle cx="24" cy="20" r="6" fill="#ff5f57"/>
  <circle cx="44" cy="20" r="6" fill="#febc2e"/>
  <circle cx="64" cy="20" r="6" fill="#28c840"/>
  <text x="{WIDTH / 2}" y="24" fill="#8b949e" font-size="12" text-anchor="middle">justenough demo · 离线示例数据（非真实菜单与价格）</text>
  <line x1="0" y1="40" x2="{WIDTH}" y2="40" stroke="#30363d"/>
  {body_block}
</svg>
'''
    OUT.write_text(svg, "utf-8")
    print(f"wrote {OUT} ({len(lines)} lines, {WIDTH}x{height})")


if __name__ == "__main__":
    main()
