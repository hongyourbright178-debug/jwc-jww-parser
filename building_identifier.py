#!/usr/bin/env python3
"""Building-type identifier from CAD drawing text entities.

Reads a JWW binary file, JWC_TEMP.TXT, or our existing parsed JSON, then
scores the drawing against building-type keyword sets (room names, labels)
and returns the best-matching type with a confidence score.

Usage:
    python3 building_identifier.py INPUT.jww
    python3 building_identifier.py INPUT.json        # parsed output
    python3 building_identifier.py INPUT.TXT         # JWC_TEMP.TXT
    python3 building_identifier.py --batch DIR       # scan a directory
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Keyword sets per building type
# ---------------------------------------------------------------------------
# Three evidence tiers:
#   project_name  — nouns that name the building type outright (保育園, 病院,
#                   マンション, …). A single hit anywhere in the drawing is
#                   near-definitive because these appear in the 工事名 text.
#   primary       — room/facility names that only occur in this building type.
#   secondary     — supportive room names.
#   tertiary      — weak signal; shared across types (e.g. 厨房 appears in
#                   hospitals, schools, nurseries).
#
# Scoring weights: project_name × 10, primary × 3, secondary × 2, tertiary × 1.
# The project_name tier is searched first; if any keyword hits it sets a
# high-confidence answer. Room-name matching is a fallback.

BUILDING_TYPES: dict[str, dict] = {
    "保育園": {
        "label_en": "nursery",
        "project_name": [
            "保育園", "保育所", "こども園", "子ども園", "幼稚園", "認定こども園",
        ],
        "primary": [
            "保育室", "乳児室", "ほふく室", "調乳室", "沐浴室", "園庭",
        ],
        "secondary": [
            "遊戯室", "お昼寝室", "お遊戯", "園児", "ランチルーム",
            "おむつ", "授乳室",
        ],
        "tertiary": [
            "給食室", "厨房",
        ],
    },
    "病院": {
        "label_en": "hospital",
        "project_name": [
            "病院", "総合病院", "診療所", "クリニック", "医院", "医療センター",
            "病棟", "救急センター",
        ],
        "primary": [
            "病室", "手術室", "ICU", "ＩＣＵ", "ナースステーション",
            "ナースコール", "診察室", "処置室", "レントゲン室",
            "CT室", "ＣＴ室", "MRI", "ＭＲＩ", "X線", "Ｘ線",
            "医療", "ＰＡＣＳ", "PACS", "放射線室", "透析室",
        ],
        "secondary": [
            "待合室", "受付", "薬局", "薬剤", "内視鏡", "手術",
            "分娩", "新生児", "外来", "救急",
        ],
        "tertiary": [
            "看護", "検査", "詰所",
        ],
    },
    "住宅": {
        "label_en": "residential",
        "project_name": [
            "マンション", "アパート", "戸建", "戸建て", "団地", "共同住宅",
            "集合住宅", "タウンハウス", "テラスハウス",
            "県営住宅", "市営住宅", "都営住宅", "府営住宅", "公営住宅",
            "公団住宅", "分譲住宅", "賃貸住宅", "ハイツ",
            # '住宅' alone is too generic (appears in many titles), we pair
            # it with the explicit 共同住宅/集合住宅 forms above.
        ],
        "primary": [
            "リビング", "ＬＤＫ", "LDK", "寝室", "主寝室", "子供室",
            "玄関", "浴室", "洗面所", "勝手口", "和室", "居間",
        ],
        "secondary": [
            "キッチン", "台所", "押入", "縁側", "バルコニー", "テラス",
            "クローゼット", "納戸", "書斎", "吹抜",
        ],
        "tertiary": [
            "脱衣", "洗濯", "トイレ", "便所",
        ],
    },
    "事務所": {
        "label_en": "office",
        "project_name": [
            "事務所ビル", "オフィスビル", "本社ビル", "事業所",
        ],
        "primary": [
            "会議室", "執務室", "サーバー室", "サーバ室", "役員室",
            "応接室", "社長室", "秘書室", "オフィス",
        ],
        "secondary": [
            "打合せ", "打ち合わせ", "ミーティング",
            "コピー室", "資料室", "書庫", "社員",
        ],
        "tertiary": [
            "休憩室", "更衣室",
        ],
    },
    "学校": {
        "label_en": "school",
        "project_name": [
            "小学校", "中学校", "高等学校", "高校", "大学", "専門学校",
            "学園", "学院", "高等専門学校", "短期大学",
        ],
        "primary": [
            "教室", "職員室", "体育館", "理科室", "図書室", "音楽室",
            "美術室", "家庭科室", "視聴覚室", "校長室", "保健室",
            "普通教室", "特別教室",
        ],
        "secondary": [
            "講堂", "校庭", "グラウンド", "部室", "クラブ", "生徒",
            "児童", "学級",
        ],
        "tertiary": [
            "ロッカー", "下駄箱", "昇降口",
        ],
    },
    "店舗": {
        "label_en": "store",
        "project_name": [
            "店舗", "百貨店", "スーパー", "ショッピングセンター",
            "ショッピングモール", "商業施設", "ドラッグストア", "量販店",
        ],
        "primary": [
            "売場", "売り場", "バックヤード", "レジ", "商品", "陳列",
            "ショップ", "テナント",
        ],
        "secondary": [
            "試着", "ストック", "ディスプレイ", "フィッティング",
            "カウンター", "ショーケース",
        ],
        "tertiary": [
            "倉庫", "従業員",
        ],
    },
    "ホテル": {
        "label_en": "hotel",
        "project_name": [
            "ホテル", "旅館", "リゾート", "民宿", "ゲストハウス",
        ],
        "primary": [
            "客室", "フロント", "ロビー", "スイート", "ツイン",
            "シングル", "ダブル", "宴会場", "チャペル",
        ],
        "secondary": [
            "ベッド", "ラウンジ", "レストラン", "バー", "宿泊",
            "コンシェルジュ", "ポーター", "ボールルーム",
        ],
        "tertiary": [
            "客用",
        ],
    },
    "介護施設": {
        "label_en": "nursing_care",
        "project_name": [
            "老人ホーム", "特別養護老人ホーム", "特養", "介護老人保健施設",
            "老健", "介護施設", "デイサービスセンター", "サービス付き高齢者",
            "障害者支援施設", "福祉施設", "養護施設", "グループホーム",
            "ショートステイ", "小規模多機能", "有料老人ホーム",
        ],
        "primary": [
            "機能訓練室", "介護浴室", "デイサービス", "ショートステイ",
            "グループホーム", "特養", "老人ホーム", "介護", "居室",
            "相談室", "リハビリ室", "静養室", "地域交流", "入居者",
        ],
        "secondary": [
            "訓練", "生活相談", "面談", "送迎", "介助", "リハビリ",
        ],
        "tertiary": [
            "共用",
        ],
    },
}


# Used for tie-breaking when a single project names multiple building types,
# and to order components of a composite-facility result.
PRIORITY_ORDER: list[str] = [
    "病院", "介護施設", "保育園", "学校", "ホテル", "事務所", "店舗", "住宅",
]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Lower-case and strip punctuation for robust substring matching."""
    if not s:
        return ""
    # Unify full-width Arabic digits, Latin letters, and strip common decorative chars.
    table = str.maketrans(
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    )
    s = s.translate(table)
    # Keep Japanese chars as-is; drop whitespace / punctuation.
    out = []
    for ch in s:
        if ch.isspace():
            continue
        if ch in "　・、。，．,.()（）[]【】「」『』/／\\-_|:：":
            continue
        out.append(ch)
    return "".join(out).upper()


