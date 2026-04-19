#!/usr/bin/env python3
"""JWC_TEMP.TXT renderer.

Takes parsed JWC output (dict or JSON) and renders an SVG. Also rasterizes
a simple PNG using only the Python stdlib (zlib + struct) — no PIL/cairosvg
dependency, so it runs in minimal environments.

CAD coordinates have +Y up; SVG has +Y down, so we flip Y.
"""

from __future__ import annotations

import json
import math
import struct
import sys
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

# JW_CAD line-color palette (index 1..9)
LINE_COLORS = {
    0: "#000000",
    1: "#000000",  # 黒
    2: "#0000ff",  # 青
    3: "#ff0000",  # 赤
    4: "#ff00ff",  # マゼンタ
    5: "#00a000",  # 緑
    6: "#00c0c0",  # シアン
    7: "#c0c000",  # 黄
    8: "#808080",  # 灰
    9: "#c0c0c0",  # 薄灰
}

# Line-type dash patterns, in SVG user units (scaled later)
# lt1 solid, lt2 dotted, lt3 dashed, lt9 fine dotted
LINE_DASH = {
    1: None,
    2: "8,4",
    3: "16,6",
    4: "16,6,2,6",
    5: "2,4",
    6: "20,4,4,4",
    7: "16,4,2,4,2,4",
    8: "4,4",
    9: "2,4",
}


# ---------------------------------------------------------------------------
# SVG generation
# ---------------------------------------------------------------------------

def render_svg(parsed: dict, svg_path: str | Path, *, width_px: int = 1600) -> tuple[str, dict]:
    rng = parsed["header"].get("range") or {
        "xmin": 0, "ymin": 0, "xmax": 1000, "ymax": 1000,
    }
    xmin, ymin, xmax, ymax = rng["xmin"], rng["ymin"], rng["xmax"], rng["ymax"]
    w_units = xmax - xmin
    h_units = ymax - ymin
    if w_units <= 0 or h_units <= 0:
        raise ValueError(f"invalid drawing range: {rng}")

    aspect = h_units / w_units
    height_px = max(1, int(width_px * aspect))

    # CAD (x, y)  →  SVG (x - xmin, ymax - y)
    def tx(x: float) -> float: return x - xmin
    def ty(y: float) -> float: return ymax - y

    # Scale the dash patterns so they stay readable regardless of drawing size.
    dash_scale = w_units / 1500.0

    def dasharray(lt: int | None) -> str | None:
        if lt is None:
            return None
        pat = LINE_DASH.get(lt)
        if not pat:
            return None
        return ",".join(f"{float(v) * dash_scale:.2f}" for v in pat.split(","))

    # Stroke width in drawing units so it stays proportional.
    default_sw = w_units / 2000.0

    parts: list[str] = []
    parts.append(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {w_units:.3f} {h_units:.3f}" '
        f'width="{width_px}" height="{height_px}" '
        f'style="background:#ffffff">\n'
    )
    parts.append('<g stroke-linecap="round" stroke-linejoin="round" fill="none">\n')

    def stroke_attrs(ent: dict) -> str:
        color = LINE_COLORS.get(ent.get("line_color") or 1, "#000000")
        lt = ent.get("line_type") or 1
        lw = ent.get("line_width") or 0
        sw = default_sw * (1 + (lw or 1) / 10.0)
        attrs = f'stroke="{color}" stroke-width="{sw:.3f}"'
        da = dasharray(lt)
        if da:
            attrs += f' stroke-dasharray="{da}"'
        return attrs

    # Entities
    for ent in parsed.get("entities", []):
        etype = ent["type"]
        if etype == "line":
            x1 = tx(ent["x1"]); y1 = ty(ent["y1"])
            x2 = tx(ent["x2"]); y2 = ty(ent["y2"])
            parts.append(
                f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
                f'{stroke_attrs(ent)}/>\n'
            )
        elif etype == "polyline":
            # Each segment is an independent line; easier to emit as many <line>s
            # than to reconstruct a polyline, because JW polylines are just chained
            # segments with arbitrary endpoints.
            for (x1, y1, x2, y2) in ent.get("segments", []):
                parts.append(
                    f'<line x1="{tx(x1):.3f}" y1="{ty(y1):.3f}" '
                    f'x2="{tx(x2):.3f}" y2="{ty(y2):.3f}" '
                    f'{stroke_attrs(ent)}/>\n'
                )
        elif etype == "circle":
            cx = tx(ent["x"]); cy = ty(ent["y"]); r = ent["r"]
            if "start_angle" in ent and "end_angle" in ent:
                sa = math.radians(ent["start_angle"])
                ea = math.radians(ent["end_angle"])
                # JW CAD angles are measured CCW from +X in CAD space; in SVG we flip Y,
                # so drawing the arc with negated sine gives the correct curve.
                x1 = cx + r * math.cos(sa)
                y1 = cy - r * math.sin(sa)
                x2 = cx + r * math.cos(ea)
                y2 = cy - r * math.sin(ea)
                sweep = ea - sa
                if sweep < 0:
                    sweep += 2 * math.pi
                large = 1 if sweep > math.pi else 0
                parts.append(
                    f'<path d="M {x1:.3f} {y1:.3f} A {r:.3f} {r:.3f} 0 {large} 0 '
                    f'{x2:.3f} {y2:.3f}" {stroke_attrs(ent)}/>\n'
                )
            else:
                parts.append(
                    f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" '
                    f'{stroke_attrs(ent)}/>\n'
                )
        elif etype == "text":
            x = tx(ent["x"]); y = ty(ent["y"])
            text = escape(ent.get("text", ""))
            angle = ent.get("angle", 0.0)
            # Text height: use font.height × scale (drawing units) as font-size.
            font = ent.get("font") or {}
            scale = parsed["header"].get("scale") or 1.0
            fh_mm = font.get("height") or 3.0
            font_size = fh_mm * scale
            # size field represents full text width in drawing units
            size_w = ent.get("size") or (len(ent.get("text", "")) * font_size)
            n_chars = max(1, len(ent.get("text", "")))
            per_char = size_w / n_chars
            # Use the larger of font_size and per_char*1.1 so text doesn't
            # collapse if cn defaults disagree with the ch size field.
            fs = max(font_size, per_char * 1.05)
            color = LINE_COLORS.get(ent.get("text_color") or ent.get("line_color") or 1, "#000000")
            transform = ""
            if angle:
                transform = f' transform="rotate({-angle:.3f} {x:.3f} {y:.3f})"'
            parts.append(
                f'<text x="{x:.3f}" y="{y:.3f}" font-size="{fs:.3f}" '
                f'font-family="MS Gothic, Noto Sans CJK JP, sans-serif" '
                f'fill="{color}" stroke="none" textLength="{size_w:.3f}" '
                f'lengthAdjust="spacingAndGlyphs"{transform}>{text}</text>\n'
            )

    parts.append('</g>\n</svg>\n')
    svg = "".join(parts)
    Path(svg_path).write_text(svg, encoding="utf-8")

    info = {
        "width_px": width_px,
        "height_px": height_px,
        "w_units": w_units,
        "h_units": h_units,
        "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
    }
    return svg, info


