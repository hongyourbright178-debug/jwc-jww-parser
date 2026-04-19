#!/usr/bin/env python3
"""
CAD Checker v7 — Feature-Based Adaptive Checker
=================================================
NO hardcoded building types. Works with ANY building.

Design philosophy:
  "Don't ask WHAT type of building. Ask WHAT is IN the building."

The system has 3 layers of rules:

Layer 1: UNIVERSAL (全建物共通)
  Fire detection, emergency lighting, exit lights, distribution boards, ELCB
  → Required for ALL buildings by 消防法 and 建築基準法
  → Thresholds auto-calculated from floor count and building scale

Layer 2: FEATURE-TRIGGERED (検出した設備に応じて)
  IF nurse_call detected → verify adequate quantity
  IF medical_ground detected → verify per surgery/ICU room
  IF solar panels detected → verify system diagram exists
  → Rules only activate when the system finds relevant equipment

Layer 3: LEARNED BASELINES (学習した基準値)
  From past engineer-corrected projects, the system learns:
  "For a 5-floor building with 100 rooms, expect ~1000 fire detectors"
  → Auto-adjusts thresholds for new projects of similar scale

This means: factory, school, hotel, temple, warehouse — ALL work.
No code changes needed for new building types.
"""

import os
import sys
import re
import json
import struct
import math
from collections import defaultdict
from datetime import datetime


# ============================================================
# 1. KEYWORD DATABASE
# ============================================================

SCAN_DB = {
    # === Devices ===
    'コンセント': ('outlet', 'text'),
    'ｺﾝｾﾝﾄ': ('outlet', 'symbol'),
    '接地極付': ('outlet_grounded', 'text'),
    '接地付': ('outlet_grounded', 'text'),
    'ｱｰｽ': ('outlet_grounded', 'symbol'),
    'アース': ('outlet_grounded', 'text'),
    'EET': ('outlet_grounded', 'text'),
    '防水コンセント': ('outlet_wp', 'text'),
    '専用コンセント': ('outlet_dedicated', 'text'),
    'スイッチ': ('switch', 'text'),
    'ｽｲｯﾁ': ('switch', 'symbol'),
    '３路': ('switch_3way', 'text'),
    '3路': ('switch_3way', 'text'),
    'ﾀﾝﾌﾞﾗ': ('switch', 'symbol'),
    'ダウンライト': ('downlight', 'text'),
    'ﾀﾞｳﾝﾗｲﾄ': ('downlight', 'symbol'),
    'シーリング': ('ceiling_light', 'text'),
    'ｼｰﾘﾝｸﾞ': ('ceiling_light', 'symbol'),
    'ブラケット': ('bracket_light', 'text'),
    'ﾌﾞﾗｹｯﾄ': ('bracket_light', 'symbol'),
    '蛍光灯': ('fluorescent', 'text'),
    'LED': ('led', 'text'),
    'ＬＥＤ': ('led', 'text'),
    'Hf': ('hf_light', 'text'),
    '照明器具': ('light_fixture', 'text'),
    '感知器': ('detector', 'text'),
    'ｶﾝﾁｷ': ('detector', 'symbol'),
    '煙感知': ('smoke_detector', 'text'),
    '煙': ('smoke_detector', 'symbol'),
    '熱感知': ('heat_detector', 'text'),
    '熱': ('heat_detector', 'symbol'),
    '差動': ('diff_detector', 'text'),
    '定温': ('fixed_temp', 'text'),
    '光電': ('photoelectric', 'text'),
    '発信機': ('fire_button', 'text'),
    '受信機': ('fire_panel', 'text'),
    '自火報': ('fire_alarm_sys', 'text'),
    '報知': ('fire_alarm_sys', 'symbol'),
    '誘導灯': ('exit_light', 'text'),
    'ﾕｳﾄﾞｳﾄｳ': ('exit_light', 'symbol'),
    '非常照明': ('emergency_light', 'text'),
    '非常灯': ('emergency_light', 'text'),
    '避難口': ('exit_sign', 'text'),
    '分電盤': ('dist_board', 'text'),
    'ﾌﾞﾝﾃﾞﾝﾊﾞﾝ': ('dist_board', 'symbol'),
    '配電盤': ('main_board', 'text'),
    'MCCB': ('breaker', 'text'),
    'ELB': ('elcb', 'text'),
    'ELCB': ('elcb', 'text'),
    'ＥＬＣＢ': ('elcb', 'text'),
    'ナースコール': ('nurse_call', 'text'),
    'ﾅｰｽｺｰﾙ': ('nurse_call', 'symbol'),
    'インターホン': ('intercom', 'text'),
    'ｲﾝﾀｰﾎﾝ': ('intercom', 'symbol'),
    '非常放送': ('broadcast', 'text'),
    '放送': ('broadcast', 'symbol'),
    '電気錠': ('electric_lock', 'text'),
    'ﾃﾞﾝｷｼﾞｮｳ': ('electric_lock', 'symbol'),
    '医療アース': ('medical_ground', 'text'),
    '医療用接地': ('medical_ground', 'text'),
    'UPS': ('ups', 'text'),
    'ＵＰＳ': ('ups', 'text'),
    'スプリンクラー': ('sprinkler', 'text'),
    'ｽﾌﾟﾘﾝｸﾗ': ('sprinkler', 'symbol'),
    'ITV': ('itv', 'text'),
    'LAN': ('lan', 'text'),
    '太陽光': ('solar', 'text'),
    '太陽光発電': ('solar_power', 'text'),
    '自家発電': ('generator', 'text'),
    '受変電': ('substation', 'text'),
    'テレビ共聴': ('tv_common', 'text'),
    '電話設備': ('telephone', 'text'),
    '低圧幹線': ('trunk_line_lv', 'text'),
    '動力': ('power_supply', 'text'),
    '空調': ('hvac', 'text'),
    '換気': ('ventilation', 'text'),
    '排煙': ('smoke_exhaust', 'text'),
    '防排煙': ('smoke_control', 'text'),
    '自動制御': ('auto_control', 'text'),
    '避雷': ('lightning_rod', 'text'),
    '接地': ('grounding', 'text'),

    # === Rooms (feature indicators) ===
    '病室': ('room_patient', 'text'),
    '手術': ('room_surgery', 'text'),
    'ＩＣＵ': ('room_icu', 'text'),
    'ICU': ('room_icu', 'text'),
    'ナースステーション': ('room_nurse_st', 'text'),
    '厨房': ('room_kitchen', 'text'),
    '調理': ('room_kitchen', 'text'),
    '廊下': ('room_corridor', 'text'),
    '階段': ('room_stairs', 'text'),
    '保育室': ('room_nursery', 'text'),
    '遊戯室': ('room_play', 'text'),
    '乳児室': ('room_infant', 'text'),
    '事務室': ('room_office', 'text'),
    '集会室': ('room_assembly', 'text'),
    '居室': ('room_dwelling', 'text'),
    'ﾀｲﾌﾟ': ('room_unit_type', 'text'),
    '教室': ('room_classroom', 'text'),
    '客室': ('room_guest', 'text'),
    'ロビー': ('room_lobby', 'text'),
    '受付': ('room_reception', 'text'),
    '倉庫': ('room_storage', 'text'),
    '機械室': ('room_mechanical', 'text'),
    '電気室': ('room_electrical', 'text'),
    'サーバ': ('room_server', 'text'),
    '駐車': ('room_parking', 'text'),
    '浴室': ('room_bath', 'text'),
    'プール': ('room_pool', 'text'),
    '体育': ('room_gym', 'text'),
    '礼拝': ('room_worship', 'text'),
    '工場': ('room_factory', 'text'),
    '作業': ('room_workshop', 'text'),
    '売場': ('room_sales_floor', 'text'),

    # === Building type indicators ===
    '病院': ('bldg_hospital', 'text'),
    '診療所': ('bldg_clinic', 'text'),
    '保育': ('bldg_nursery', 'text'),
    '幼稚園': ('bldg_kindergarten', 'text'),
    '学校': ('bldg_school', 'text'),
    '住宅': ('bldg_residential', 'text'),
    'マンション': ('bldg_residential', 'text'),
    '共同住宅': ('bldg_residential', 'text'),
    '共用': ('bldg_residential', 'text'),
    'ホテル': ('bldg_hotel', 'text'),
    '旅館': ('bldg_hotel', 'text'),
    '老人ホーム': ('bldg_elderly', 'text'),
    '介護': ('bldg_elderly', 'text'),
    'デイサービス': ('bldg_elderly', 'text'),
    '福祉': ('bldg_welfare', 'text'),
    '事務所ビル': ('bldg_office', 'text'),
    'オフィス': ('bldg_office', 'text'),
    '工場': ('bldg_factory', 'text'),
    '倉庫': ('bldg_warehouse', 'text'),
    '店舗': ('bldg_retail', 'text'),
    '百貨店': ('bldg_retail', 'text'),
    '飲食店': ('bldg_restaurant', 'text'),
    '図書館': ('bldg_library', 'text'),
    '美術館': ('bldg_museum', 'text'),
    '体育館': ('bldg_gym', 'text'),
    '劇場': ('bldg_theater', 'text'),
    '映画館': ('bldg_theater', 'text'),
    '駐車場': ('bldg_parking', 'text'),
    '神社': ('bldg_shrine', 'text'),
    '寺院': ('bldg_temple', 'text'),
    '教会': ('bldg_church', 'text'),
}

# Pre-encode
def _build_keyword_table():
    table = []
    for kw, (dt, src) in SCAN_DB.items():
        try:
            table.append((kw.encode('shift_jis'), dt))
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        if all(ord(c) < 128 for c in kw):
            table.append((kw.encode('ascii'), dt))
    return table

_KW_TABLE = _build_keyword_table()