def _title_block_texts(text_entities: list[dict]) -> list[str]:
    """Pick texts that look like they come from the title block.

    Heuristic: anything whose font size is in the top quartile is a candidate,
    plus anything containing the common title-block markers (工事, 建築,
    仮称, 新築, 改修, 増築, 建替, 建設). Returns raw strings.
    """
    if not text_entities:
        return []
    sizes = sorted(
        [e.get("size", 0) for e in text_entities if e.get("size") and e.get("text", "").strip()]
    )
    p75 = sizes[int(len(sizes) * 0.75)] if sizes else 0
    out: list[str] = []
    title_markers = ("工事", "建築", "仮称", "新築", "改修", "増築", "建替", "建設", "計画")
    for e in text_entities:
        t = e.get("text", "")
        if not t or not t.strip():
            continue
        sz = e.get("size", 0) or 0
        if sz >= p75 or any(m in t for m in title_markers):
            out.append(t)
    return out


_TITLE_MARKERS = ("工事", "建築", "仮称", "新築", "改修", "増築", "建替", "建設", "計画")


def _project_name_pass(text_entities: list[dict]) -> dict | None:
    """Phase 1: look for definitive building-type nouns in title-block texts.

    Each candidate match must appear in a text that ALSO contains a
    project-marker word (工事/仮称/建築/…). This filters out false positives
    like 'ホテルパン' (a kitchen pan) that merely contain a building-type
    substring but aren't the 工事名.

    Returns a classification dict if a match is found, else None. This pass
    is treated as near-definitive: any match yields ≥95% confidence.
    """
    title_texts = _title_block_texts(text_entities)
    if not title_texts:
        return None
    # Keep only texts that look like project titles (have a marker word).
    title_texts = [t for t in title_texts if any(m in t for m in _TITLE_MARKERS)]
    if not title_texts:
        return None
    normalized = [_normalize(t) for t in title_texts]

    per_type_hits: dict[str, dict[str, int]] = {}
    per_type_texts: dict[str, list[str]] = {}
    for jp_type, spec in BUILDING_TYPES.items():
        hits: dict[str, int] = {}
        matched_texts: list[str] = []
        for kw in spec.get("project_name", []):
            nkw = _normalize(kw)
            if not nkw:
                continue
            count = 0
            for orig, norm in zip(title_texts, normalized):
                if nkw in norm:
                    count += 1
                    if orig not in matched_texts:
                        matched_texts.append(orig)
            if count:
                hits[kw] = count
        if hits:
            per_type_hits[jp_type] = hits
            per_type_texts[jp_type] = matched_texts

    if not per_type_hits:
        return None

    # Order matched types by the global priority list first, then by raw hit
    # count for anything outside that list.
    priority_rank = {t: i for i, t in enumerate(PRIORITY_ORDER)}
    matched_types = sorted(
        per_type_hits.keys(),
        key=lambda t: (priority_rank.get(t, len(PRIORITY_ORDER)),
                       -sum(per_type_hits[t].values())),
    )

    is_composite = len(matched_types) >= 2
    if is_composite:
        best_type = "+".join(matched_types) + "(複合施設)"
        best_type_en = "+".join(BUILDING_TYPES[t]["label_en"] for t in matched_types) + " (composite)"
        merged_keywords: dict[str, int] = {}
        for t in matched_types:
            for k, v in per_type_hits[t].items():
                merged_keywords[k] = merged_keywords.get(k, 0) + v
        merged_texts: list[str] = []
        for t in matched_types:
            for s in per_type_texts.get(t, []):
                if s not in merged_texts:
                    merged_texts.append(s)
        return {
            "method": "title_block",
            "best_type": best_type,
            "best_type_en": best_type_en,
            "best_confidence": 1.0,
            "is_composite": True,
            "component_types": matched_types,
            "per_type_keywords": {t: per_type_hits[t] for t in matched_types},
            "matched_keywords": merged_keywords,
            "title_block_texts": merged_texts,
        }

    best_type = matched_types[0]
    best_hits = per_type_hits[best_type]
    total_hits = sum(sum(h.values()) for h in per_type_hits.values())
    best_total = sum(best_hits.values())
    confidence = 0.95 + 0.05 * (best_total / total_hits) if total_hits else 0.95
    confidence = round(min(1.0, confidence), 4)
    return {
        "method": "title_block",
        "best_type": best_type,
        "best_type_en": BUILDING_TYPES[best_type]["label_en"],
        "best_confidence": confidence,
        "is_composite": False,
        "matched_keywords": best_hits,
        "competing_types": {k: v for k, v in per_type_hits.items() if k != best_type},
        "title_block_texts": per_type_texts.get(best_type, []),
    }


