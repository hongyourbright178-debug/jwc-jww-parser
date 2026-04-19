#!/usr/bin/env python3
"""JWC_TEMP.TXT parser.

Reads a JW_CAD external-conversion temp file (Shift_JIS) and emits a
structured dict / JSON with header metadata, layer state, and entities
(lines, circles/arcs, text, polylines, blocks).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _tokenize(line: str) -> list[str]:
    """Split a line into tokens, preserving a trailing quoted text payload.

    JW_CAD text entities look like:  ch X Y SIZE ANGLE "some text
    The quote starts the text body which may contain spaces and runs
    to end of line. Font name lines look like: cn"$<...> (no space).
    """
    stripped = line.rstrip("\r\n")
    if not stripped:
        return []
    # Handle the cn" form (no space between prefix and quote)
    if stripped.startswith("cn\""):
        return ["cn", stripped[3:]]
    q = stripped.find('"')
    if q == -1:
        return stripped.split()
    head = stripped[:q].split()
    tail = stripped[q + 1:]
    return head + [tail]


def parse(path: str | Path) -> dict:
    data = Path(path).read_bytes().decode("shift_jis", errors="replace")

    result = {
        "header": {},
        "entities": [],
        "blocks": [],
    }
    # Track the active drawing state; each entity gets a snapshot.
    state = {
        "layer_group": None,
        "layer": None,
        "line_color": None,
        "line_type": None,
        "line_width": None,
        "text_color": None,
        "font_name": None,
        "font": None,  # cn0 w h spacing count
    }

    in_polyline = False
    polyline_segments: list[list[float]] = []
    in_block = False
    current_block: dict | None = None

    for raw in data.splitlines():
        tokens = _tokenize(raw)
        if not tokens:
            continue
        head = tokens[0]

        # --- Header ---
        if head == "hq":
            result["header"]["hq"] = True
            continue
        if head == "hk":
            result["header"]["hk"] = int(tokens[1]) if len(tokens) > 1 else 0
            continue
        if head == "hs":
            result["header"]["scales"] = [float(x) for x in tokens[1:]]
            if tokens[1:]:
                result["header"]["scale"] = float(tokens[1])
            continue
        if head == "hzs":
            # paper width, height (mm)
            result["header"]["paper"] = {
                "width": float(tokens[1]),
                "height": float(tokens[2]),
            }
            continue
        if head == "hcw":
            result["header"]["char_widths"] = [float(x) for x in tokens[1:]]
            continue
        if head == "hch":
            result["header"]["char_heights"] = [float(x) for x in tokens[1:]]
            continue
        if head == "hcd":
            result["header"]["char_spacing"] = [float(x) for x in tokens[1:]]
            continue
        if head == "hcc":
            result["header"]["char_colors"] = [int(x) for x in tokens[1:]]
            continue
        if head == "hn":
            # drawing range xmin ymin xmax ymax
            vals = [float(x) for x in tokens[1:5]]
            result["header"]["range"] = {
                "xmin": vals[0], "ymin": vals[1],
                "xmax": vals[2], "ymax": vals[3],
            }
            continue

        # --- State changes (lg / ly / lc / lt / lw / cc) ---
        # lgX, lyX: hex char 0-f
        if len(head) >= 3 and head.startswith("lg"):
            state["layer_group"] = head[2:]
            continue
        if len(head) >= 3 and head.startswith("ly"):
            state["layer"] = head[2:]
            continue
        if len(head) >= 3 and head.startswith("lc") and head[2:].isdigit():
            state["line_color"] = int(head[2:])
            continue
        if len(head) >= 3 and head.startswith("lt") and head[2:].isdigit():
            state["line_type"] = int(head[2:])
            continue
        if len(head) >= 3 and head.startswith("lw") and head[2:].isdigit():
            state["line_width"] = int(head[2:])
            continue
        if len(head) >= 3 and head.startswith("cc") and head[2:].isdigit():
            state["text_color"] = int(head[2:])
            continue

        # --- Font control ---
        if head == "cn":
            # either cn0 w h spacing count   or   cn <font-name-payload>
            rest = tokens[1:]
            if rest and rest[0].startswith("$<"):
                # font name: $<ＭＳ ゴシック>  -> strip $< and trailing >
                name = rest[0]
                if name.startswith("$<"):
                    name = name[2:]
                if name.endswith(">"):
                    name = name[:-1]
                state["font_name"] = name
            else:
                # cn0 w h spacing count   (the leading "0" is glued to cn as "cn0")
                pass
            continue
        if head.startswith("cn") and head[2:].isdigit():
            # cn0 w h spacing count
            nums = [float(x) for x in tokens[1:]]
            if len(nums) >= 4:
                state["font"] = {
                    "mode": int(head[2:]),
                    "width": nums[0],
                    "height": nums[1],
                    "spacing": nums[2],
                    "count": int(nums[3]),
                }
            continue

        # --- Block begin/end ---
        if head == "BL":
            in_block = True
            current_block = {
                "name": tokens[1] if len(tokens) > 1 else "",
                "entities": [],
            }
            continue
        if head == "BE":
            if current_block is not None:
                result["blocks"].append(current_block)
            current_block = None
            in_block = False
            continue

        # --- Polyline ---
        if head == "pl":
            in_polyline = True
            polyline_segments = []
            continue
        if head == "#":
            if in_polyline:
                result["entities"].append({
                    "type": "polyline",
                    "segments": polyline_segments,
                    **_state_snapshot(state),
                })
                polyline_segments = []
                in_polyline = False
            continue

        # --- z2 prefix: next-line has a line segment (just skip, handled by numeric line) ---
        if head == "z2":
            # z2 by itself is a modifier marking the next line; the line payload
            # is almost always on the SAME token list when it appears alone.
            # In this file, z2 is on its own line and the 4 numbers are on the
            # following line — so we just consume and continue.
            continue

        # --- Circle / arc ---
        if head == "ci":
            nums = [float(x) for x in tokens[1:]]
            ent = {"type": "circle", "x": nums[0], "y": nums[1], "r": nums[2]}
            if len(nums) >= 5:
                ent["start_angle"] = nums[3]
                ent["end_angle"] = nums[4]
            if len(nums) >= 6:
                ent["direction"] = nums[5]
            if len(nums) >= 7:
                ent["flatness"] = nums[6]
            if len(nums) >= 8:
                ent["tilt"] = nums[7]
            ent.update(_state_snapshot(state))
            _emit(result, current_block, in_block, ent)
            continue

        # --- Text ---
        if head == "ch" or head == "cv":
            # ch x y size angle "text
            try:
                x = float(tokens[1]); y = float(tokens[2])
                size = float(tokens[3]); angle = float(tokens[4])
            except (IndexError, ValueError):
                continue
            text = tokens[5] if len(tokens) > 5 else ""
            ent = {
                "type": "text",
                "x": x, "y": y,
                "size": size, "angle": angle,
                "text": text,
                "vertical": head == "cv",
                **_state_snapshot(state),
            }
            _emit(result, current_block, in_block, ent)
            continue

        # --- Bare line segment: 4 numbers ---
        if len(tokens) == 4:
            try:
                x1, y1, x2, y2 = (float(t) for t in tokens)
            except ValueError:
                continue
            if in_polyline:
                polyline_segments.append([x1, y1, x2, y2])
            else:
                ent = {
                    "type": "line",
                    "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    **_state_snapshot(state),
                }
                _emit(result, current_block, in_block, ent)
            continue

        # Unknown/ignored line — keep as raw for debugging visibility
        # (no-op; silently skip to avoid noise)

    return result


def _state_snapshot(state: dict) -> dict:
    return {
        "layer_group": state["layer_group"],
        "layer": state["layer"],
        "line_color": state["line_color"],
        "line_type": state["line_type"],
        "line_width": state["line_width"],
        "text_color": state["text_color"],
        "font_name": state["font_name"],
        "font": dict(state["font"]) if state["font"] else None,
    }


def _emit(result: dict, current_block, in_block: bool, ent: dict) -> None:
    if in_block and current_block is not None:
        current_block["entities"].append(ent)
    else:
        result["entities"].append(ent)


# ---------------------------------------------------------------------------
# Multi-file merge
# ---------------------------------------------------------------------------

_COORD_KEYS_LINE = ("x1", "y1", "x2", "y2")
_COORD_KEYS_POINT = ("x", "y")


def _scale_entities(entities: list[dict], mul: float) -> None:
    """Multiply every geometric coordinate in-place by mul."""
    if mul == 1.0:
        return
    for e in entities:
        t = e.get("type")
        if t == "line":
            for k in _COORD_KEYS_LINE:
                e[k] *= mul
        elif t == "polyline":
            segs = e.get("segments") or []
            for s in segs:
                for i in range(len(s)):
                    s[i] *= mul
        elif t == "circle":
            e["x"] *= mul; e["y"] *= mul; e["r"] *= mul
        elif t == "text":
            e["x"] *= mul; e["y"] *= mul
            if "size" in e:
                e["size"] *= mul


def _dominant_layer_group(entities: list[dict]) -> str | None:
    """Return the most common layer_group tag across entities (or None)."""
    counts: dict[str, int] = {}
    for e in entities:
        lg = e.get("layer_group")
        if lg is None:
            continue
        counts[lg] = counts.get(lg, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def merge(parsed_list: list[dict], *, base_scale: float | None = None,
          dedupe: bool = True) -> dict:
    """Merge multiple parsed JWC dicts into one.

    - Normalizes per-layer-group scale differences so all entities share
      the chosen base_scale's coordinate space (default: the first file's
      dominant layer group scale).
    - Skips any input whose range+entity-count signature matches one already
      ingested (handles accidental duplicate exports).
    - Unions the hn range.
    """
    if not parsed_list:
        raise ValueError("parsed_list is empty")

    header = {k: v for k, v in parsed_list[0]["header"].items() if k != "range"}
    hs = header.get("scales") or [50.0] * 16
    if base_scale is None:
        base_scale = float(header.get("scale") or hs[0] or 50.0)

    combined = {
        "header": header,
        "entities": [],
        "blocks": [],
        "sources": [],
    }
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    seen: set[tuple[int, float, float, float, float]] = set()

    for idx, p in enumerate(parsed_list):
        rng = p["header"].get("range") or {}
        sig = (
            len(p["entities"]),
            round(rng.get("xmin", 0), 3),
            round(rng.get("ymin", 0), 3),
            round(rng.get("xmax", 0), 3),
            round(rng.get("ymax", 0), 3),
        )
        if dedupe and sig in seen:
            combined["sources"].append({"index": idx, "skipped": "duplicate"})
            continue
        seen.add(sig)

        lg = _dominant_layer_group(p["entities"])
        mul = 1.0
        if lg is not None:
            try:
                lg_idx = int(lg, 16)
            except ValueError:
                lg_idx = -1
            if 0 <= lg_idx < len(hs):
                group_scale = float(hs[lg_idx]) or 1.0
                mul = base_scale / group_scale
        if mul != 1.0:
            _scale_entities(p["entities"], mul)
            for b in p["blocks"]:
                _scale_entities(b.get("entities", []), mul)

        r_xmin = rng.get("xmin", 0) * mul
        r_ymin = rng.get("ymin", 0) * mul
        r_xmax = rng.get("xmax", 0) * mul
        r_ymax = rng.get("ymax", 0) * mul
        xmin = min(xmin, r_xmin); ymin = min(ymin, r_ymin)
        xmax = max(xmax, r_xmax); ymax = max(ymax, r_ymax)

        combined["entities"].extend(p["entities"])
        combined["blocks"].extend(p["blocks"])
        combined["sources"].append({
            "index": idx,
            "layer_group": lg,
            "scale_mul": mul,
            "entity_count": len(p["entities"]),
            "range": {"xmin": r_xmin, "ymin": r_ymin, "xmax": r_xmax, "ymax": r_ymax},
        })

    combined["header"]["range"] = {
        "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
    }
    combined["header"]["scale"] = base_scale
    return combined


def parse_many(paths: list[str | Path]) -> list[dict]:
    return [parse(p) for p in paths]


def main() -> None:
    # Two modes:
    #   python parser.py [IN.TXT] [OUT.json]           — single file
    #   python parser.py --merge OUT.json IN1 IN2 ...  — multi-file merge
    argv = sys.argv[1:]
    if argv and argv[0] in ("-m", "--merge"):
        if len(argv) < 3:
            print("usage: parser.py --merge OUT.json IN1.TXT IN2.TXT ...", file=sys.stderr)
            sys.exit(2)
        out = argv[1]
        inputs = argv[2:]
        merged = merge(parse_many(inputs))
        counts: dict[str, int] = {}
        for e in merged["entities"]:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        merged["summary"] = {
            "entity_counts": counts,
            "total_entities": len(merged["entities"]),
            "block_count": len(merged["blocks"]),
            "input_files": inputs,
        }
        Path(out).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Merged {len(inputs)} files -> {out}")
        print(f"  base scale:  1:{int(merged['header'].get('scale') or 0)}")
        print(f"  union range: {merged['header']['range']}")
        print(f"  entities:    {counts} (total {len(merged['entities'])})")
        for s in merged["sources"]:
            if "skipped" in s:
                print(f"  [skipped #{s['index']} — duplicate]")
            else:
                print(f"  #{s['index']} lg{s['layer_group']} ×{s['scale_mul']:.4g} "
                      f"({s['entity_count']} ents)")
        return

    src = argv[0] if argv else "JWC_TEMP.TXT"
    out = argv[1] if len(argv) > 1 else "jwc_parsed.json"
    result = parse(src)

    # Summary counts
    counts: dict[str, int] = {}
    for e in result["entities"]:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    result["summary"] = {
        "entity_counts": counts,
        "total_entities": len(result["entities"]),
        "block_count": len(result["blocks"]),
    }

    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Parsed {src} -> {out}")
    print(f"  paper: {result['header'].get('paper')}")
    print(f"  scale: {result['header'].get('scale')}")
    print(f"  range: {result['header'].get('range')}")
    print(f"  entities: {counts} (total {len(result['entities'])})")
    print(f"  blocks: {len(result['blocks'])}")


if __name__ == "__main__":
    main()