# Device groups
DEVICE_GROUPS = {
    'fire_detection': ['detector', 'smoke_detector', 'heat_detector',
                       'diff_detector', 'fixed_temp', 'photoelectric',
                       'fire_button', 'fire_panel', 'fire_alarm_sys'],
    'exit_light': ['exit_light', 'exit_sign'],
    'emergency_light': ['emergency_light'],
    'outlet': ['outlet', 'outlet_wp', 'outlet_dedicated'],
    'outlet_grounded': ['outlet_grounded'],
    'switch': ['switch', 'switch_3way'],
    'lighting': ['downlight', 'ceiling_light', 'bracket_light',
                 'fluorescent', 'led', 'hf_light', 'light_fixture'],
    'dist_board': ['dist_board', 'main_board'],
    'breaker_elcb': ['breaker', 'elcb'],
    'nurse_call': ['nurse_call'],
    'intercom': ['intercom'],
    'broadcast': ['broadcast'],
    'electric_lock': ['electric_lock'],
    'medical_ground': ['medical_ground'],
    'ups': ['ups'],
    'sprinkler': ['sprinkler'],
    'tv_common': ['tv_common'],
    'solar_power': ['solar_power', 'solar'],
    'telephone': ['telephone'],
    'generator': ['generator'],
    'substation': ['substation'],
    'hvac': ['hvac'],
    'ventilation': ['ventilation'],
    'smoke_exhaust': ['smoke_exhaust', 'smoke_control'],
    'lightning_rod': ['lightning_rod'],
    'power_supply': ['power_supply'],
    'auto_control': ['auto_control'],
    'itv': ['itv'],
    'lan': ['lan'],
}

# Drawing type classification
DRAWING_TYPES = {
    '電灯設備': 'lighting', '照明設備': 'lighting',
    '非常照明': 'emergency_light', '誘導灯': 'exit_light',
    'コンセント設備': 'outlet', 'コンセント壁': 'outlet',
    '火災報知': 'fire_alarm', '幹線': 'trunk_line',
    '動力': 'power', '分電盤': 'panel', '負荷表': 'panel',
    '放送設備': 'broadcast', 'ナースコール': 'nurse_call',
    'インターホン': 'intercom', '電気錠': 'electric_lock',
    'ITV': 'itv', 'LAN': 'lan', 'テレビ': 'tv',
    '電話': 'tel', '医療アース': 'medical_ground',
    '受変電': 'substation', '自家発電': 'generator',
    '太陽光': 'solar', '系統図': 'diagram', '結線図': 'diagram',
    '詳細図': 'detail', '姿図': 'detail', '病室': 'detail',
    '空調': 'hvac', '換気': 'ventilation', '排煙': 'smoke_exhaust',
}


# ============================================================
# 2. SCANNING
# ============================================================

def scan_keywords(data):
    devices = defaultdict(int)
    for encoded, device_type in _KW_TABLE:
        count = data.count(encoded)
        if count > 0:
            devices[device_type] = max(devices[device_type], count)
    return dict(devices)

def classify_drawing(filename):
    for kw, dtype in DRAWING_TYPES.items():
        if kw in filename:
            return dtype
    return 'other'

def extract_floor(filename):
    for p in ['1階', '2階', '3階', '4階', '5階', '6階', '7階', '8階', '9階', '10階',
              'R階', 'Ｒ階', 'ピット', 'B1', 'B2', '地下']:
        if p in filename:
            return p
    return ''

def aggregate_devices(keywords):
    groups = {}
    for group_name, device_types in DEVICE_GROUPS.items():
        total = sum(keywords.get(dt, 0) for dt in device_types)
        if total > 0:
            groups[group_name] = total
    return groups


# ============================================================
# 3. PROJECT PROFILING
# ============================================================

def build_profile(folder, filenames=None):
    """Build project profile entirely from data. No assumptions."""
    if filenames is None:
        filenames = sorted(f for f in os.listdir(folder) if f.lower().endswith('.jww'))

    all_kw = defaultdict(int)
    floors = set()
    by_dtype = defaultdict(lambda: {'count': 0, 'total_size': 0})
    total_size = 0

    for fname in filenames:
        fpath = os.path.join(folder, fname)
        with open(fpath, 'rb') as f:
            data = f.read()
        if not data.startswith(b'JwwData'):
            continue

        kw = scan_keywords(data)
        for k, v in kw.items():
            all_kw[k] += v

        fl = extract_floor(fname)
        if fl:
            floors.add(fl)

        dt = classify_drawing(fname)
        by_dtype[dt]['count'] += 1
        by_dtype[dt]['total_size'] += len(data)
        total_size += len(data)

    device_groups = aggregate_devices(all_kw)

    # Detect building features (not "type" — features!)
    features = set()
    bldg_types_found = []
    rooms = {}
    room_map = {
        'room_patient': '病室', 'room_surgery': '手術室', 'room_icu': 'ICU',
        'room_nurse_st': 'NS', 'room_kitchen': '厨房', 'room_corridor': '廊下',
        'room_stairs': '階段', 'room_nursery': '保育室', 'room_play': '遊戯室',
        'room_infant': '乳児室', 'room_office': '事務室', 'room_assembly': '集会室',
        'room_dwelling': '居室', 'room_classroom': '教室', 'room_guest': '客室',
        'room_lobby': 'ロビー', 'room_storage': '倉庫', 'room_mechanical': '機械室',
        'room_electrical': '電気室', 'room_server': 'サーバ室', 'room_parking': '駐車場',
        'room_bath': '浴室', 'room_pool': 'プール', 'room_gym': '体育館',
        'room_factory': '工場', 'room_workshop': '作業場', 'room_sales_floor': '売場',
    }

    for rt, label in room_map.items():
        count = all_kw.get(rt, 0)
        if count > 0:
            rooms[label] = count

    # Detect features from what's found
    # Medical features
    if any(all_kw.get(k, 0) > 0 for k in ['nurse_call', 'room_patient', 'room_surgery',
                                             'room_icu', 'room_nurse_st', 'medical_ground']):
        features.add('medical')
    # Childcare features
    if any(all_kw.get(k, 0) > 0 for k in ['room_nursery', 'room_play', 'room_infant',
                                             'bldg_nursery', 'bldg_kindergarten']):
        features.add('childcare')
    # Residential features
    if any(all_kw.get(k, 0) > 0 for k in ['room_dwelling', 'room_unit_type',
                                             'bldg_residential', 'tv_common']):
        features.add('residential')
    # Elderly care
    if any(all_kw.get(k, 0) > 0 for k in ['bldg_elderly']):
        features.add('elderly_care')
    # High-rise (many floors)
    if len(floors) > 5:
        features.add('highrise')
    # Kitchen/cooking present
    if all_kw.get('room_kitchen', 0) > 0:
        features.add('has_kitchen')
    # Server room
    if all_kw.get('room_server', 0) > 0:
        features.add('has_server_room')
    # Parking
    if all_kw.get('room_parking', 0) > 0:
        features.add('has_parking')
    # Large assembly
    if any(all_kw.get(k, 0) > 0 for k in ['room_assembly', 'room_lobby', 'bldg_theater']):
        features.add('assembly')

    # Building type label (for display, not for rules!)
    for k, v in all_kw.items():
        if k.startswith('bldg_') and v > 0:
            bldg_types_found.append(k.replace('bldg_', ''))

    if not bldg_types_found:
        # Guess from features
        if 'medical' in features:
            bldg_types_found.append('hospital')
        elif 'childcare' in features:
            bldg_types_found.append('nursery')
        elif 'residential' in features:
            bldg_types_found.append('residential')
        else:
            bldg_types_found.append('unknown')

    floor_count = max(len(floors), 1)
    total_rooms = sum(rooms.values())

    return {
        'folder': folder,
        'file_count': len(filenames),
        'filenames': filenames,
        'floors': sorted(floors),
        'floor_count': floor_count,
        'rooms': rooms,
        'total_rooms': total_rooms,
        'total_size': total_size,
        'keywords': dict(all_kw),
        'device_groups': device_groups,
        'by_drawing_type': dict(by_dtype),
        'features': features,
        'building_types': bldg_types_found,
        'building_label': '/'.join(bldg_types_found) or 'unknown',
    }


# ============================================================
# 4. FEATURE-BASED RULES ENGINE
# ============================================================