def _room_name_pass(texts: list[str]) -> dict:
    """Phase 2: score each type by room-name (primary/secondary/tertiary) matches."""
    normalized = [_normalize(t) for t in texts if t]

    per_type_scores: list[dict] = []
    for jp_type, spec in BUILDING_TYPES.items():
        hits: dict[str, int] = {}
        weighted = 0
        buckets = [
            (spec.get("primary", []), 3),
            (spec.get("secondary", []), 2),
            (spec.get("tertiary", []), 1),
        ]
        for keywords, w in buckets:
            for kw in keywords:
                nkw = _normalize(kw)
                if not nkw:
                    continue
                count = sum(1 for t in normalized if nkw in t)
                if count:
                    hits[kw] = count
                    weighted += w * min(count, 5)

        primary_hit_count = sum(
            1 for kw in spec.get("primary", []) if kw in hits
        )
        per_type_scores.append({
            "type": jp_type,
            "label_en": spec["label_en"],
            "weighted": weighted,
            "primary_hits": primary_hit_count,
            "hits": hits,
        })

    total_weighted = sum(s["weighted"] for s in per_type_scores)
    for s in per_type_scores:
        s["confidence"] = round(s["weighted"] / total_weighted, 4) if total_weighted else 0.0

    # Primary sort: weighted score desc, primary_hits desc.
    # Tie-break on score: priority order (病院 > 介護施設 > 保育園 > …).
    priority_rank = {t: i for i, t in enumerate(PRIORITY_ORDER)}
    per_type_scores.sort(
        key=lambda s: (
            -s["weighted"],
            -s["primary_hits"],
            priority_rank.get(s["type"], len(PRIORITY_ORDER)),
        ),
    )
    best = per_type_scores[0] if per_type_scores and per_type_scores[0]["weighted"] > 0 else None
    return {
        "method": "room_names",
        "best_type": best["type"] if best else None,
        "best_type_en": best["label_en"] if best else None,
        "best_confidence": best["confidence"] if best else 0.0,
        "best_weighted_score": best["weighted"] if best else 0,
        "matched_keywords": best["hits"] if best else {},
        "ranking": [
            {
                "type": s["type"], "label_en": s["label_en"],
                "confidence": s["confidence"],
                "weighted_score": s["weighted"],
                "primary_hits": s["primary_hits"],
                "hits_count": len(s["hits"]),
                "top_hits": dict(list(s["hits"].items())[:5]),
            }
            for s in per_type_scores
        ],
    }