# ---------------------------------------------------------------------------
# Pure-Python PNG rasterizer (lines, arcs, text boxes) — no deps
# ---------------------------------------------------------------------------

def _write_png(path: str | Path, pixels: bytearray, w: int, h: int) -> None:
    """Write RGBA bytes (row-major, 4 bytes/pixel) as a PNG file."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
        )

    # Prepend filter byte (0 = None) to each scanline
    row_bytes = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(pixels[y * row_bytes:(y + 1) * row_bytes])
    compressed = zlib.compress(bytes(raw), 9)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    out = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    Path(path).write_bytes(out)


def _hex_to_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


class _Canvas:
    def __init__(self, w: int, h: int, bg=(255, 255, 255)):
        self.w = w; self.h = h
        self.buf = bytearray(w * h * 4)
        for i in range(0, len(self.buf), 4):
            self.buf[i] = bg[0]; self.buf[i + 1] = bg[1]
            self.buf[i + 2] = bg[2]; self.buf[i + 3] = 255

    def _put(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 4
            self.buf[i] = rgb[0]; self.buf[i + 1] = rgb[1]
            self.buf[i + 2] = rgb[2]; self.buf[i + 3] = 255

    def line(self, x0: float, y0: float, x1: float, y1: float,
             rgb: tuple[int, int, int], width: int = 1,
             dash: list[float] | None = None) -> None:
        x0i, y0i, x1i, y1i = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx = abs(x1i - x0i); sx = 1 if x0i < x1i else -1
        dy = -abs(y1i - y0i); sy = 1 if y0i < y1i else -1
        err = dx + dy
        x, y = x0i, y0i
        # dash handling: advance dash cursor per pixel stepped
        dash_cur = 0.0
        dash_idx = 0
        draw = True
        r = max(0, width // 2)
        while True:
            if draw:
                for ox in range(-r, r + 1):
                    for oy in range(-r, r + 1):
                        self._put(x + ox, y + oy, rgb)
            if x == x1i and y == y1i:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy; x += sx
            if e2 <= dx:
                err += dx; y += sy
            if dash:
                dash_cur += 1.0
                if dash_cur >= dash[dash_idx]:
                    dash_cur = 0.0
                    dash_idx = (dash_idx + 1) % len(dash)
                    draw = (dash_idx % 2 == 0)

    def arc(self, cx: float, cy: float, r: float, sa: float, ea: float,
            rgb: tuple[int, int, int], width: int = 1) -> None:
        # Approximate with short line segments
        if ea < sa:
            ea += 2 * math.pi
        steps = max(8, int(r * abs(ea - sa)))
        prev = None
        for i in range(steps + 1):
            t = sa + (ea - sa) * (i / steps)
            x = cx + r * math.cos(t)
            y = cy - r * math.sin(t)  # y-flip already baked in by caller
            if prev is not None:
                self.line(prev[0], prev[1], x, y, rgb, width)
            prev = (x, y)

    def circle(self, cx: float, cy: float, r: float,
               rgb: tuple[int, int, int], width: int = 1) -> None:
        self.arc(cx, cy, r, 0, 2 * math.pi, rgb, width)

    def text_box(self, x: float, y: float, w: float, h: float,
                 rgb: tuple[int, int, int]) -> None:
        # Outline rectangle as a placeholder for text glyphs.
        self.line(x, y, x + w, y, rgb, 1)
        self.line(x + w, y, x + w, y + h, rgb, 1)
        self.line(x + w, y + h, x, y + h, rgb, 1)
        self.line(x, y + h, x, y, rgb, 1)


def render_png(parsed: dict, png_path: str | Path, *,
               width_px: int = 1600, text_as_boxes: bool = True) -> dict:
    rng = parsed["header"]["range"]
    xmin, ymin, xmax, ymax = rng["xmin"], rng["ymin"], rng["xmax"], rng["ymax"]
    w_units = xmax - xmin
    h_units = ymax - ymin
    scale = width_px / w_units
    height_px = max(1, int(h_units * scale))

    def tx(x: float) -> float: return (x - xmin) * scale
    def ty(y: float) -> float: return (ymax - y) * scale

    canvas = _Canvas(width_px, height_px)

    def dash_for(lt: int | None) -> list[float] | None:
        if not lt or lt == 1:
            return None
        pat = LINE_DASH.get(lt)
        if not pat:
            return None
        dash_px_scale = scale * (w_units / 1500.0)
        return [max(1.0, float(v) * dash_px_scale) for v in pat.split(",")]

    for ent in parsed.get("entities", []):
        color = _hex_to_rgb(LINE_COLORS.get(ent.get("line_color") or 1, "#000000"))
        lt = ent.get("line_type") or 1
        lw_unit = max(1, int((ent.get("line_width") or 1) / 4))
        dash = dash_for(lt)
        if ent["type"] == "line":
            canvas.line(tx(ent["x1"]), ty(ent["y1"]),
                        tx(ent["x2"]), ty(ent["y2"]),
                        color, lw_unit, dash)
        elif ent["type"] == "polyline":
            for (x1, y1, x2, y2) in ent.get("segments", []):
                canvas.line(tx(x1), ty(y1), tx(x2), ty(y2), color, lw_unit, dash)
        elif ent["type"] == "circle":
            cx, cy, r = tx(ent["x"]), ty(ent["y"]), ent["r"] * scale
            if "start_angle" in ent and "end_angle" in ent:
                canvas.arc(cx, cy, r,
                           math.radians(ent["start_angle"]),
                           math.radians(ent["end_angle"]),
                           color, lw_unit)
            else:
                canvas.circle(cx, cy, r, color, lw_unit)
        elif ent["type"] == "text" and text_as_boxes:
            font = ent.get("font") or {}
            fh_mm = font.get("height") or 3.0
            draw_scale = parsed["header"].get("scale") or 1.0
            h_units_text = fh_mm * draw_scale
            w_units_text = ent.get("size") or h_units_text * max(1, len(ent.get("text", "")))
            x = tx(ent["x"])
            y = ty(ent["y"]) - h_units_text * scale
            canvas.text_box(x, y, w_units_text * scale, h_units_text * scale,
                            _hex_to_rgb(LINE_COLORS.get(ent.get("text_color") or 1, "#000000")))

    _write_png(png_path, canvas.buf, width_px, height_px)
    return {"width_px": width_px, "height_px": height_px}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else "jwc_parsed.json"
    svg_out = sys.argv[2] if len(sys.argv) > 2 else "jwc_output.svg"
    png_out = sys.argv[3] if len(sys.argv) > 3 else "jwc_output.png"

    parsed = json.loads(Path(src).read_text(encoding="utf-8"))
    _, info = render_svg(parsed, svg_out)
    png_info = render_png(parsed, png_out)
    print(f"SVG: {svg_out} ({info['width_px']}x{info['height_px']})")
    print(f"PNG: {png_out} ({png_info['width_px']}x{png_info['height_px']})")


if __name__ == "__main__":
    main()