# Fix suggestions — what to do when a rule fails
FIX_SUGGESTIONS = {
    'fire_detection': {
        'action': '感知器の追加設置',
        'detail': '各室・廊下・階段に煙感知器または熱感知器を配置してください。'
                  '天井高4m以下→煙感知器、4m超→光電式分離型を検討。',
        'drawings': ['自動火災報知設備 平面図'],
        'standard': '各階ごとに、廊下・階段・各室に感知器が必要（消防法施行令第21条）。'
                    '感知器の警戒面積: 煙=150㎡/個、差動=70㎡/個、定温=70㎡/個。',
    },
    'exit_light': {
        'action': '誘導灯の追加設置',
        'detail': '避難口（出入口）の上部に避難口誘導灯、廊下に通路誘導灯を設置。'
                  '床面1ルクス以上を確保。',
        'drawings': ['誘導灯設備 平面図'],
        'standard': '避難口には必ず誘導灯を設置（消防法施行令第26条）。'
                    '廊下・通路には通路誘導灯を20m以下の間隔で設置。',
    },
    'emergency_light': {
        'action': '非常照明の追加設置',
        'detail': '居室・廊下・階段に非常用照明器具を設置。'
                  '床面1ルクス以上（蛍光灯は2ルクス以上）を30分間維持。',
        'drawings': ['非常照明設備 平面図'],
        'standard': '建築基準法施行令第126条の4に基づき、特殊建築物・3階以上の建物は必須。'
                    '直接照明で床面水平面照度1ルクス以上。',
    },
    'dist_board': {
        'action': '分電盤の確認・追加',
        'detail': '各階に最低1面の分電盤を設置。盤スケジュール表と単線結線図を確認。',
        'drawings': ['電灯分電盤負荷表', '幹線設備 系統図'],
        'standard': '電気設備技術基準に基づき、適切な容量の分電盤を各階に配置。',
    },
    'breaker_elcb': {
        'action': '漏電遮断器(ELCB)の確認',
        'detail': '水回り・屋外・医療機器回路にELCBを設置。'
                  '感度電流30mA以下、動作時間0.1秒以下。',
        'drawings': ['電灯分電盤負荷表', '幹線設備 系統図'],
        'standard': '電気設備技術基準第15条: 金属製外箱の機器、水気のある場所は漏電遮断器必須。',
    },
    'outlet': {
        'action': 'コンセントの追加配置',
        'detail': '各室にコンセントを配置。居室は壁面4mにつき1口以上が目安。'
                  '廊下は10m間隔、トイレ・洗面所にも設置。',
        'drawings': ['コンセント設備 平面図'],
        'standard': '内線規程に基づく適正配置。住宅: 居室は2口×2箇所以上。'
                    '病院: ベッド1台につき4口以上。',
    },
    'outlet_grounded': {
        'action': '接地極付コンセント(EET)の追加',
        'detail': '水回り（厨房・洗面・浴室）、OA機器用、医療機器用に'
                  '接地極付コンセントを設置。',
        'drawings': ['コンセント設備 平面図'],
        'standard': '内線規程3202節: 水気のある場所、大型機器には接地極付コンセント必須。',
    },
    'lighting': {
        'action': '照明器具の追加・配置見直し',
        'detail': '各室の用途に応じた照度を確保。事務室500lx、廊下100lx、'
                  '病室100lx、手術室1000lx以上。',
        'drawings': ['電灯設備 平面図', '照明器具姿図'],
        'standard': 'JIS Z 9110照度基準に基づき、用途別の推奨照度を確保。',
    },
    'nurse_call': {
        'action': 'ナースコールの追加設置',
        'detail': '全病室のベッドサイド、トイレ、浴室にナースコールを設置。'
                  'ナースステーションに親機を配置。',
        'drawings': ['ナースコール設備 平面図', 'ナースコール設備 系統図'],
        'standard': '医療法施行規則第16条: 病室にはナースコール装置を設ける。',
    },
    'broadcast': {
        'action': '非常放送設備の確認',
        'detail': '各階の廊下・ロビー・大部屋にスピーカーを設置。'
                  '非常放送アンプの容量を確認。',
        'drawings': ['非常放送設備 平面図', '非常放送設備 系統図'],
        'standard': '消防法施行令第24条: 収容人員50人以上の建物は非常警報設備必須。',
    },
    'medical_ground': {
        'action': '医療用接地の追加',
        'detail': '手術室・ICU・心カテ室に医療用等電位接地を設置。'
                  '接地抵抗10Ω以下。',
        'drawings': ['医療アース設置設備 平面図'],
        'standard': 'JIS T 1022: 医用電気機器の安全に関する接地要求。',
    },
    'ups': {
        'action': 'UPS/無停電電源の確認',
        'detail': '手術室・ICU・サーバ室の重要機器にUPSを接続。'
                  '停電時10分以上のバックアップ確保。',
        'drawings': ['幹線設備 系統図'],
        'standard': 'JIS T 1022: 医療施設の重要負荷には無停電電源装置を設ける。',
    },
    'sprinkler': {
        'action': 'スプリンクラーの確認',
        'detail': 'スプリンクラーヘッドの配置を確認。天井高3m以下で'
                  '有効散水半径2.3m（標準型）。',
        'drawings': ['スプリンクラー設備 平面図'],
        'standard': '消防法施行令第12条: 一定規模以上の特定防火対象物に設置義務。',
    },
    'switch': {
        'action': 'スイッチの配置確認',
        'detail': '各室の出入口にスイッチを設置。3路スイッチは廊下・階段の両端に配置。',
        'drawings': ['電灯設備 平面図'],
        'standard': '内線規程: 照明器具にはスイッチを設ける。',
    },
    'emergency_broadcast': {
        'action': '非常放送スピーカーの追加設置',
        'detail': '各階の廊下・ロビー・大部屋にスピーカーを設置。'
                  '非常放送アンプの容量を確認。',
        'drawings': ['非常放送設備 平面図', '非常放送設備 系統図'],
        'standard': '消防法施行令第24条: 収容人員50人以上の建物は非常警報設備必須。'
                    '各階ごとにスピーカーを配置し、25m以内で音声が到達すること。',
    },
    'smoke_exhaust': {
        'action': '排煙設備の確認',
        'detail': '各室500㎡以内ごとに排煙口を設置。排煙口は天井面から80cm以内。',
        'drawings': ['排煙設備 平面図'],
        'standard': '建築基準法施行令第126条の2: 特殊建築物は排煙設備を設ける。',
    },
}

# Device subtype keywords per drawing group (for detailed analysis)
SUBTYPE_KEYWORDS = {
    'outlet': {
        'EET': '接地極付ダブル(EET)', 'ET': '接地極付(ET)', '2PE': '2P接地付',
        'WP': '防水コンセント', '専用': '専用回路', '200V': '200V用',
        '20A': '20A', 'アース': 'アース付',
    },
    'fire_detection': {
        '煙': '煙感知器', '熱': '熱感知器', '差動': '差動式',
        '定温': '定温式', '光電': '光電式', '発信機': '発信機',
    },
    'exit_light': {
        '避難口': '避難口誘導灯', '通路': '通路誘導灯', '誘導灯信号': '信号装置',
    },
    'emergency_light': {
        'LED': 'LED非常灯', '蛍光': '蛍光灯型',
    },
    'lighting': {
        'LED': 'LED照明', 'Hf': 'Hf蛍光灯', 'ダウンライト': 'ダウンライト',
        'シーリング': 'シーリング', 'ブラケット': 'ブラケット',
        'ペンダント': 'ペンダント', '直管': '直管型',
    },
    'emergency_broadcast': {
        'スピーカ': 'スピーカー', 'アンプ': 'アンプ',
    },
    'nurse_call': {
        'ベッド': 'ベッドサイド', 'トイレ': 'トイレ用', '親機': '親機',
    },
}

# Default suggestion for groups not in FIX_SUGGESTIONS
DEFAULT_FIX = {
    'action': '設備の配置・数量を確認',
    'detail': '図面上の設備数量が基準値を下回っています。設計内容を再確認してください。',
    'drawings': ['該当設備 平面図'],
    'standard': '関連法令・基準に基づき確認。',
}

# Japanese names for device groups
GROUP_NAMES_JA = {
    'fire_detection': '火災感知器', 'exit_light': '誘導灯',
    'emergency_light': '非常照明', 'dist_board': '分電盤',
    'breaker_elcb': '漏電遮断器(ELCB)', 'outlet': 'コンセント',
    'outlet_grounded': '接地極付コンセント', 'lighting': '照明設備',
    'switch': 'スイッチ', 'nurse_call': 'ナースコール',
    'broadcast': '非常放送', 'medical_ground': '医療用接地',
    'ups': 'UPS/無停電電源', 'intercom': 'インターホン',
    'electric_lock': '電気錠', 'sprinkler': 'スプリンクラー',
    'tv_common': 'テレビ共聴', 'telephone': '電話設備',
    'solar_power': '太陽光発電', 'generator': '自家発電',
    'substation': '受変電設備', 'hvac': '空調設備',
    'ventilation': '換気設備', 'smoke_exhaust': '排煙設備',
    'lightning_rod': '避雷設備', 'power_supply': '動力設備',
    'auto_control': '自動制御', 'itv': 'ITV監視',
    'lan': 'LAN設備', 'emergency_broadcast': '非常放送',
    'hvac_power': '空調電源', 'vent_power': '換気電源',
    'trunk_line': '幹線設備', 'power': '動力設備',
    'spo2': 'SpO2モニタ', 'pacs': 'PACS設備',
    'tv': 'テレビ共聴', 'solar': '太陽光発電',
}