def classify(texts_or_entities) -> dict:
    """Run title-block match first, fall back to room-name scoring.

    Accepts either:
      - list[str]      — raw strings (project_name pass is skipped)
      - list[dict]     — text entity dicts with "text" and "size" fields
                         (project_name pass can weight by font size)
    """
    if texts_or_entities and isinstance(texts_or_entities[0], dict):
        entities = texts_or_entities
        texts = [e.get("text", "") for e in entities]
    else:
        entities = [{"text": t, "size": 0} for t in texts_or_entities]
        texts = list(texts_or_entities)

    title_result = _project_name_pass(entities)
    room_result = _room_name_pass(texts)

    if title_result:
        return {
            **title_result,
            "text_count": sum(1 for t in texts if t and t.strip()),
            "room_ranking": room_result["ranking"],
            "room_best": room_result["best_type"],
        }

    return {
        **room_result,
        "text_count": sum(1 for t in texts if t and t.strip()),
    }


# ---------------------------------------------------------------------------
# Input adapters
# ---------------------------------------------------------------------------

def _text_entities_from_parsed(data: dict) -> list[dict]:
    out = []
    for e in data.get("entities", []):
        if e.get("type") != "text":
            continue
        out.append({"text": e.get("text", ""), "size": e.get("size", 0)})
    return out


