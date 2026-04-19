#!/usr/bin/env python3
"""JWW binary file parser.

Reads a JW_CAD .jww file directly (no JW_CAD installation needed) and emits
the same JSON shape produced by parser.py, so renderer.py can be run against
the output unchanged.

Based on the LibreCAD jwwlib C++ source (jwwdoc.cpp / jwwdoc.h) and the
public format spec at jwcad.net/jwdatafmt.txt.
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path


# A0..A4 paper short/long sides (mm) — JWW m_nZumen index 0..4
_PAPER_MM = {
    0: (1189.0, 841.0),   # A0
    1: (841.0, 594.0),    # A1
    2: (594.0, 420.0),    # A2
    3: (420.0, 297.0),    # A3
    4: (297.0, 210.0),    # A4
    8: (2.0 * 1189.0, 2.0 * 841.0),   # 2A
    9: (3.0 * 1189.0, 3.0 * 841.0),   # 3A
    10: (4.0 * 1189.0, 4.0 * 841.0),  # 4A
    11: (5.0 * 1189.0, 5.0 * 841.0),  # 5A
    12: (10000.0, 10000.0),  # 10m square
    13: (50000.0, 50000.0),  # 50m square
    14: (100000.0, 100000.0),  # 100m square
}


class _R:
    """Little-endian binary stream reader."""

    def __init__(self, data: bytes):
        self.b = data
        self.i = 0
        self.n = len(data)

    def eof(self) -> bool:
        return self.i >= self.n

    def read(self, k: int) -> bytes:
        if self.i + k > self.n:
            raise EOFError(f"read past EOF at {self.i} (+{k}), size={self.n}")
        out = self.b[self.i:self.i + k]
        self.i += k
        return out

    def peek_byte(self) -> int:
        return self.b[self.i]

    def u8(self) -> int:
        v = self.b[self.i]; self.i += 1; return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.b, self.i)[0]; self.i += 2; return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.b, self.i)[0]; self.i += 4; return v

    def f64(self) -> float:
        v = struct.unpack_from("<d", self.b, self.i)[0]; self.i += 8; return v

    def mfc_string(self) -> str:
        """MFC CArchive length-prefixed string.

        Handles the full encoding family used by MFC (also by JWW v7+):
            BYTE b
            if b < 0xFF:         length = b          (ASCII/Shift_JIS)
            else:
                WORD w
                if w == 0xFFFE:  Unicode marker     (wide-char payload)
                    BYTE b2
                    if b2 < 0xFF:        length = b2
                    else:
                        WORD w2
                        if w2 != 0xFFFF: length = w2
                        else:            DWORD dw   → length = dw
                    payload = ReadWideChars(length)
                elif w == 0xFFFF:                   (long ASCII)
                    DWORD dw                        → length = dw
                    payload = ReadBytes(length)
                else:
                    length = w
                    payload = ReadBytes(length)
        """
        bt = self.u8()
        if bt == 0:
            return ""
        if bt != 0xFF:
            return self.read(bt).decode("shift_jis", errors="replace")

        w = self.u16()
        if w == 0xFFFE:
            # Unicode payload, re-read length
            bt2 = self.u8()
            if bt2 == 0:
                return ""
            if bt2 != 0xFF:
                length_chars = bt2
            else:
                w2 = self.u16()
                if w2 != 0xFFFF:
                    length_chars = w2
                else:
                    length_chars = self.u32()
            raw = self.read(length_chars * 2)
            return raw.decode("utf-16-le", errors="replace")
        if w == 0xFFFF:
            length = self.u32()
            return self.read(length).decode("shift_jis", errors="replace")
        return self.read(w).decode("shift_jis", errors="replace")


# ---------------------------------------------------------------------------
# Document header (m_* variables) — we don't use most of them, but we must
# consume them in exact order to reach the entity stream.
# ---------------------------------------------------------------------------

def _read_header(r: _R) -> dict:
    head = r.read(8).decode("ascii", errors="replace")
    if head != "JwwData.":
        raise ValueError(f"not a JWW file: header={head!r}")
    version = r.u32()
    if not (version == 230 or version >= 300):
        raise ValueError(f"unsupported JWW version {version}")

    memo = r.mfc_string()
    m_nZumen = r.u32()
    m_nWriteGLay = r.u32()

    glay = []
    for _ in range(16):
        g = {
            "m_anGLay": r.u32(),
            "m_anWriteLay": r.u32(),
            "m_adScale": r.f64(),
            "m_anGLayProtect": r.u32(),
            "layers": [],
        }
        for _ in range(16):
            g["layers"].append({
                "m_aanLay": r.u32(),
                "m_aanLayProtect": r.u32(),
            })
        glay.append(g)

    # Dummy[14]
    for _ in range(14):
        r.u32()

    sunpou = [r.u32() for _ in range(5)]
    _ = r.u32()          # Dummy1
    max_draw_wid = r.u32()
    prt_genten_x = r.f64()
    prt_genten_y = r.f64()
    prt_bairitsu = r.f64()
    prt_90kaiten = r.u32()
    memori_mode = r.u32()
    memori_hyouji_min = r.f64()
    memori_x = r.f64()
    memori_y = r.f64()
    memori_kijun_x = r.f64()
    memori_kijun_y = r.f64()

    layer_names = [[r.mfc_string() for _ in range(16)] for _ in range(16)]
    glayer_names = [r.mfc_string() for _ in range(16)]

    kage_level = r.f64()
    kage_ido = r.f64()
    n_kage9_15 = r.u32()
    kabe_kage_level = r.f64()

    if version >= 300:
        tenkuu_level = r.f64()
        tenkuu_enko_r = r.f64()

    mm_tani_3d = r.u32()
    bairitsu = r.f64()
    genten_x = r.f64()
    genten_y = r.f64()
    hanni_bairitsu = r.f64()
    hanni_genten_x = r.f64()
    hanni_genten_y = r.f64()

    # Zoom/mark jump
    if version >= 300:
        for _ in range(8):
            r.f64()  # m_dZoomJumpBairitsu
            r.f64()  # x
            r.f64()  # y
            r.u32()  # m_nZoomJumpGLay
    else:
        for _ in range(4):
            r.f64(); r.f64(); r.f64()

    # Dummy block (v3+)
    if version >= 300:
        r.f64(); r.f64(); r.f64()
        r.u32()
        r.f64(); r.f64()
        moji_bg = r.f64()
        n_moji_bg = r.u32()

    # Fukusen spacing × 10
    for _ in range(10):
        r.f64()
    r.f64()  # m_dRyoygawaFukusenTomeDe

    # Per-color display + width (10 entries)
    pens = []
    for _ in range(10):
        pens.append({"color": r.u32(), "width": r.u32()})

    # Per-color print (10 entries)
    prt_pens = []
    for _ in range(10):
        prt_pens.append({
            "color": r.u32(),
            "width": r.u32(),
            "point_radius": r.f64(),
        })

    # Line types 2..9 (8 entries) — 4 DWORDs each
    for _ in range(8):
        r.u32(); r.u32(); r.u32(); r.u32()
    # Random line 11..15 (5 entries) — 5 DWORDs each
    for _ in range(5):
        r.u32(); r.u32(); r.u32(); r.u32(); r.u32()
    # Double-length line 16..19 (4 entries) — 4 DWORDs each
    for _ in range(4):
        r.u32(); r.u32(); r.u32(); r.u32()

    # Various single-DWORD flags (14 of them per source order)
    for _ in range(14):
        r.u32()

    # 2.5D view parameters
    # m_dEye_H_Ichi_1..3 (3 DWORDs in source, interesting but we parse as-is)
    # Actually in source these are DWORDs despite names suggesting angles.
    # Already consumed in the loop above? Let me recount from source:
    # The 14 single-DWORDs cover:
    #   m_nDrawGamenTen, m_nDrawPrtTen, m_nBitMapFirstDraw, m_nGyakuDraw,
    #   m_nGyakuSearch, m_nColorPrint, m_nLayJunPrint, m_nColJunPrint,
    #   m_nPrtRenzoku, m_nPrtKyoutuuGray, m_nPrtDispOnlyNonDraw,
    #   m_lnDrawTime, nEyeInit, m_dEye_H_Ichi_1
    # Then m_dEye_H_Ichi_2, m_dEye_H_Ichi_3 (DWORDs) — 2 more
    r.u32(); r.u32()

    r.f64()  # m_dEye_Z_Ichi_1
    r.f64()  # m_dEye_Y_Ichi_1
    r.f64()  # m_dEye_Z_Ichi_2
    r.f64()  # m_dEye_Y_Ichi_2
    r.f64()  # m_dEye_V_Ichi_3

    r.f64()  # m_dSenNagasaSunpou
    r.f64()  # m_dBoxSunpouX
    r.f64()  # m_dBoxSunpouY
    r.f64()  # m_dEnHankeySunpou

    r.u32()  # m_nSolidNinniColor
    r.u32()  # m_SolidColor

    if version >= 420:
        # SXF color extension: 257 color slots
        for _ in range(257):
            r.u32(); r.u32()
        for _ in range(257):
            r.mfc_string()        # m_astrUDColorName[n]
            r.u32(); r.u32(); r.f64()

        # SXF line-type extension: 33 slots + 33 parameter slots
        for _ in range(33):
            r.u32(); r.u32(); r.u32(); r.u32()
        for _ in range(33):
            r.mfc_string()        # m_astrUDLTypeName[n]
            r.u32()               # m_anUDLTypeSegment[n]
            for _ in range(10):
                r.f64()           # m_aadUDLTypePitch[n][1..10]

    # Text-kind table: 10 entries, 3 doubles + 1 DWORD
    moji_table = []
    for _ in range(10):
        moji_table.append({
            "width": r.f64(),
            "height": r.f64(),
            "spacing": r.f64(),
            "color": r.u32(),
        })

    # Current-write text settings
    moji_size_x = r.f64()
    moji_size_y = r.f64()
    moji_kankaku = r.f64()
    moji_color = r.u32()
    moji_shu = r.u32()
    moji_seiri_gyou = r.f64()
    moji_seiri_suu = r.f64()
    moji_kijun_on = r.u32()
    r.f64(); r.f64(); r.f64()  # zure X[0..2]
    r.f64(); r.f64(); r.f64()  # zure Y[0..2]

    return {
        "version": version,
        "memo": memo,
        "m_nZumen": m_nZumen,
        "glay": glay,
        "scales": [g["m_adScale"] for g in glay],
        "glayer_names": glayer_names,
        "layer_names": layer_names,
        "moji_table": moji_table,
        "active_moji": {
            "width": moji_size_x,
            "height": moji_size_y,
            "spacing": moji_kankaku,
            "color": moji_color,
            "kind": moji_shu,
        },
        "prt": {
            "genten_x": prt_genten_x, "genten_y": prt_genten_y,
            "bairitsu": prt_bairitsu,
        },
    }


# ---------------------------------------------------------------------------
# Entity deserialization
# ---------------------------------------------------------------------------

def _read_base(r: _R, version: int) -> dict:
    """CData base — common fields for every entity."""
    m_lGroup = r.u32()
    m_nPenStyle = r.u8()
    m_nPenColor = r.u16()
    m_nPenWidth = r.u16() if version >= 351 else 0
    m_nLayer = r.u16()
    m_nGLayer = r.u16()
    m_sFlg = r.u16()
    return {
        "group": m_lGroup,
        "pen_style": m_nPenStyle,
        "pen_color": m_nPenColor,
        "pen_width": m_nPenWidth,
        "layer": m_nLayer,
        "glayer": m_nGLayer,
        "flags": m_sFlg,
    }


def _read_sen(r: _R, version: int) -> dict:
    base = _read_base(r, version)
    x1 = r.f64(); y1 = r.f64(); x2 = r.f64(); y2 = r.f64()
    return {"_base": base, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _read_enko(r: _R, version: int) -> dict:
    base = _read_base(r, version)
    cx = r.f64(); cy = r.f64()
    radius = r.f64()
    start_rad = r.f64()
    arc_rad = r.f64()
    tilt_rad = r.f64()
    henpei = r.f64()
    zen_en = r.u32()
    return {
        "_base": base,
        "cx": cx, "cy": cy, "r": radius,
        "start_rad": start_rad, "arc_rad": arc_rad,
        "tilt_rad": tilt_rad, "henpei": henpei,
        "full": bool(zen_en),
    }


def _read_ten(r: _R, version: int) -> dict:
    base = _read_base(r, version)
    x = r.f64(); y = r.f64()
    kariten = r.u32()
    code = rot = scale = 0
    if base["pen_style"] == 100:
        code = r.u32()
        rot = r.f64()
        scale = r.f64()
    return {
        "_base": base,
        "x": x, "y": y, "kariten": kariten,
        "code": code, "rot": rot, "scale": scale,
    }


def _read_moji(r: _R, version: int) -> dict:
    base = _read_base(r, version)
    x1 = r.f64(); y1 = r.f64(); x2 = r.f64(); y2 = r.f64()
    moji_shu = r.u32()
    size_x = r.f64(); size_y = r.f64()
    kankaku = r.f64()
    kakudo = r.f64()
    font_name = r.mfc_string()
    text = r.mfc_string()
    return {
        "_base": base,
        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "moji_shu": moji_shu,
        "size_x": size_x, "size_y": size_y,
        "kankaku": kankaku, "angle": kakudo,
        "font_name": font_name, "text": text,
    }


def _read_solid(r: _R, version: int) -> dict:
    base = _read_base(r, version)
    p1x = r.f64(); p1y = r.f64()
    p4x = r.f64(); p4y = r.f64()
    p2x = r.f64(); p2y = r.f64()
    p3x = r.f64(); p3y = r.f64()
    rgb = r.u32() if base["pen_color"] == 10 else 0
    return {
        "_base": base,
        "p1": (p1x, p1y), "p4": (p4x, p4y),
        "p2": (p2x, p2y), "p3": (p3x, p3y),
        "rgb": rgb,
    }


def _read_block(r: _R, version: int) -> dict:
    base = _read_base(r, version)
    x = r.f64(); y = r.f64()
    sx = r.f64(); sy = r.f64()
    rot = r.f64()
    num = r.u32()
    return {
        "_base": base,
        "x": x, "y": y, "scale_x": sx, "scale_y": sy,
        "rot": rot, "number": num,
    }


def _read_sunpou(r: _R, version: int) -> dict:
    base = _read_base(r, version)
    sen = _read_sen(r, version)
    moji = _read_moji(r, version)
    extra = {}
    if version >= 420:
        extra["sxf_mode"] = r.u16()
        extra["sen_ho1"] = _read_sen(r, version)
        extra["sen_ho2"] = _read_sen(r, version)
        extra["ten1"] = _read_ten(r, version)
        extra["ten2"] = _read_ten(r, version)
        extra["ten_ho1"] = _read_ten(r, version)
        extra["ten_ho2"] = _read_ten(r, version)
    return {"_base": base, "sen": sen, "moji": moji, **extra}


def _read_list(r: _R, version: int) -> dict:
    base = _read_base(r, version)
    num = r.u32()
    reffered = r.u32()
    time_ = r.u32()
    name = r.mfc_string()
    return {"_base": base, "number": num, "name": name,
            "reffered": reffered, "time": time_}


_DISPATCH = {
    "CDataSen": _read_sen,
    "CDataEnko": _read_enko,
    "CDataTen": _read_ten,
    "CDataMoji": _read_moji,
    "CDataSolid": _read_solid,
    "CDataBlock": _read_block,
    "CDataSunpou": _read_sunpou,
    "CDataList": _read_list,
}


# ---------------------------------------------------------------------------
# Entity-stream walker — mirrors JWWDocument::Read() in jwwdoc.cpp
# ---------------------------------------------------------------------------

def _read_entity_stream(r: _R, version: int) -> dict[str, list[dict]]:
    """Walk the class-tagged entity stream until EOF.

    Each class name is introduced once with marker 0xFFFF and stored at
    auto-incrementing index i. Subsequent entities reuse the class by
    reference (wd & 0x8000 → index = wd & 0x7FFF, or via 0xFF7F/0x7FFF
    forms that follow with a DWORD index).
    """
    out: dict[str, list[dict]] = {k: [] for k in _DISPATCH}
    class_table: dict[int, str] = {}

    # Initial marker (one-time)
    if r.eof():
        return out
    wd = r.u16()
    if wd == 0xFFFF:
        # followed by a DWORD (discarded in jwwlib)
        r.u32()

    i = 1
    while not r.eof():
        try:
            wd = r.u16()
        except EOFError:
            break
        if wd == 0x0000:
            continue

        if wd == 0xFFFF:
            # new class definition
            try:
                _objCode = r.u16()
                nlen = r.u16()
                name = r.read(nlen).decode("ascii", errors="replace")
            except EOFError:
                break
            class_table[i] = name
            j = i
            i += 1
        elif wd == 0xFF7F:
            dw = r.u32()
            j = dw & 0x7FFFFFFF
        elif wd == 0x7FFF:
            dw = r.u32()
            j = dw & 0x7FFFFFFF
        else:
            if wd & 0x8000:
                j = wd & 0x7FFF
            else:
                j = 0

        s = class_table.get(j, "")
        handler = _DISPATCH.get(s)
        if handler is None:
            if not s:
                # no class context — can't proceed reliably
                continue
            # unknown class name — skip quietly
            continue
        try:
            rec = handler(r, version)
        except EOFError:
            break
        out[s].append(rec)

        if s:
            i += 1

    return out


# ---------------------------------------------------------------------------
# Conversion to the parser.py / renderer.py JSON shape
# ---------------------------------------------------------------------------

def _fmt_lg(gi: int) -> str:
    return "0123456789abcdef"[gi] if 0 <= gi < 16 else hex(gi)


def _fmt_ly(li: int) -> str:
    return "0123456789abcdef"[li] if 0 <= li < 16 else hex(li)


def _convert(ents: dict[str, list[dict]], header: dict) -> tuple[list[dict], dict]:
    """Convert raw JWW records to the external-format-like entity shape."""
    out_entities: list[dict] = []
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")

    def bump(x: float, y: float) -> None:
        nonlocal xmin, ymin, xmax, ymax
        if x < xmin: xmin = x
        if x > xmax: xmax = x
        if y < ymin: ymin = y
        if y > ymax: ymax = y

    def base_attrs(b: dict) -> dict:
        return {
            "layer_group": _fmt_lg(b["glayer"]),
            "layer": _fmt_ly(b["layer"]),
            "line_color": b["pen_color"] if 1 <= b["pen_color"] <= 9 else (b["pen_color"] or 1),
            "line_type": b["pen_style"] if 1 <= b["pen_style"] <= 9 else 1,
            "line_width": b["pen_width"],
            "text_color": None,
            "font_name": None,
            "font": None,
        }

    # Lines
    for rec in ents.get("CDataSen", []):
        b = rec["_base"]
        e = {"type": "line", "x1": rec["x1"], "y1": rec["y1"],
             "x2": rec["x2"], "y2": rec["y2"], **base_attrs(b)}
        out_entities.append(e)
        bump(rec["x1"], rec["y1"]); bump(rec["x2"], rec["y2"])

    # Circles / arcs
    for rec in ents.get("CDataEnko", []):
        b = rec["_base"]
        e: dict = {"type": "circle", "x": rec["cx"], "y": rec["cy"],
                   "r": rec["r"], **base_attrs(b)}
        if not rec["full"]:
            start_deg = math.degrees(rec["start_rad"])
            end_deg = start_deg + math.degrees(rec["arc_rad"])
            e["start_angle"] = start_deg
            e["end_angle"] = end_deg
            e["direction"] = 0
        if rec["henpei"] and rec["henpei"] != 1.0:
            e["flatness"] = rec["henpei"]
        if rec["tilt_rad"]:
            e["tilt"] = math.degrees(rec["tilt_rad"])
        out_entities.append(e)
        # approximate bounding box
        bump(rec["cx"] - rec["r"], rec["cy"] - rec["r"])
        bump(rec["cx"] + rec["r"], rec["cy"] + rec["r"])

    # Text
    moji_table = header.get("moji_table") or []
    for rec in ents.get("CDataMoji", []):
        b = rec["_base"]
        text = rec["text"]
        n = max(1, len(text))
        # Size field in external format is the total text width.
        size_w = rec["size_x"] * n + rec["kankaku"] * (n - 1)
        e = {
            "type": "text",
            "x": rec["x1"], "y": rec["y1"],
            "size": size_w,
            "angle": rec["angle"],
            "text": text,
            "vertical": False,
            **base_attrs(b),
        }
        # Font snapshot: width/height/spacing/count (count kept at text length).
        e["font"] = {
            "mode": 0,
            "width": rec["size_x"],
            "height": rec["size_y"],
            "spacing": rec["kankaku"],
            "count": n,
        }
        e["font_name"] = rec["font_name"] or None
        e["text_color"] = b["pen_color"] if 1 <= b["pen_color"] <= 9 else (b["pen_color"] or 1)
        out_entities.append(e)
        bump(rec["x1"], rec["y1"])
        bump(rec["x2"], rec["y2"])

    # Solids → render outline as 4 lines (renderer has no fill support)
    for rec in ents.get("CDataSolid", []):
        b = rec["_base"]
        p1, p2, p3, p4 = rec["p1"], rec["p2"], rec["p3"], rec["p4"]
        pts = [p1, p2, p4, p3]  # JWW solid winds 1→2→4→3
        attrs = base_attrs(b)
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            out_entities.append({"type": "line", "x1": x1, "y1": y1,
                                 "x2": x2, "y2": y2, **attrs})
            bump(x1, y1); bump(x2, y2)

    if xmin == float("inf"):
        xmin = ymin = 0.0
        xmax = ymax = 1000.0
    rng = {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
    return out_entities, rng


def parse(path: str | Path) -> dict:
    data = Path(path).read_bytes()
    r = _R(data)
    hdr = _read_header(r)
    raw_ents = _read_entity_stream(r, hdr["version"])
    entities, rng = _convert(raw_ents, hdr)

    # Determine drawing scale: active layer group's scale is the conventional
    # "primary" scale; fall back to first non-1 scale or 1.
    scales = hdr["scales"]
    active_lg = None
    if entities:
        # Pick the dominant glayer
        counts: dict[str, int] = {}
        for e in entities:
            lg = e.get("layer_group")
            if lg is None: continue
            counts[lg] = counts.get(lg, 0) + 1
        if counts:
            active_lg = max(counts.items(), key=lambda kv: kv[1])[0]
    primary_scale = 50.0
    if active_lg is not None:
        try:
            primary_scale = float(scales[int(active_lg, 16)])
        except (ValueError, IndexError):
            pass

    paper = _PAPER_MM.get(hdr["m_nZumen"])
    paper_dict = {"width": paper[0], "height": paper[1]} if paper else None

    out = {
        "header": {
            "hq": True,
            "hk": 0,
            "scales": scales,
            "scale": primary_scale,
            "paper": paper_dict,
            "range": rng,
            "char_widths": [m["width"] for m in hdr.get("moji_table", [])],
            "char_heights": [m["height"] for m in hdr.get("moji_table", [])],
            "char_spacing": [m["spacing"] for m in hdr.get("moji_table", [])],
            "char_colors": [m["color"] for m in hdr.get("moji_table", [])],
            "memo": hdr.get("memo"),
            "version": hdr["version"],
        },
        "entities": entities,
        "blocks": [],
    }
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _write_json(result: dict, out_path: Path) -> None:
    counts: dict[str, int] = {}
    for e in result["entities"]:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    result["summary"] = {
        "entity_counts": counts,
        "total_entities": len(result["entities"]),
        "block_count": len(result["blocks"]),
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("usage:")
        print("  python3 jww_parser.py INPUT.jww [OUTPUT.json]")
        print("  python3 jww_parser.py --batch INPUT_DIR OUTPUT_DIR")
        sys.exit(0)

    if argv[0] == "--batch":
        if len(argv) < 3:
            print("--batch requires INPUT_DIR and OUTPUT_DIR", file=sys.stderr)
            sys.exit(2)
        in_dir = Path(argv[1]); out_dir = Path(argv[2])
        out_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(in_dir.glob("*.jww")) + sorted(in_dir.glob("*.JWW"))
        if not files:
            print(f"no .jww files in {in_dir}")
            return
        for f in files:
            out_path = out_dir / (f.stem + ".json")
            try:
                result = parse(f)
                _write_json(result, out_path)
                s = result["summary"]
                print(f"  {f.name} -> {out_path.name}  "
                      f"{s['entity_counts']}  ({s['total_entities']} total)")
            except Exception as exc:
                print(f"  {f.name} FAILED: {exc}")
        return

    src = Path(argv[0])
    out = Path(argv[1]) if len(argv) > 1 else src.with_suffix(".json")
    result = parse(src)
    _write_json(result, out)
    s = result["summary"]
    print(f"Parsed {src} -> {out}")
    print(f"  version:   {result['header']['version']}")
    print(f"  paper:     {result['header']['paper']}")
    print(f"  scale:     1:{int(result['header']['scale'] or 0)}")
    print(f"  range:     {result['header']['range']}")
    print(f"  entities:  {s['entity_counts']}  (total {s['total_entities']})")


if __name__ == "__main__":
    main()