def generate_rules(profile, knowledge=None):
    """
    Generate rules from features. THREE layers:

    Layer 1: Universal (applies to ALL buildings)
    Layer 2: Feature-triggered (only if relevant equipment found)
    Layer 3: Knowledge-adjusted (from past projects)
    """
    rules = []
    floor_count = profile['floor_count']
    total_rooms = profile['total_rooms']
    features = profile['features']
    device_groups = profile['device_groups']

    def add_rule(group, severity, law, min_count=1, layer='universal',
                 condition=None, geometric=False):
        """Helper to add a rule."""
        # Scale adjustment
        scaled_min = min_count
        if group in ['fire_detection', 'lighting', 'outlet']:
            scaled_min = max(min_count, floor_count * 2)
            if total_rooms > 10:
                scaled_min = max(scaled_min, total_rooms // 5)
        elif group in ['exit_light', 'emergency_light', 'broadcast']:
            scaled_min = max(min_count, floor_count)
        elif group in ['dist_board', 'breaker_elcb']:
            scaled_min = max(min_count, floor_count)

        # Knowledge adjustment
        if knowledge:
            bl = knowledge.get('baselines', {})
            # Find closest matching building type
            for bt in profile['building_types']:
                if bt in bl and group in bl[bt]:
                    per_floor = bl[bt][group].get('per_floor', 0)
                    if per_floor > 0:
                        learned_min = int(per_floor * floor_count * 0.5)
                        scaled_min = max(scaled_min, learned_min)
                    break

        rules.append({
            'id': f'R{len(rules)+1:02d}',
            'name': GROUP_NAMES_JA.get(group, group),
            'group': group,
            'min_count': scaled_min,
            'severity': severity,
            'law': law,
            'layer': layer,
            'geometric_check': group if geometric else None,
        })

    # ─── Layer 1: UNIVERSAL RULES (全建物共通) ───
    add_rule('fire_detection', 'CRITICAL', '消防法施行令第21条', 1, 'universal')
    add_rule('exit_light', 'CRITICAL', '消防法施行令第26条', 1, 'universal', geometric=True)
    add_rule('emergency_light', 'CRITICAL', '建築基準法施行令第126条の4', 1, 'universal', geometric=True)
    add_rule('dist_board', 'HIGH', '電気設備技術基準', 1, 'universal')
    add_rule('breaker_elcb', 'HIGH', '電気設備技術基準第15条', 1, 'universal')
    add_rule('outlet', 'HIGH', '内線規程', 1, 'universal', geometric=True)
    add_rule('lighting', 'HIGH', '建築基準法第28条', 1, 'universal', geometric=True)

    # ─── Layer 2: FEATURE-TRIGGERED RULES ───
    # These rules ONLY appear if the system detects relevant equipment/rooms

    # Grounded outlets — if ANY grounded outlet found, check quantity
    if device_groups.get('outlet_grounded', 0) > 0:
        min_g = 3 if 'medical' in features else 1
        add_rule('outlet_grounded', 'HIGH', '内線規程3202節', min_g, 'feature')

    # Nurse call — only for medical facilities
    if device_groups.get('nurse_call', 0) > 0:
        patient_rooms = profile['rooms'].get('病室', 0)
        min_nc = max(1, patient_rooms // 3) if patient_rooms > 0 else 1
        add_rule('nurse_call', 'CRITICAL', '医療法施行規則第16条', min_nc, 'feature')

    # Emergency broadcast — if detected
    if device_groups.get('broadcast', 0) > 0:
        add_rule('broadcast', 'HIGH', '消防法施行令第24条', 1, 'feature')

    # Medical ground — if detected
    if device_groups.get('medical_ground', 0) > 0:
        add_rule('medical_ground', 'HIGH', 'JIS T 1022', 1, 'feature')

    # UPS — if detected
    if device_groups.get('ups', 0) > 0:
        add_rule('ups', 'HIGH', 'JIS T 1022', 1, 'feature')

    # Intercom — if detected
    if device_groups.get('intercom', 0) > 0:
        add_rule('intercom', 'MEDIUM', '建築設計標準', 1, 'feature')

    # Electric lock — if detected
    if device_groups.get('electric_lock', 0) > 0:
        add_rule('electric_lock', 'MEDIUM', '建築設計標準', 1, 'feature')

    # Sprinkler — if detected
    if device_groups.get('sprinkler', 0) > 0:
        add_rule('sprinkler', 'HIGH', '消防法施行令第12条', 1, 'feature')

    # TV common — if detected
    if device_groups.get('tv_common', 0) > 0:
        add_rule('tv_common', 'MEDIUM', '共聴設備設計基準', 1, 'feature')

    # Telephone — if detected
    if device_groups.get('telephone', 0) > 0:
        add_rule('telephone', 'MEDIUM', '電気通信事業法', 1, 'feature')

    # Solar power — if detected
    if device_groups.get('solar_power', 0) > 0:
        add_rule('solar_power', 'MEDIUM', '建築物省エネ法', 1, 'feature')

    # Generator — if detected
    if device_groups.get('generator', 0) > 0:
        add_rule('generator', 'HIGH', '建築基準法施行令第123条', 1, 'feature')

    # Substation — if detected
    if device_groups.get('substation', 0) > 0:
        add_rule('substation', 'HIGH', '電気事業法', 1, 'feature')

    # Switch — if detected
    if device_groups.get('switch', 0) > 0:
        add_rule('switch', 'MEDIUM', '内線規程', 1, 'feature')

    # Smoke exhaust — if detected
    if device_groups.get('smoke_exhaust', 0) > 0:
        add_rule('smoke_exhaust', 'HIGH', '建築基準法施行令第126条の2', 1, 'feature')

    # ITV — if detected
    if device_groups.get('itv', 0) > 0:
        add_rule('itv', 'MEDIUM', '建築設計標準', 1, 'feature')

    # LAN — if detected
    if device_groups.get('lan', 0) > 0:
        add_rule('lan', 'MEDIUM', '情報通信設備基準', 1, 'feature')

    return rules


# ============================================================
# 5. EVALUATION
# ============================================================

def evaluate_rules(rules, device_groups, geo_deltas=None, before_groups=None):
    """
    Evaluate rules against device counts.
    If before_groups is provided, also checks delta (improvement from before→after).
    """
    results = []
    for rule in rules:
        group = rule['group']
        count = device_groups.get(group, 0)
        min_count = rule['min_count']

        geo_status = None
        if geo_deltas and rule.get('geometric_check'):
            geo = geo_deltas.get(rule['geometric_check'])
            if geo:
                geo_status = {'size_delta': geo.get('size_delta', 0),
                              'size_pct': geo.get('size_pct', 0)}

        if count >= min_count:
            status = 'PASS'
        elif count > 0:
            status = 'MARGINAL' if count >= min_count * 0.7 else 'WARN'
        else:
            if geo_status and geo_status['size_delta'] > 1000:
                status = 'PASS_GEO'
            else:
                status = 'FAIL'

        results.append({'rule': rule, 'count': count, 'status': status, 'geo': geo_status})
    return results


def evaluate_delta(before_groups, after_groups, geo_deltas=None):
    """
    Layer 3: DELTA CHECK — did the correction actually improve things?

    This checks: for each device group present, did the count increase
    from before→after? If not, flags it as NO_CHANGE.

    Critical devices (fire, exit, emergency) MUST increase.
    Other devices: flag as info if unchanged.
    """
    CRITICAL_GROUPS = {
        'fire_detection': ('火災感知器', 'CRITICAL', '消防法施行令第21条'),
        'exit_light': ('誘導灯', 'CRITICAL', '消防法施行令第26条'),
        'emergency_light': ('非常照明', 'CRITICAL', '建築基準法施行令第126条の4'),
        'dist_board': ('分電盤', 'HIGH', '電気設備技術基準'),
        'breaker_elcb': ('漏電遮断器(ELCB)', 'HIGH', '電気設備技術基準第15条'),
        'outlet': ('コンセント', 'HIGH', '内線規程'),
        'outlet_grounded': ('接地極付コンセント', 'HIGH', '内線規程3202節'),
        'lighting': ('照明設備', 'HIGH', '建築基準法第28条'),
    }

    delta_results = []
    all_groups = sorted(set(before_groups.keys()) | set(after_groups.keys()))

    for group in all_groups:
        before = before_groups.get(group, 0)
        after = after_groups.get(group, 0)
        delta = after - before

        name = GROUP_NAMES_JA.get(group, group)

        # Check geometric delta too
        geo_delta = 0
        if geo_deltas and group in geo_deltas:
            geo_delta = geo_deltas[group].get('size_delta', 0)

        if group in CRITICAL_GROUPS:
            cname, severity, law = CRITICAL_GROUPS[group]
            if delta > 0:
                status = 'IMPROVED'
            elif geo_delta > 5000:
                status = 'IMPROVED_GEO'
            elif delta == 0 and before > 0:
                status = 'NO_CHANGE'
            elif delta < 0:
                status = 'DECREASED'
            else:
                status = 'NO_CHANGE'
        else:
            severity = 'INFO'
            law = ''
            if delta > 0:
                status = 'IMPROVED'
            elif geo_delta > 5000:
                status = 'IMPROVED_GEO'
            elif delta == 0:
                status = 'NO_CHANGE'
            else:
                status = 'DECREASED'

        delta_results.append({
            'group': group,
            'name': name,
            'severity': severity if group in CRITICAL_GROUPS else 'INFO',
            'law': law if group in CRITICAL_GROUPS else '',
            'before': before,
            'after': after,
            'delta': delta,
            'geo_delta': geo_delta,
            'status': status,
        })

    return delta_results


def compute_geo_deltas(before_profile, after_profile):
    deltas = {}
    type_to_group = {
        'exit_light': 'exit_light', 'emergency_light': 'emergency_light',
        'outlet': 'outlet', 'lighting': 'lighting',
        'fire_alarm': 'fire_detection', 'nurse_call': 'nurse_call',
        'broadcast': 'broadcast', 'medical_ground': 'medical_ground',
    }
    b_types = before_profile['by_drawing_type']
    a_types = after_profile['by_drawing_type']
    for dtype in set(b_types.keys()) | set(a_types.keys()):
        group = type_to_group.get(dtype)
        if not group:
            continue
        b_size = b_types.get(dtype, {}).get('total_size', 0)
        a_size = a_types.get(dtype, {}).get('total_size', 0)
        delta = a_size - b_size
        pct = (delta / b_size * 100) if b_size > 0 else 0
        deltas[group] = {'size_delta': delta, 'size_pct': pct}
    return deltas


# ============================================================
# 6. VISUAL ANALYSIS (Cấp 3 — 図面座標ベース検図)
# ============================================================

DRAWING_TYPES_VISUAL = {
    '電灯設備': 'lighting', '照明設備': 'lighting',
    'コンセント設備': 'outlet', 'コンセント壁': 'outlet',
    '自動火災報知': 'fire_detection', '火災報知': 'fire_detection',
    '誘導灯': 'exit_light', '非常照明': 'emergency_light',
    '非常放送': 'emergency_broadcast', 'ナースコール': 'nurse_call',
    'インターホン': 'intercom', '電気錠': 'electric_lock',
    'LAN設備': 'lan', 'PACS': 'pacs', '電話設備': 'telephone',
    '幹線': 'trunk_line', '動力': 'power',
    '空調電源': 'hvac_power', '換気電源': 'vent_power',
    '医療アース': 'medical_ground', '自動制御': 'auto_control',
    'テレビ': 'tv', 'ITV': 'itv', 'サーチュレーション': 'spo2',
    '太陽光': 'solar',
}

CRITICAL_VISUAL_GROUPS = {
    'fire_detection', 'exit_light', 'emergency_light',
    'lighting', 'outlet', 'emergency_broadcast',
}


def extract_device_subtypes(filepath, group):
    """Extract device subtype counts from text in JWW file.
    Uses SUBTYPE_KEYWORDS to identify specific device types."""
    keywords = SUBTYPE_KEYWORDS.get(group)
    if not keywords:
        return {}
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
    except (IOError, OSError):
        return {}
    if not data.startswith(b'JwwData'):
        return {}

    # Quick scan: count keyword occurrences in Shift_JIS decoded text
    # Decode entire file as shift_jis (lossy but fast)
    try:
        text_blob = data.decode('shift_jis', errors='ignore')
    except Exception:
        return {}

    counts = {}
    for kw, label in keywords.items():
        c = text_blob.count(kw)
        if c > 0:
            counts[label] = c
    return counts


def extract_coordinate_lines(filepath, device_layers_only=False, max_lines=10000):
    """Extract line coordinates from JWW binary for visual analysis."""
    DEVICE_LAYERS = {8, 9}
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
    except (IOError, OSError):
        return []
    if not data.startswith(b'JwwData'):
        return []
    lines = []
    i = 256
    data_len = len(data)
    while i < data_len - 50 and len(lines) < max_lines:
        if data[i] == 0x02 and data[i+1:i+4] == b'\x00\x00\x00':
            off = i + 10
            if off + 32 <= data_len:
                try:
                    x1, y1, x2, y2 = struct.unpack_from('<4d', data, off)
                    if (not any(math.isnan(v) or math.isinf(v) for v in [x1,y1,x2,y2]) and
                        all(abs(v) < 50000 for v in [x1,y1,x2,y2]) and
                        not (abs(x1-x2) < 0.001 and abs(y1-y2) < 0.001)):
                        layer = data[i+4]
                        if device_layers_only and layer not in DEVICE_LAYERS:
                            i += 42
                            continue
                        length = math.sqrt((x2-x1)**2 + (y2-y1)**2)
                        lines.append({'x1':x1,'y1':y1,'x2':x2,'y2':y2,
                                      'layer':layer,'length':length})
                        i += 42
                        continue
                except Exception:
                    pass
        i += 1
    return lines


def find_symbols(lines, max_sym_size=20):
    """Find device symbols by clustering short lines. Returns symbol positions + shapes."""
    short = [l for l in lines if l['length'] < max_sym_size]
    if not short:
        return []

    CELL = max_sym_size
    grid = {}
    for idx, l in enumerate(short):
        cx = (l['x1'] + l['x2']) / 2
        cy = (l['y1'] + l['y2']) / 2
        key = (int(cx / CELL), int(cy / CELL))
        if key not in grid:
            grid[key] = []
        grid[key].append(idx)

    used = set()
    symbols = []
    for gx, gy in grid:
        all_idx = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in grid.get((gx+dx, gy+dy), []):
                    if i not in used:
                        all_idx.append(i)
        if len(all_idx) < 2:
            continue
        grp = [short[i] for i in all_idx]
        xs = [l['x1'] for l in grp] + [l['x2'] for l in grp]
        ys = [l['y1'] for l in grp] + [l['y2'] for l in grp]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if w > 30 or h > 30:
            continue
        for i in all_idx:
            used.add(i)
        n = len(grp)
        aspect = w / h if h > 0.5 else 99
        if n >= 6 and 0.5 < aspect < 2.0:
            shape = 'CIRCLE'
        elif n == 3 and 0.5 < aspect < 2.0 and w < 15:
            shape = 'TRIANGLE'
        elif n == 4 and 0.5 < aspect < 2.0 and w < 15:
            shape = 'SQUARE'
        elif n == 2:
            shape = 'CROSS'
        else:
            shape = 'CLUSTER'
        symbols.append({
            'cx': sum(xs)/len(xs), 'cy': sum(ys)/len(ys),
            'w': w, 'h': h, 'n_lines': n, 'shape': shape,
        })
    return symbols


def visual_zone_analysis(before_lines, after_lines, grid_size=50):
    """Compare line density per zone between before and after."""
    if not after_lines:
        return None
    all_x = [l['x1'] for l in after_lines] + [l['x2'] for l in after_lines]
    all_y = [l['y1'] for l in after_lines] + [l['y2'] for l in after_lines]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    nx = max(1, int((max_x - min_x) / grid_size) + 1)
    ny = max(1, int((max_y - min_y) / grid_size) + 1)

    def density(lines):
        d = {}
        for l in lines:
            steps = max(1, int(l['length'] / (grid_size / 2)))
            for s in range(steps + 1):
                t = s / steps
                x = l['x1'] + t * (l['x2'] - l['x1'])
                y = l['y1'] + t * (l['y2'] - l['y1'])
                k = (min(nx-1, max(0, int((x - min_x) / grid_size))),
                     min(ny-1, max(0, int((y - min_y) / grid_size))))
                d[k] = d.get(k, 0) + 1
        return d

    bd = density(before_lines)
    ad = density(after_lines)

    improved = 0
    unchanged = 0
    empty = 0
    for gx in range(nx):
        for gy in range(ny):
            a = ad.get((gx, gy), 0)
            b = bd.get((gx, gy), 0)
            if a < 3:
                empty += 1
            elif a - b > 0:
                improved += 1
            else:
                unchanged += 1

    mid_x = (min_x + max_x) / 2
    mid_y = (min_y + max_y) / 2
    # Describe unchanged zone locations
    regions = {}
    for gx in range(nx):
        for gy in range(ny):
            a = ad.get((gx, gy), 0)
            b = bd.get((gx, gy), 0)
            if a >= 3 and a - b <= 0:
                cx = min_x + (gx + 0.5) * grid_size
                cy = min_y + (gy + 0.5) * grid_size
                r = ('北' if cy > mid_y else '南') + ('東' if cx > mid_x else '西')
                regions[r] = regions.get(r, 0) + 1

    return {
        'improved': improved, 'unchanged': unchanged, 'empty': empty,
        'total_zones': nx * ny,
        'unchanged_regions': regions,
    }


def run_visual_check(before_folder, after_folder):
    """Run visual analysis on all drawing pairs. Returns per-file results + suggestions."""
    before_files = {f: os.path.join(before_folder, f)
                    for f in os.listdir(before_folder) if f.lower().endswith('.jww')}
    after_files = {f: os.path.join(after_folder, f)
                   for f in os.listdir(after_folder) if f.lower().endswith('.jww')}
    common = sorted(set(before_files) & set(after_files))

    results = []
    for fname in common:
        # Classify drawing
        group = None
        for keyword, grp in DRAWING_TYPES_VISUAL.items():
            if keyword in fname:
                group = grp
                break
        if not group:
            continue
        # Detect floor
        floor = None
        for pat, fid in [('ピット','PIT'),('R階','RF'),('Ｒ階','RF'),
                         ('1階','1F'),('2階','2F'),('3階','3F'),('4階','4F'),('5階','5F'),
                         ('１階','1F'),('２階','2F'),('３階','3F')]:
            if pat in fname:
                floor = fid
                break
        if not floor:
            continue
        if '平面図' not in fname and '詳細図' not in fname:
            continue

        # Extract lines
        a_all = extract_coordinate_lines(after_files[fname])
        if len(a_all) < 5:
            continue
        b_dev = extract_coordinate_lines(before_files[fname], device_layers_only=True)
        a_dev = extract_coordinate_lines(after_files[fname], device_layers_only=True)

        b_size = os.path.getsize(before_files[fname])
        a_size = os.path.getsize(after_files[fname])
        size_pct = (a_size - b_size) / b_size * 100 if b_size > 0 else 0

        if len(a_dev) < 10 and size_pct > 0.5:
            b_dev = extract_coordinate_lines(before_files[fname])
            a_dev = a_all

        if len(a_dev) < 5:
            continue

        # Zone analysis
        zones = visual_zone_analysis(b_dev, a_dev)
        if not zones:
            continue

        # Symbol detection (on after file, device layers)
        dev_lines = extract_coordinate_lines(after_files[fname], device_layers_only=True)
        if len(dev_lines) < 5:
            dev_lines = a_all
        syms = find_symbols(dev_lines)

        results.append({
            'filename': fname,
            'group': group,
            'floor': floor,
            'group_label': GROUP_NAMES_JA.get(group, group),
            'before_lines': len(b_dev),
            'after_lines': len(a_dev),
            'delta_lines': len(a_dev) - len(b_dev),
            'size_delta_pct': size_pct,
            'zones': zones,
            'n_symbols': len(syms),
            'symbol_shapes': {},
        })
        from collections import Counter
        results[-1]['symbol_shapes'] = dict(Counter(s['shape'] for s in syms))

    # Generate suggestions
    suggestions = []
    for r in results:
        z = r['zones']
        is_critical = r['group'] in CRITICAL_VISUAL_GROUPS
        truly_no_change = (r['delta_lines'] < 5 and r['size_delta_pct'] < 1)

        if truly_no_change:
            suggestions.append({
                'severity': 'CRITICAL' if is_critical else 'WARNING',
                'floor': r['floor'], 'group': r['group'], 'label': r['group_label'],
                'filename': r['filename'], 'type': 'NO_CHANGE',
                'message': f'{r["floor"]} {r["group_label"]}：修正前後で変化なし',
                'action': f'{r["group_label"]}の図面を確認し、必要な設備を追加してください',
                'regions': {},
            })
        elif z['unchanged'] > 0 and z['improved'] > 0:
            pct = z['improved'] / (z['improved'] + z['unchanged']) * 100
            if pct < 80 and is_critical:
                reg_str = '・'.join(f'{k}({v}区画)' for k, v in
                    sorted(z['unchanged_regions'].items(), key=lambda x: -x[1])[:3])
                suggestions.append({
                    'severity': 'WARNING',
                    'floor': r['floor'], 'group': r['group'], 'label': r['group_label'],
                    'filename': r['filename'], 'type': 'PARTIAL_FIX',
                    'message': f'{r["floor"]} {r["group_label"]}：{pct:.0f}%修正済み。未修正：{reg_str}',
                    'action': f'{reg_str}の{r["group_label"]}を確認・追加してください',
                    'regions': z['unchanged_regions'],
                    'pct': pct,
                })
        else:
            suggestions.append({
                'severity': 'PASS',
                'floor': r['floor'], 'group': r['group'], 'label': r['group_label'],
                'filename': r['filename'], 'type': 'FULLY_FIXED',
                'message': f'{r["floor"]} {r["group_label"]}：全エリア修正確認済み',
                'action': '',
                'regions': {},
            })

    sev_order = {'CRITICAL': 0, 'WARNING': 1, 'PASS': 2}
    suggestions.sort(key=lambda s: (sev_order.get(s['severity'], 9), s['floor']))
    return results, suggestions


def run_visual_scan(folder):
    """
    Visual analysis for SINGLE folder (main mode).
    Analyzes each floor drawing:
    - Count device symbols per zone
    - Detect empty zones (potential missing devices)
    - Check device density vs expected thresholds
    Returns per-file results + error/suggestion list.
    """
    files = {f: os.path.join(folder, f)
             for f in os.listdir(folder) if f.lower().endswith('.jww')}

    # Expected minimum symbols per floor for critical groups
    # Based on typical Japanese electrical standards
    MIN_SYMBOLS_PER_FLOOR = {
        'fire_detection': 8,   # 感知器: at least ~8 per floor (depends on rooms)
        'exit_light': 3,       # 誘導灯: at least ~3 per floor (exits + corridors)
        'emergency_light': 5,  # 非常照明: at least ~5 per floor
        'lighting': 10,        # 照明: at least ~10 per floor
        'outlet': 8,           # コンセント: at least ~8 per floor
        'emergency_broadcast': 3,  # 非常放送: at least ~3 per floor
    }

    # Min density: at least X% of zones should have device lines
    MIN_COVERAGE_PCT = {
        'fire_detection': 30,
        'exit_light': 10,
        'emergency_light': 15,
        'lighting': 25,
        'outlet': 20,
        'emergency_broadcast': 10,
    }

    results = []
    for fname, fpath in sorted(files.items()):
        # Classify drawing
        group = None
        for keyword, grp in DRAWING_TYPES_VISUAL.items():
            if keyword in fname:
                group = grp
                break
        if not group:
            continue
        # Detect floor
        floor = None
        for pat, fid in [('ピット','PIT'),('R階','RF'),('Ｒ階','RF'),
                         ('1階','1F'),('2階','2F'),('3階','3F'),('4階','4F'),('5階','5F'),
                         ('１階','1F'),('２階','2F'),('３階','3F')]:
            if pat in fname:
                floor = fid
                break
        if not floor:
            continue
        if '平面図' not in fname and '詳細図' not in fname:
            continue

        # Extract device layer lines
        dev_lines = extract_coordinate_lines(fpath, device_layers_only=True)
        all_lines = None
        if len(dev_lines) < 10:
            all_lines = extract_coordinate_lines(fpath)
            if len(all_lines) < 5:
                continue
            dev_lines = all_lines

        # Zone density analysis (single file)
        zone_info = single_zone_analysis(dev_lines)
        if not zone_info:
            continue

        # Symbol detection
        syms = find_symbols(dev_lines)

        from collections import Counter
        shape_counts = dict(Counter(s['shape'] for s in syms))

        # Extract device subtypes from text
        subtypes = extract_device_subtypes(fpath, group)

        results.append({
            'filename': fname,
            'group': group,
            'floor': floor,
            'group_label': GROUP_NAMES_JA.get(group, group),
            'n_lines': len(dev_lines),
            'n_symbols': len(syms),
            'symbol_shapes': shape_counts,
            'zones': zone_info,
            'subtypes': subtypes,
        })

    # Generate errors and suggestions
    errors = []
    for r in results:
        z = r['zones']
        is_critical = r['group'] in CRITICAL_VISUAL_GROUPS
        group_label = r['group_label']
        floor = r['floor']

        # Check 1: Symbol count vs minimum
        min_sym = MIN_SYMBOLS_PER_FLOOR.get(r['group'], 0)
        # RF/PIT floors have much fewer devices — reduce threshold
        if floor in ('RF', 'PIT') and min_sym > 0:
            min_sym = max(1, min_sym // 4)
        if min_sym > 0 and r['n_symbols'] < min_sym:
            severity = 'CRITICAL' if is_critical and r['n_symbols'] == 0 else \
                       'WARNING' if is_critical else 'INFO'
            fix = FIX_SUGGESTIONS.get(r['group'], DEFAULT_FIX)
            errors.append({
                'severity': severity,
                'floor': floor, 'group': r['group'], 'label': group_label,
                'filename': r['filename'], 'type': 'LOW_SYMBOL_COUNT',
                'message': f"{floor} {group_label}：シンボル数不足（検出={r['n_symbols']}, "
                           f"期待≧{min_sym}）",
                'action': fix['action'] + f"（{fix['detail'][:60]}）",
                'standard': fix.get('standard', ''),
                'drawings': fix.get('drawings', []),
            })

        # Check 2: Zone coverage — are there large empty areas?
        min_cov = MIN_COVERAGE_PCT.get(r['group'], 0)
        if min_cov > 0 and z['coverage_pct'] < min_cov:
            severity = 'WARNING' if is_critical else 'INFO'
            # Describe which areas are empty
            empty_desc = '・'.join(f"{k}({v}区画)" for k, v in
                sorted(z['empty_regions'].items(), key=lambda x: -x[1])[:3])
            fix = FIX_SUGGESTIONS.get(r['group'], DEFAULT_FIX)
            errors.append({
                'severity': severity,
                'floor': floor, 'group': r['group'], 'label': group_label,
                'filename': r['filename'], 'type': 'LOW_COVERAGE',
                'message': f"{floor} {group_label}：設備カバー率不足（{z['coverage_pct']:.0f}%, "
                           f"期待≧{min_cov}%）。空白エリア：{empty_desc}",
                'action': f"{empty_desc}に{group_label}を追加してください",
                'standard': fix.get('standard', ''),
                'drawings': fix.get('drawings', []),
            })

        # Check 3: If no issues, mark as OK
        has_error = any(e['filename'] == r['filename'] for e in errors)
        if not has_error:
            errors.append({
                'severity': 'PASS',
                'floor': floor, 'group': r['group'], 'label': group_label,
                'filename': r['filename'], 'type': 'OK',
                'message': f"{floor} {group_label}：OK（シンボル{r['n_symbols']}個, "
                           f"カバー率{z['coverage_pct']:.0f}%）",
                'action': '',
                'standard': '',
                'drawings': [],
            })

    sev_order = {'CRITICAL': 0, 'WARNING': 1, 'INFO': 2, 'PASS': 3}
    errors.sort(key=lambda e: (sev_order.get(e['severity'], 9), e['floor']))
    return results, errors


def single_zone_analysis(lines, grid_size=50):
    """Analyze device distribution across zones for a single drawing."""
    if not lines or len(lines) < 3:
        return None
    all_x = [l['x1'] for l in lines] + [l['x2'] for l in lines]
    all_y = [l['y1'] for l in lines] + [l['y2'] for l in lines]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    # Skip if drawing area is too small
    if max_x - min_x < grid_size or max_y - min_y < grid_size:
        return None
    nx = max(1, int((max_x - min_x) / grid_size) + 1)
    ny = max(1, int((max_y - min_y) / grid_size) + 1)

    density = {}
    for l in lines:
        steps = max(1, int(l['length'] / (grid_size / 2)))
        for s in range(steps + 1):
            t = s / steps
            x = l['x1'] + t * (l['x2'] - l['x1'])
            y = l['y1'] + t * (l['y2'] - l['y1'])
            k = (min(nx-1, max(0, int((x - min_x) / grid_size))),
                 min(ny-1, max(0, int((y - min_y) / grid_size))))
            density[k] = density.get(k, 0) + 1

    occupied = 0
    empty = 0
    mid_x = (min_x + max_x) / 2
    mid_y = (min_y + max_y) / 2
    empty_regions = {}

    for gx in range(nx):
        for gy in range(ny):
            d = density.get((gx, gy), 0)
            if d >= 3:
                occupied += 1
            else:
                empty += 1
                cx = min_x + (gx + 0.5) * grid_size
                cy = min_y + (gy + 0.5) * grid_size
                r = ('北' if cy > mid_y else '南') + ('東' if cx > mid_x else '西')
                empty_regions[r] = empty_regions.get(r, 0) + 1

    total = nx * ny
    coverage_pct = occupied / total * 100 if total > 0 else 0

    return {
        'occupied': occupied, 'empty': empty, 'total_zones': total,
        'coverage_pct': coverage_pct, 'empty_regions': empty_regions,
    }


def print_visual_results(results, suggestions):
    """Print PART C visual analysis results (for validate mode)."""
    n_crit = sum(1 for s in suggestions if s['severity'] == 'CRITICAL')
    n_warn = sum(1 for s in suggestions if s['severity'] == 'WARNING')
    n_pass = sum(1 for s in suggestions if s['severity'] == 'PASS')

    print(f"\n    図面分析: {len(results)}図面 → CRITICAL:{n_crit} WARNING:{n_warn} PASS:{n_pass}")

    for s in suggestions:
        if s['severity'] == 'PASS':
            continue
        icon = '🔴' if s['severity'] == 'CRITICAL' else '🟡'
        print(f"    {icon} [{s['severity']}] {s['message']}")
        if s['action']:
            print(f"       💡 {s['action']}")

    # Show symbol counts per file (top interesting ones)
    sym_files = [r for r in results if r['n_symbols'] > 0]
    if sym_files:
        print(f"\n    検出シンボル:")
        for r in sorted(sym_files, key=lambda x: -x['n_symbols'])[:8]:
            shapes = r['symbol_shapes']
            shape_str = ' '.join(f'{k}:{v}' for k, v in sorted(shapes.items(), key=lambda x: -x[1]))
            print(f"      {r['floor']} {r['group_label']}: {r['n_symbols']}個 ({shape_str})")

    return {'n_critical': n_crit, 'n_warning': n_warn, 'n_pass': n_pass}


def print_scan_visual(results, errors):
    """Print visual scan results (for scan mode — single folder)."""
    n_crit = sum(1 for e in errors if e['severity'] == 'CRITICAL')
    n_warn = sum(1 for e in errors if e['severity'] == 'WARNING')
    n_info = sum(1 for e in errors if e['severity'] == 'INFO')
    n_pass = sum(1 for e in errors if e['severity'] == 'PASS')

    print(f"\n    図面分析: {len(results)}図面チェック")
    print(f"    結果: CRITICAL:{n_crit} WARNING:{n_warn} INFO:{n_info} PASS:{n_pass}")

    # Print errors/warnings first
    for e in errors:
        if e['severity'] == 'PASS':
            continue
        icon = {'CRITICAL': '❌', 'WARNING': '⚠️', 'INFO': 'ℹ️'}.get(e['severity'], '?')
        print(f"\n    {icon} [{e['severity']}] {e['message']}")
        if e['action']:
            print(f"       💡 対応: {e['action']}")
        if e.get('standard'):
            print(f"       📖 根拠: {e['standard'][:80]}")
        if e.get('drawings'):
            print(f"       📄 確認図面: {', '.join(e['drawings'])}")

    # Summary of symbols with subtypes
    sym_files = [r for r in results if r['n_symbols'] > 0 or r.get('subtypes')]
    if sym_files:
        print(f"\n    検出シンボル・設備内訳:")
        # Group by device type for cleaner output
        by_group = {}
        for r in sym_files:
            g = r['group']
            if g not in by_group:
                by_group[g] = []
            by_group[g].append(r)

        for g, items in sorted(by_group.items()):
            label = GROUP_NAMES_JA.get(g, g)
            print(f"\n      【{label}】")
            for r in sorted(items, key=lambda x: x['floor']):
                cov = r['zones']['coverage_pct'] if r['zones'] else 0
                line = f"        {r['floor']}: シンボル{r['n_symbols']:>3}個 カバー率{cov:>3.0f}%"
                subtypes = r.get('subtypes', {})
                if subtypes:
                    sub_str = ', '.join(f'{k}:{v}' for k, v in
                        sorted(subtypes.items(), key=lambda x: -x[1])[:4])
                    line += f"  内訳: {sub_str}"
                print(line)

    return {'n_critical': n_crit, 'n_warning': n_warn, 'n_info': n_info, 'n_pass': n_pass}


# ============================================================
# 7. KNOWLEDGE BASE
# ============================================================

DEFAULT_KB_PATH = os.path.expanduser('~/.cad_checker_knowledge.json')

def load_knowledge(path=None):
    path = path or DEFAULT_KB_PATH
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            kb = json.load(f)
        # Only accept v3 knowledge base (feature-based)
        if kb.get('version') != 3:
            print(f"  ⚠ Old knowledge base (v{kb.get('version', '?')}), starting fresh for v3")
            return {'version': 3, 'projects': [], 'baselines': {}}
        return kb
    return {'version': 3, 'projects': [], 'baselines': {}}

def save_knowledge(kb, path=None):
    path = path or DEFAULT_KB_PATH
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
    return path

def learn_from_profile(profile, kb=None):
    if kb is None:
        kb = load_knowledge()

    btype = profile['building_label']
    fc = profile['floor_count']
    ratios = {}
    for group, count in profile['device_groups'].items():
        ratios[group] = {
            'count': count,
            'per_floor': round(count / fc, 1) if fc > 0 else 0,
        }

    kb['projects'].append({
        'date': datetime.now().isoformat(),
        'building_type': btype,
        'features': sorted(profile['features']),
        'file_count': profile['file_count'],
        'floor_count': fc,
        'total_rooms': profile['total_rooms'],
        'ratios': ratios,
    })

    # Update baselines
    if btype not in kb['baselines']:
        kb['baselines'][btype] = {}
    same = [p for p in kb['projects'] if p['building_type'] == btype]
    for group in set().union(*(p['ratios'].keys() for p in same)):
        vals = [p['ratios'][group] for p in same if group in p['ratios']]
        if vals:
            avg = sum(v['per_floor'] for v in vals) / len(vals)
            kb['baselines'][btype][group] = {
                'per_floor': round(avg, 1),
                'sample_count': len(vals),
            }

    return kb


# ============================================================
# 7. DISPLAY
# ============================================================

BLDG_EMOJI = {
    'hospital': '🏥', 'clinic': '🏥', 'nursery': '🏫', 'kindergarten': '🏫',
    'school': '🎓', 'residential': '🏢', 'hotel': '🏨', 'elderly': '👴',
    'welfare': '🤝', 'office': '🏢', 'factory': '🏭', 'warehouse': '📦',
    'retail': '🛒', 'restaurant': '🍽️', 'library': '📚', 'museum': '🎨',
    'gym': '💪', 'theater': '🎭', 'parking': '🅿️', 'shrine': '⛩️',
    'temple': '🛕', 'church': '⛪', 'unknown': '🏗️',
}

def get_emoji(building_types):
    for bt in building_types:
        if bt in BLDG_EMOJI:
            return BLDG_EMOJI[bt]
    return '🏗️'


def print_results(eval_results):
    status_emoji = {
        'PASS': '✅', 'PASS_GEO': '🔷', 'MARGINAL': '⚠️',
        'WARN': '🟡', 'FAIL': '❌',
    }
    counts = defaultdict(int)
    fix_items = []

    for er in eval_results:
        r = er['rule']
        emoji = status_emoji.get(er['status'], '?')
        geo = ''
        if er['geo'] and er['geo']['size_delta'] > 0:
            geo = f" [GEO: +{er['geo']['size_delta']:,}B]"
        layer = f"[{r['layer'][:4]}]" if r['layer'] != 'universal' else '[共通]'
        print(f"    {emoji} {layer} [{r['severity']:>8}] {r['name']}: "
              f"count={er['count']} (min={r['min_count']}) → {er['status']}"
              f"{geo} ({r['law']})")
        counts[er['status']] += 1

        # Collect items that need fixing
        if er['status'] not in ('PASS', 'PASS_GEO'):
            fix_items.append(er)

    total = len(eval_results)
    passed = counts['PASS'] + counts['PASS_GEO']
    pct = passed / total * 100 if total > 0 else 0

    universal = [er for er in eval_results if er['rule']['layer'] == 'universal']
    feature = [er for er in eval_results if er['rule']['layer'] == 'feature']
    u_pass = sum(1 for er in universal if er['status'] in ('PASS', 'PASS_GEO'))
    f_pass = sum(1 for er in feature if er['status'] in ('PASS', 'PASS_GEO'))

    print(f"\n    共通ルール: {u_pass}/{len(universal)} PASS")
    print(f"    検出ルール: {f_pass}/{len(feature)} PASS")
    print(f"    合計: {passed}/{total} PASS ({pct:.0f}%)")

    # Print fix suggestions if there are failures
    if fix_items:
        print(f"\n  ━━━ 修正提案（{len(fix_items)}件）━━━")
        for i, er in enumerate(fix_items, 1):
            r = er['rule']
            group = r['group']
            fix = FIX_SUGGESTIONS.get(group, DEFAULT_FIX)
            shortage = r['min_count'] - er['count']

            print(f"\n    [{i}] {r['name']} — {fix['action']}")
            print(f"        状態: {er['status']} (検出={er['count']}, 基準={r['min_count']}, 不足={shortage})")
            print(f"        対応: {fix['detail']}")
            print(f"        図面: {', '.join(fix['drawings'])}")
            print(f"        根拠: {fix['standard']}")

    return {'total': total, 'passed': passed, 'pct': pct, 'counts': dict(counts)}


def print_delta_results(delta_results):
    """Print delta check results."""
    status_emoji = {
        'IMPROVED': '✅', 'IMPROVED_GEO': '🔷',
        'NO_CHANGE': '⚠️', 'DECREASED': '❌',
    }

    # Show critical/high first, then info
    critical = [d for d in delta_results if d['severity'] in ('CRITICAL', 'HIGH')]
    info = [d for d in delta_results if d['severity'] == 'INFO']

    if critical:
        print(f"    重要設備の修正状況:")
        for d in sorted(critical, key=lambda x: -abs(x['delta'])):
            emoji = status_emoji.get(d['status'], '?')
            geo = f" [GEO: +{d['geo_delta']:,}B]" if d['geo_delta'] > 5000 else ''
            print(f"    {emoji} [{d['severity']:>8}] {d['name']:<22}: "
                  f"{d['before']:>5} → {d['after']:>5} ({d['delta']:>+d}){geo} "
                  f"→ {d['status']}")

    improved = sum(1 for d in critical if d['status'] in ('IMPROVED', 'IMPROVED_GEO'))
    nochange = sum(1 for d in critical if d['status'] == 'NO_CHANGE')
    decreased = sum(1 for d in critical if d['status'] == 'DECREASED')

    print(f"\n    重要設備: {improved} improved, {nochange} no change, {decreased} decreased")

    if info:
        info_improved = sum(1 for d in info if d['status'] in ('IMPROVED', 'IMPROVED_GEO'))
        print(f"    その他設備: {info_improved}/{len(info)} improved")

    # Show fix suggestions for NO_CHANGE and DECREASED critical items
    problem_items = [d for d in critical if d['status'] in ('NO_CHANGE', 'DECREASED')]
    if problem_items:
        print(f"\n    修正提案（未改善の重要設備）:")
        for d in problem_items:
            fix = FIX_SUGGESTIONS.get(d['group'], DEFAULT_FIX)
            if d['status'] == 'NO_CHANGE':
                print(f"      ⚠️ {d['name']}: Before={d['before']} → After={d['after']} (変化なし)")
                print(f"         → {fix['action']}: {fix['detail'][:80]}...")
                print(f"         → 確認図面: {', '.join(fix['drawings'])}")
            elif d['status'] == 'DECREASED':
                print(f"      ❌ {d['name']}: Before={d['before']} → After={d['after']} (減少!)")
                print(f"         → 設備が削除された可能性。意図的でない場合は復元してください。")
                print(f"         → 確認図面: {', '.join(fix['drawings'])}")


# ============================================================
# 8. MAIN COMMANDS
# ============================================================

def cmd_scan(folder, knowledge=None):
    print(f"\n{'=' * 70}")
    print(f"  CAD Checker v7 — AI図面自動チェック")
    print(f"  フォルダ: {folder}")
    print(f"{'=' * 70}")

    profile = build_profile(folder)
    emoji = get_emoji(profile['building_types'])

    print(f"\n  {emoji} {profile['building_label']}")
    print(f"  図面数: {profile['file_count']} | "
          f"階数: {profile['floor_count']} | "
          f"室数: {profile['total_rooms']}")
    if profile['features']:
        print(f"  検出設備: {', '.join(sorted(profile['features']))}")
    if profile['rooms']:
        r_str = ', '.join(f"{k}={v}" for k, v in sorted(profile['rooms'].items())[:10])
        print(f"  室名: {r_str}")

    print(f"\n  ━━━ PART A: 基準チェック（法令・基準に基づく設備数量）━━━")
    print(f"\n  検出設備 ({len(profile['device_groups'])} types):")
    for g, c in sorted(profile['device_groups'].items(), key=lambda x: -x[1]):
        name = GROUP_NAMES_JA.get(g, g)
        print(f"    {name:<22}: {c:>6}")

    rules = generate_rules(profile, knowledge)
    results = evaluate_rules(rules, profile['device_groups'])

    print(f"\n  基準判定:")
    summary_a = print_results(results)

    # PART B: Visual scan (symbol detection + zone coverage)
    print(f"\n  ━━━ PART B: 図面検図（シンボル検出・配置分析）━━━")
    vis_results, vis_errors = run_visual_scan(folder)
    summary_b = print_scan_visual(vis_results, vis_errors)

    # Overall summary
    print(f"\n  ━━━ 総合判定 ━━━")
    part_a_pct = summary_a['pct']
    n_issues = summary_b['n_critical'] + summary_b['n_warning']
    vis_total = summary_b['n_critical'] + summary_b['n_warning'] + summary_b['n_info'] + summary_b['n_pass']
    part_b_pct = summary_b['n_pass'] / vis_total * 100 if vis_total > 0 else 100

    print(f"    PART A 基準チェック: {summary_a['passed']}/{summary_a['total']} PASS ({part_a_pct:.0f}%)")
    print(f"    PART B 図面検図:   {summary_b['n_pass']}/{vis_total} PASS ({part_b_pct:.0f}%)")

    total_score = part_a_pct * 0.5 + part_b_pct * 0.5
    print(f"    総合スコア: {total_score:.0f}% (A:{part_a_pct:.0f}%×0.5 + B:{part_b_pct:.0f}%×0.5)")

    if summary_b['n_critical'] > 0:
        print(f"\n    ❌ {summary_b['n_critical']}件のCRITICALエラー — 早急に対応してください")
    if summary_b['n_warning'] > 0:
        print(f"    ⚠️  {summary_b['n_warning']}件のWARNING — 確認を推奨します")
    if n_issues == 0 and summary_a['pct'] == 100:
        print(f"\n    ✅ 全チェック合格 — 問題は検出されませんでした")

    return profile


def cmd_validate(before_folder, after_folder, knowledge=None):
    print(f"\n{'=' * 70}")
    print(f"  CAD Checker v7 — Feature-Based Validation")
    print(f"  Before: {before_folder}")
    print(f"  After:  {after_folder}")
    print(f"{'=' * 70}")

    bp = build_profile(before_folder)
    ap = build_profile(after_folder)

    emoji = get_emoji(ap['building_types'])
    print(f"\n  {emoji} {ap['building_label']}")
    print(f"  Files: {bp['file_count']} → {ap['file_count']} | "
          f"Floors: {ap['floor_count']} | Rooms: {ap['total_rooms']}")
    if ap['features']:
        print(f"  Features: {', '.join(sorted(ap['features']))}")

    # Changes
    b_dev = aggregate_devices(bp['keywords'])
    a_dev = ap['device_groups']
    geo = compute_geo_deltas(bp, ap)

    changes = []
    for g in sorted(set(b_dev.keys()) | set(a_dev.keys())):
        b = b_dev.get(g, 0)
        a = a_dev.get(g, 0)
        d = a - b
        if d != 0:
            changes.append((g, b, a, d))

    if changes:
        print(f"\n  Changes (before → after):")
        for g, b, a, d in changes:
            name = GROUP_NAMES_JA.get(g, g)
            marker = '↑' if d > 0 else '↓'
            print(f"    {marker} {name:<22}: {b:>6} → {a:>6} ({d:>+d})")

    if geo:
        geo_changes = [(k, v) for k, v in geo.items() if v['size_delta'] > 0]
        if geo_changes:
            print(f"\n  Geometric changes:")
            for g, delta in sorted(geo_changes, key=lambda x: -x[1]['size_delta']):
                name = GROUP_NAMES_JA.get(g, g)
                print(f"    ↑ {name:<22}: +{delta['size_delta']:>10,}B ({delta['size_pct']:>+.1f}%)")

    rules = generate_rules(ap, knowledge)
    results = evaluate_rules(rules, ap['device_groups'], geo, b_dev)

    print(f"\n  ━━━ PART A: 基準チェック（After が法令基準を満たすか）━━━")
    summary = print_results(results)

    # Delta check
    delta_results = evaluate_delta(b_dev, a_dev, geo)
    print(f"\n  ━━━ PART B: 修正チェック（Before→After で改善されたか）━━━")
    print_delta_results(delta_results)

    # Combined score
    delta_critical = [d for d in delta_results if d['severity'] in ('CRITICAL', 'HIGH')]
    delta_improved = sum(1 for d in delta_critical if d['status'] in ('IMPROVED', 'IMPROVED_GEO'))
    delta_total = len(delta_critical)

    # Visual check (PART C)
    print(f"\n  ━━━ PART C: 図面検図（座標ベース空間分析）━━━")
    vis_results, vis_suggestions = run_visual_check(before_folder, after_folder)
    vis_summary = print_visual_results(vis_results, vis_suggestions)

    # Combined score: A (30%) + B (30%) + C (40%)
    print(f"\n  ━━━ 総合判定 ━━━")
    print(f"    PART A 基準チェック: {summary['passed']}/{summary['total']} PASS")
    print(f"    PART B 修正チェック: {delta_improved}/{delta_total} IMPROVED (重要設備)")
    vis_total = vis_summary['n_warning'] + vis_summary['n_critical'] + vis_summary['n_pass']
    vis_pass_pct = vis_summary['n_pass'] / vis_total * 100 if vis_total > 0 else 100
    print(f"    PART C 図面検図:   {vis_summary['n_pass']}/{vis_total} PASS ({vis_pass_pct:.0f}%)")

    part_a = summary['pct']
    part_b = (delta_improved / delta_total * 100) if delta_total > 0 else 100
    part_c = vis_pass_pct
    total_score = part_a * 0.3 + part_b * 0.3 + part_c * 0.4
    print(f"    総合スコア:  {total_score:.0f}% (A:{part_a:.0f}×0.3 + B:{part_b:.0f}×0.3 + C:{part_c:.0f}×0.4)")

    if vis_summary['n_critical'] > 0:
        print(f"\n    ⚠ 図面検図で{vis_summary['n_critical']}件のCRITICAL検出")
    if vis_summary['n_warning'] > 0:
        print(f"    ⚠ 図面検図で{vis_summary['n_warning']}件のWARNING検出")

    summary['delta_improved'] = delta_improved
    summary['delta_total'] = delta_total
    summary['visual'] = vis_summary
    summary['total_score'] = total_score
    return summary


def cmd_learn(folder, kb_path=None):
    print(f"\n{'=' * 70}")
    print(f"  CAD Checker v7 — Learning")
    print(f"  Folder: {folder}")
    print(f"{'=' * 70}")

    kb = load_knowledge(kb_path)
    profile = build_profile(folder)

    emoji = get_emoji(profile['building_types'])
    print(f"\n  Learning from: {emoji} {profile['building_label']} "
          f"({profile['file_count']} files, {profile['floor_count']} floors)")

    kb = learn_from_profile(profile, kb)

    bl = kb['baselines'].get(profile['building_label'], {})
    print(f"\n  Learned baselines (per floor):")
    for group, data in sorted(bl.items(), key=lambda x: -x[1]['per_floor']):
        name = GROUP_NAMES_JA.get(group, group)
        print(f"    {name:<22}: {data['per_floor']:>6.1f}/floor "
              f"({data['sample_count']} project{'s' if data['sample_count']>1 else ''})")

    path = save_knowledge(kb, kb_path)
    print(f"\n  Knowledge saved: {path}")
    print(f"  Total projects: {len(kb['projects'])}")
    return kb


# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    args = sys.argv[1:]
    kb_path = None

    if '--knowledge' in args:
        idx = args.index('--knowledge')
        kb_path = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    kb = load_knowledge(kb_path)

    if len(args) == 2 and args[0] == 'learn':
        cmd_learn(args[1], kb_path)
    elif len(args) == 2 and args[0] == 'compare':
        cmd_validate(args[1].split(',')[0], args[1].split(',')[1], kb)
    elif len(args) == 3 and args[0] == 'compare':
        cmd_validate(args[1], args[2], kb)
    elif len(args) == 1:
        cmd_scan(args[0], kb)
    else:
        samples = os.environ.get('SAMPLES', '/mnt/c/jww/samples')
        # Default: scan the 'after' folder (or first available)
        af = os.path.join(samples, 'after')
        bf = os.path.join(samples, 'before')
        if os.path.isdir(af):
            cmd_scan(af, kb)
        elif os.path.isdir(bf):
            cmd_scan(bf, kb)
        else:
            print("Usage:")
            print(f"  {sys.argv[0]} <folder>                    # AI自動チェック（メイン機能）")
            print(f"  {sys.argv[0]} compare <before> <after>     # before/after比較")
            print(f"  {sys.argv[0]} learn <folder>               # 学習モード")
            sys.exit(1)