def _extract_text_entities(path: Path) -> tuple[list[dict], str]:
    """Return (entities, source_kind)."""
    suffix = path.suffix.lower()
    if suffix == ".jww":
        from jww_parser import parse as jww_parse
        data = jww_parse(path)
        return _text_entities_from_parsed(data), "jww"
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return _text_entities_from_parsed(data), "json"
    if suffix in (".txt",):
        from parser import parse as txt_parse
        data = txt_parse(path)
        return _text_entities_from_parsed(data), "jwc_temp"
    raise ValueError(f"unsupported input: {path} (expected .jww, .json, or .txt)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report(path: Path, result: dict) -> None:
    print(f"=== {path.name} ===")
    print(f"text entities:   {result.get('text_count')}")
    method = result.get("method")
    best = result.get("best_type")
    if best:
        print(f"building type:   {best} ({result['best_type_en']})")
        print(f"confidence:      {result['best_confidence']:.1%}  "
              f"[method: {method}]")
        print("matched keywords:")
        for kw, n in sorted(
            result.get("matched_keywords", {}).items(), key=lambda kv: -kv[1]
        ):
            print(f"  {kw:20s} ×{n}")
        if method == "title_block":
            if result.get("is_composite"):
                print(f"composite components: {' + '.join(result['component_types'])}")
                for t, kws in result.get("per_type_keywords", {}).items():
                    print(f"  [{t}] matched: {', '.join(kws.keys())}")
            print("title-block texts matched:")
            for t in result.get("title_block_texts", [])[:5]:
                print(f"  • {t!r}")
            if result.get("competing_types"):
                print("also found (weaker):")
                for k, v in result["competing_types"].items():
                    print(f"  {k}: {v}")
            if result.get("room_best") and result["room_best"] != best:
                print(f"(room-name fallback would say: {result['room_best']})")
    else:
        print("building type:   UNKNOWN (no keyword hits)")
    if "ranking" in result:
        print("room-name ranking:")
        for r in result["ranking"][:5]:
            print(f"  {r['type']:6s} ({r['label_en']:12s})  "
                  f"conf={r['confidence']:.1%}  score={r['weighted_score']:>3}  "
                  f"primary_hits={r['primary_hits']}")
    print()


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("usage:")
        print("  python3 building_identifier.py INPUT.jww|.json|.TXT")
        print("  python3 building_identifier.py --batch DIR")
        sys.exit(0)

    if argv[0] == "--batch":
        if len(argv) < 2:
            print("--batch requires DIR", file=sys.stderr)
            sys.exit(2)
        d = Path(argv[1])
        files = (
            sorted(d.glob("*.jww")) +
            sorted(d.glob("*.JWW")) +
            sorted(d.glob("*.json")) +
            sorted(d.glob("*.TXT")) +
            sorted(d.glob("*.txt"))
        )
        agg_entities: list[dict] = []
        for f in files:
            try:
                entities, kind = _extract_text_entities(f)
            except Exception as exc:
                print(f"[skip] {f.name}: {exc}")
                continue
            res = classify(entities)
            agg_entities.extend(entities)
            best = res["best_type"] or "—"
            mark = "★" if res.get("method") == "title_block" else " "
            print(f"  {mark} {f.name:55s}  {best:6s} "
                  f"conf={res['best_confidence']:.0%}  ({res['text_count']} texts)")
        if agg_entities:
            print()
            print("--- Aggregate across all files ---")
            agg = classify(agg_entities)
            _print_report(Path(f"{d}/(aggregate)"), agg)
        return

    path = Path(argv[0])
    entities, _ = _extract_text_entities(path)
    result = classify(entities)
    _print_report(path, result)


if __name__ == "__main__":
    main()
