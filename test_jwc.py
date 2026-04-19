#!/usr/bin/env python3
"""End-to-end test: parse JWC_TEMP.TXT, render SVG + PNG, print summary."""

from __future__ import annotations

import json
from pathlib import Path

from parser import parse
from renderer import render_svg, render_png


def main() -> None:
    root = Path(__file__).parent
    src = root / "JWC_TEMP.TXT"
    assert src.exists(), f"missing {src}"

    parsed = parse(src)

    # 1. JSON dump
    json_path = root / "jwc_parsed.json"
    counts: dict[str, int] = {}
    for e in parsed["entities"]:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    parsed["summary"] = {
        "entity_counts": counts,
        "total_entities": len(parsed["entities"]),
        "block_count": len(parsed["blocks"]),
    }
    json_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. SVG
    svg_path = root / "jwc_output.svg"
    _, info = render_svg(parsed, svg_path, width_px=1600)

    # 3. PNG
    png_path = root / "jwc_output.png"
    png_info = render_png(parsed, png_path, width_px=1600)

    # 4. Report
    print("=== JWC_TEMP.TXT parse/render test ===")
    print(f"source:        {src}  ({src.stat().st_size} bytes)")
    print()
    print("-- Header --")
    hdr = parsed["header"]
    print(f"  paper:       {hdr.get('paper')}")
    print(f"  scale:       1:{int(hdr.get('scale') or 0)}")
    rng = hdr.get("range", {})
    print(f"  range:       x=[{rng.get('xmin'):.1f} .. {rng.get('xmax'):.1f}]")
    print(f"               y=[{rng.get('ymin'):.1f} .. {rng.get('ymax'):.1f}]")
    print()
    print("-- Entities --")
    for k, v in sorted(counts.items()):
        print(f"  {k:10s} {v}")
    print(f"  total      {len(parsed['entities'])}")
    print(f"  blocks     {len(parsed['blocks'])}")
    print()
    print("-- Outputs --")
    print(f"  JSON:  {json_path}  ({json_path.stat().st_size} bytes)")
    print(f"  SVG:   {svg_path}   ({svg_path.stat().st_size} bytes, {info['width_px']}x{info['height_px']})")
    print(f"  PNG:   {png_path}   ({png_path.stat().st_size} bytes, {png_info['width_px']}x{png_info['height_px']})")
    print()

    # Sanity checks
    assert counts.get("line", 0) > 0, "expected line entities"
    assert counts.get("text", 0) > 0, "expected text entities"
    assert svg_path.stat().st_size > 1024, "SVG looks empty"
    assert png_path.stat().st_size > 1024, "PNG looks empty"
    print("OK: all sanity checks passed.")


if __name__ == "__main__":
    main()
