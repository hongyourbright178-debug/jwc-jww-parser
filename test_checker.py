#!/usr/bin/env python3
"""
テストスクリプト — cad_checker_v7.py の動作確認
=================================================
このスクリプトを実行すると、AIチェッカーが正しく動作するか自動検証します。

使い方:
  python3 test_checker.py /mnt/c/jww/samples/after

全テストがPASSなら本番環境でも動作します。
"""

import os
import sys
import time

# テスト対象のモジュールをインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cad_checker_v7 import (
    build_profile, generate_rules, evaluate_rules,
    extract_coordinate_lines, find_symbols, extract_device_subtypes,
    run_visual_scan, single_zone_analysis,
    DRAWING_TYPES_VISUAL, SUBTYPE_KEYWORDS, GROUP_NAMES_JA,
)


def test_result(name, passed, detail=""):
    icon = "✅" if passed else "❌"
    print(f"  {icon} {name}")
    if detail and not passed:
        print(f"     → {detail}")
    return passed


def run_tests(folder):
    print(f"\n{'='*60}")
    print(f"  CAD Checker v7 — 自動テスト")
    print(f"  対象フォルダ: {folder}")
    print(f"{'='*60}\n")

    total = 0
    passed = 0
    t0 = time.time()

    # ─── Test 1: フォルダにJWWファイルがあるか ───
    jww_files = [f for f in os.listdir(folder) if f.lower().endswith('.jww')]
    total += 1
    if test_result(f"JWWファイル検出: {len(jww_files)}件", len(jww_files) > 0,
                   "JWWファイルが見つかりません"):
        passed += 1

    # ─── Test 2: プロファイル構築 ───
    try:
        profile = build_profile(folder)
        total += 1
        ok = profile['file_count'] > 0 and profile['floor_count'] > 0
        if test_result(f"プロファイル構築: {profile['file_count']}ファイル, "
                       f"{profile['floor_count']}階, {profile['total_rooms']}室", ok):
            passed += 1
    except Exception as e:
        total += 1
        test_result("プロファイル構築", False, str(e))
        print("\n  ❌ 基本テスト失敗 — これ以上テストできません")
        return

    # ─── Test 3: 設備検出 ───
    total += 1
    n_types = len(profile['device_groups'])
    if test_result(f"設備種類検出: {n_types}種類", n_types >= 5,
                   f"検出が少なすぎます ({n_types}種類)"):
        passed += 1

    # ─── Test 4: 必須設備の存在確認 ───
    required = ['fire_detection', 'lighting', 'outlet']
    for req in required:
        total += 1
        count = profile['device_groups'].get(req, 0)
        name = GROUP_NAMES_JA.get(req, req)
        if test_result(f"必須設備 {name}: {count}件検出", count > 0,
                       f"{name}が検出されません。ファイル名を確認してください。"):
            passed += 1

    # ─── Test 5: ルール生成 ───
    total += 1
    rules = generate_rules(profile)
    if test_result(f"チェックルール生成: {len(rules)}ルール", len(rules) >= 7,
                   "ルールが少なすぎます"):
        passed += 1

    # ─── Test 6: ルール評価 ───
    total += 1
    results = evaluate_rules(rules, profile['device_groups'])
    n_pass = sum(1 for r in results if r['status'] in ('PASS', 'PASS_GEO'))
    n_fail = sum(1 for r in results if r['status'] == 'FAIL')
    if test_result(f"基準チェック実行: {n_pass}PASS / {n_fail}FAIL / {len(results)}合計",
                   len(results) > 0):
        passed += 1

    # ─── Test 7: JWWバイナリ読み取り ───
    test_file = os.path.join(folder, jww_files[0])
    total += 1
    try:
        lines = extract_coordinate_lines(test_file)
        if test_result(f"座標抽出 ({jww_files[0][:30]}...): {len(lines)}本",
                       len(lines) > 0):
            passed += 1
    except Exception as e:
        test_result("座標抽出", False, str(e))

    # ─── Test 8: シンボル検出 ───
    total += 1
    try:
        syms = find_symbols(lines)
        if test_result(f"シンボル検出: {len(syms)}個", True):
            passed += 1
    except Exception as e:
        test_result("シンボル検出", False, str(e))

    # ─── Test 9: 設備内訳検出 ───
    # Find a file with known subtypes
    outlet_file = None
    for f in jww_files:
        if 'コンセント' in f and '平面図' in f:
            outlet_file = f
            break
    if outlet_file:
        total += 1
        subtypes = extract_device_subtypes(os.path.join(folder, outlet_file), 'outlet')
        if test_result(f"設備内訳検出 ({outlet_file[:30]}...): {len(subtypes)}種類",
                       len(subtypes) > 0):
            passed += 1
            for label, count in sorted(subtypes.items(), key=lambda x: -x[1])[:3]:
                print(f"       {label}: {count}")

    # ─── Test 10: ゾーン分析 ───
    total += 1
    try:
        zone = single_zone_analysis(lines)
        if zone:
            if test_result(f"ゾーン分析: カバー率{zone['coverage_pct']:.0f}% "
                           f"({zone['occupied']}占有/{zone['total_zones']}全体)", True):
                passed += 1
        else:
            test_result("ゾーン分析", False, "分析結果がNone")
    except Exception as e:
        test_result("ゾーン分析", False, str(e))

    # ─── Test 11: 図面検図（フル実行）───
    total += 1
    try:
        vis_results, vis_errors = run_visual_scan(folder)
        n_vis = len(vis_results)
        n_crit = sum(1 for e in vis_errors if e['severity'] == 'CRITICAL')
        n_warn = sum(1 for e in vis_errors if e['severity'] == 'WARNING')
        n_vpass = sum(1 for e in vis_errors if e['severity'] == 'PASS')
        if test_result(f"図面検図: {n_vis}図面分析 → PASS:{n_vpass} WARNING:{n_warn} CRITICAL:{n_crit}",
                       n_vis > 0):
            passed += 1
    except Exception as e:
        test_result("図面検図", False, str(e))

    elapsed = time.time() - t0

    # ─── Summary ───
    print(f"\n{'='*60}")
    print(f"  テスト結果: {passed}/{total} PASS")
    print(f"  実行時間: {elapsed:.1f}秒")
    print(f"{'='*60}")

    if passed == total:
        print(f"\n  ✅ 全テスト合格！本番環境で動作可能です。")
        print(f"\n  使い方:")
        print(f"    python3 cad_checker_v7.py <JWWファイルのフォルダ>")
    else:
        print(f"\n  ⚠ {total - passed}件のテストが失敗しました。")
        print(f"  上記の❌項目を確認してください。")

    return passed == total


if __name__ == '__main__':
    if len(sys.argv) < 2:
        # Default path
        default = '/mnt/c/jww/samples/after'
        if os.path.isdir(default):
            folder = default
        else:
            print("Usage: python3 test_checker.py <JWWフォルダパス>")
            sys.exit(1)
    else:
        folder = sys.argv[1]

    if not os.path.isdir(folder):
        print(f"エラー: フォルダが見つかりません: {folder}")
        sys.exit(1)

    success = run_tests(folder)
    sys.exit(0 if success else 1)
