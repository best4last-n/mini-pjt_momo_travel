"""기차역·공항 거점과 교통 안내(예약 방법) — SERVICE.md 3-6 (18).

출발지 → (가까운 역·공항까지 접근) → 열차·항공 → (도착 역·공항에서 여행지까지) 전체 시간을 추정하고,
예약은 공식 사이트 링크로 안내(직접 예약·결제는 하지 않음 — 가드레일 4). 모두 직선거리 기반 추정치.
"""
from __future__ import annotations

import math

from src.shared import data, travel

# 고속철도역(KTX·SRT) — (이름, 위도, 경도, 운행)
STATIONS = [
    ("서울역", 37.5547, 126.9707, "KTX"), ("용산역", 37.5299, 126.9648, "KTX"), ("수서역", 37.4873, 127.1015, "SRT"),
    ("광명역", 37.4163, 126.8848, "KTX"), ("동탄역", 37.2000, 127.0960, "SRT"), ("수원역", 37.2659, 127.0002, "KTX"),
    ("천안아산역", 36.7943, 127.1046, "KTX·SRT"), ("오송역", 36.6203, 127.3270, "KTX·SRT"), ("대전역", 36.3324, 127.4343, "KTX·SRT"),
    ("서대전역", 36.3226, 127.4040, "KTX"), ("김천구미역", 36.1131, 128.1800, "KTX·SRT"), ("동대구역", 35.8793, 128.6286, "KTX·SRT"),
    ("신경주역", 35.7983, 129.1395, "KTX·SRT"), ("울산역", 35.5516, 129.1383, "KTX·SRT"), ("부산역", 35.1151, 129.0422, "KTX·SRT"),
    ("광주송정역", 35.1374, 126.7913, "KTX·SRT"), ("익산역", 35.9409, 126.9462, "KTX·SRT"), ("전주역", 35.8500, 127.1617, "KTX·SRT"),
    ("남원역", 35.4142, 127.3727, "KTX·SRT"), ("구례구역", 35.1697, 127.4467, "KTX·SRT"), ("순천역", 34.9454, 127.5033, "KTX·SRT"),
    ("여수엑스포역", 34.7529, 127.7484, "KTX·SRT"), ("포항역", 36.0716, 129.3415, "KTX·SRT"),
    ("강릉역", 37.7640, 128.8995, "KTX"), ("동해역", 37.5176, 129.1239, "KTX"),
    ("진주역", 35.1486, 128.1242, "KTX"),
]
AIRPORTS = [
    ("김포공항", 37.5583, 126.7906), ("인천공항", 37.4602, 126.4407), ("청주공항", 36.7166, 127.4991), ("대구공항", 35.8941, 128.6589),
    ("김해공항", 35.1795, 128.9382), ("광주공항", 35.1264, 126.8089), ("여수공항", 34.8424, 127.6161), ("포항경주공항", 35.9878, 129.4205),
    ("울산공항", 35.5935, 129.3518), ("제주공항", 33.5113, 126.4930), ("무안공항", 34.9914, 126.3828), ("사천공항", 35.0886, 128.0703),
    ("군산공항", 35.9038, 126.6158), ("원주공항", 37.4381, 127.9604), ("양양공항", 38.0613, 128.6692),
]
# 국내선 노선(대략, 계절·운항사 따라 변동) — 여행 지역 쪽 공항 기준
ROUTES = {"사천공항": {"김포공항", "제주공항"}, "여수공항": {"김포공항", "제주공항"}, "포항경주공항": {"김포공항", "제주공항"}, "울산공항": {"김포공항", "제주공항"},
          "광주공항": {"김포공항", "제주공항"}, "김해공항": {"김포공항", "인천공항", "제주공항"}, "제주공항": {a[0] for a in AIRPORTS} - {"제주공항"}}
REGION_HUBS = {
    "suncheon_gurye": {"stations": ["순천역", "구례구역"], "airports": ["여수공항"]},
    "gyeongju": {"stations": ["신경주역", "포항역", "울산역"], "airports": ["포항경주공항", "울산공항"]},
    "suwon": {"stations": ["수원역"], "airports": []},
    "gangneung": {"stations": ["강릉역", "동해역"], "airports": []},
    "jeonju": {"stations": ["전주역", "익산역"], "airports": []},
    "tongyeong_geoje": {"stations": ["진주역"], "airports": ["사천공항"]},
    "jeju": {"stations": [], "airports": ["제주공항"]},      # 섬 — 항공 전용(regions.json flight_required)
}
BOOKING = {
    "KTX": {"label": "코레일 승차권 예매", "url": "https://www.letskorail.com"},
    "SRT": {"label": "SRT 예매", "url": "https://etk.srail.kr"},
    "고속버스": {"label": "고속버스 예매(코버스)", "url": "https://www.kobus.co.kr"},
    "시외버스": {"label": "시외버스 예매(버스타고)", "url": "https://txbus.t-money.co.kr"},
    "air": [{"label": "대한항공", "url": "https://www.koreanair.com"}, {"label": "아시아나항공", "url": "https://flyasiana.com"},
            {"label": "제주항공", "url": "https://www.jejuair.net"}, {"label": "진에어", "url": "https://www.jinair.com"},
            {"label": "티웨이항공", "url": "https://www.twayair.com"}],
}


def _ceil5(m: float) -> int:
    return int(math.ceil(m / 5) * 5)


def access_min(a: tuple, b: tuple) -> int:
    """거점 접근(자차·택시 기준): 시내 30km/h, 10km 넘는 구간 60km/h + 준비 10분."""
    d = travel.km(a, b)
    return _ceil5((min(d, 10) * 1.3 / 30 + max(d - 10, 0) * 1.3 / 60) * 60 + 10)


def _station(name):
    return next(s for s in STATIONS if s[0] == name)


def _airport(name):
    return next(a for a in AIRPORTS if a[0] == name)


def rail_options(origin: tuple, region: str) -> list[dict]:
    center = data.region_center(region)
    dests = [_station(n) for n in REGION_HUBS.get(region, {}).get("stations", [])]
    near = sorted(STATIONS, key=lambda s: travel.km(origin, (s[1], s[2])))[:3]
    out = []
    for o in near:
        for d in dests:
            if o[0] == d[0]:
                continue
            ride = _ceil5(travel.km((o[1], o[2]), (d[1], d[2])) * 1.2 / 140 * 60 + 15)
            acc, egr = access_min(origin, (o[1], o[2])), access_min((d[1], d[2]), center)
            total = acc + 20 + ride + egr
            op = "SRT" if o[3] == "SRT" else "KTX"
            out.append({"name": f"{o[0]} → {d[0]}", "from": o[0], "to": d[0], "operator": op, "access_min": acc,
                        "ride_min": ride, "egress_min": egr, "wait_min": 20, "total_min": total})
    out.sort(key=lambda x: x["total_min"])
    return out[:4]


def air_options(origin: tuple, region: str) -> list[dict]:
    center = data.region_center(region)
    out = []
    for dn in REGION_HUBS.get(region, {}).get("airports", []):
        d = _airport(dn)
        for on in ROUTES.get(dn, set()):
            o = _airport(on)
            acc = access_min(origin, (o[1], o[2]))
            if acc > 120:
                continue
            fly = _ceil5(travel.km((o[1], o[2]), (d[1], d[2])) / 600 * 60 + 25)
            egr = access_min((d[1], d[2]), center)
            total = acc + 60 + fly + 20 + egr
            out.append({"name": f"{on} → {dn}", "from": on, "to": dn, "operator": "항공", "access_min": acc,
                        "checkin_min": 60, "ride_min": fly, "baggage_min": 20, "egress_min": egr, "total_min": total})
    out.sort(key=lambda x: x["total_min"])
    return out[:3]


def guide(spec, one_way_drive: int | None) -> dict | None:
    """plan.transport_guide — 장거리 이동 방법·대안 비교·예약 링크·팁(자차면 주유 안내)."""
    if not spec.departure:
        return None
    origin = tuple(spec.origin_coords) if getattr(spec, "origin_coords", None) else data.SIDO_COORDS.get(spec.departure)
    if not origin:
        return None
    oname = spec.origin_label or spec.departure
    mode = spec.long_transport
    g = {"mode": mode, "estimated": True, "options": [], "booking": [], "tips": [], "legs": [], "fuel": []}
    rail = rail_options(origin, spec.region)
    air = air_options(origin, spec.region)
    if mode == "KTX·SRT" and rail:
        best = rail[0]
        g["options"] = [{**o, "best": i == 0} for i, o in enumerate(rail)]
        g["legs"] = [{"type": "access", "from": oname, "to": best["from"], "minutes": best["access_min"], "note": "자차·택시·대중교통 추정"},
                     {"type": "checkin", "from": best["from"], "to": best["from"], "minutes": 20, "note": "승차 대기"},
                     {"type": "ride", "from": best["from"], "to": best["to"], "minutes": best["ride_min"], "note": best["operator"]},
                     {"type": "egress", "from": best["to"], "to": data.region_label(spec.region), "minutes": best["egress_min"],
                      "note": spec.local_transport}]
        g["summary"] = f"{oname} → {best['from']}(약 {best['access_min']}분) → {best['operator']} → {best['to']}(약 {best['ride_min']}분) → {spec.local_transport}"
        g["total_min"] = best["total_min"]
        ops = {o["operator"] for o in rail}
        g["booking"] = [BOOKING[o] for o in ("SRT", "KTX") if o in ops]
        g["tips"] = ["주말·연휴는 2~3주 전 예매를 권해요(매월 출발 한 달 전부터 열려요)",
                     "직통 여부와 정확한 소요시간은 예매 사이트에서 확인해 주세요"]
        if spec.local_transport == "렌터카":
            g["tips"].append(f"{best['to']} 근처 렌터카는 미리 예약해 두면 역에서 바로 받을 수 있어요")
    elif mode == "비행기":
        if air:
            best = air[0]
            g["options"] = [{**o, "best": i == 0} for i, o in enumerate(air)]
            g["legs"] = [{"type": "access", "from": oname, "to": best["from"], "minutes": best["access_min"], "note": "공항까지"},
                         {"type": "checkin", "from": best["from"], "to": best["from"], "minutes": 60, "note": "탑승 수속·보안 검색"},
                         {"type": "ride", "from": best["from"], "to": best["to"], "minutes": best["ride_min"], "note": "국내선"},
                         {"type": "egress", "from": best["to"], "to": data.region_label(spec.region),
                          "minutes": best["baggage_min"] + best["egress_min"], "note": "수하물·공항에서 이동"}]
            g["summary"] = f"{oname} → {best['from']}(약 {best['access_min']}분) → ✈ {best['to']}(비행 약 {best['ride_min']}분) → {spec.local_transport}"
            g["total_min"] = best["total_min"]
            g["booking"] = BOOKING["air"]
            g["tips"] = ["국내선은 신분증이 꼭 필요해요. 출발 40분 전까지 탑승 수속을 마쳐야 해요",
                         "운항 노선·편수는 계절마다 달라요 — 항공사 사이트에서 확인해 주세요"]
            if rail and rail[0]["total_min"] <= best["total_min"]:
                g["tips"].append(f"이 구간은 KTX·SRT({rail[0]['name']}, 약 {rail[0]['total_min']}분)가 비슷하거나 더 빨라요")
        else:
            g["summary"] = "이 지역은 출발지 근처에서 이어지는 국내선이 없어 KTX·SRT나 자차를 추천해요"
            g["tips"] = ["비행기 대신 아래 열차 안내를 참고해 주세요"]
            g["options"] = [{**o, "best": i == 0} for i, o in enumerate(rail)]
            g["booking"] = [BOOKING["KTX"], BOOKING["SRT"]]
            g["total_min"] = rail[0]["total_min"] if rail else None
    elif mode == "고속·시외버스":
        g["summary"] = f"{oname} → 가까운 고속·시외버스 터미널 → {data.region_label(spec.region)} 터미널 → {spec.local_transport}"
        g["total_min"] = one_way_drive + 30 if one_way_drive else None
        g["booking"] = [BOOKING["고속버스"], BOOKING["시외버스"]]
        g["tips"] = ["명절·주말은 매진이 잦아요. 예매 앱으로 미리 좌석을 잡아 두세요"]
        g["options"] = [{**o, "best": False} for o in rail[:2]]
    else:   # 자차 — 도로 경로 기준(열차는 '참고'로만)
        from src.mobility import roads
        dr = roads.drive(origin, data.region_center(spec.region), spec.start, spec.depart_time, True)
        dest = data.region_label(spec.region)
        if dr:
            g["summary"] = (f"{oname} → {dest} 자차 약 {dr['distance_km']:,.0f}km · 예상 {dr['total_min']}분"
                            f"(막힘 없을 때 {dr['free_min']}분 → {dr['reason']} {dr['factor']}배"
                            + (f" + 휴게 {dr['rests']}회" if dr["rests"] else "")
                            + (f" + 돌발 {dr['event_delay_min']}분" if dr.get("event_delay_min") else "") + ")")
            g["road_segments"] = dr.get("segments") or []      # 도로별: 거리·막힘 없을 때·예상 분·속도·출처(실시간/예측/평균)
            g["road_events"] = dr.get("events") or []          # 돌발상황(사고·공사·기상)
            g["road_cautions"] = dr.get("cautions") or []      # 주의운전 구간(졸음쉼터·갓길차로·정체·작업 등)
            g["traffic_sources"] = dr.get("its_used") or []
            g["rests"] = dr.get("rests") or 0
            ret = roads.drive(data.region_center(spec.region), origin, spec.days[-1], 16 * 60, False)
            if ret:
                g["return_drive"] = {"total_min": ret["total_min"], "reason": ret["reason"], "free_min": ret["free_min"],
                                     "segments": ret.get("segments") or [], "traffic_sources": ret.get("its_used") or [],
                                     "rests": ret.get("rests") or 0, "distance_km": ret.get("distance_km")}
            g["total_min"] = dr["total_min"]
            g["legs"] = [{"type": "drive", "from": oname, "to": dest, "minutes": dr["total_min"],
                          "distance_km": dr["distance_km"], "note": dr["source"]}]
            g["route_path"] = dr["path"]
            g["source"] = dr["source"]
            g["estimated"] = not dr["live"]
        else:
            g["summary"] = f"{oname} → {dest} 자차 약 {one_way_drive}분(편도, 직선거리 추정)"
            g["total_min"] = one_way_drive
        g["options"] = []
        g["alternatives"] = [{**o, "best": False} for o in rail[:2]]   # 참고: 열차로 가면
        g["tips"] = ["휴게소에서 2시간마다 쉬어 가세요", "출발 전 타이어 공기압·워셔액을 확인해 주세요",
                     "실제 소요는 출발 직전 내비게이션 실시간 경로로 확인해 주세요"]
        g["fuel"] = fuel_tips(spec, origin)
        if not roads.its_key():
            g["traffic_note"] = "국가교통정보센터 실시간 소통·예측·CCTV는 ITS_API_KEY 설정 후 반영돼요"
        else:
            used = (dr or {}).get("its_used") or []
            g["traffic_note"] = ((f"국가교통정보센터 {'·'.join(used)} 속도로 도로별 소요를 계산했어요. " if used else
                                  "출발일이 오늘·내일이 아니라 국가교통정보센터 예측이 아직 없어 요일·시간대 평균으로 계산했어요(출발 하루 전부터 예측 반영). ")
                                 + "경로 CCTV·돌발·주의운전 구간을 함께 확인하세요")
    return g


def fuel_tips(spec, origin: tuple) -> list[dict]:
    """주유 권장: 출발 전, 장거리면 중간 지점, 귀가 전."""
    from src.counsel import options as O
    v = spec.vehicle or {}
    fuel = v.get("fuel") or "휘발유"
    eff = float(v.get("efficiency_kmpl") or O.efficiency(v.get("type"), fuel))
    rng = 400 if fuel == "전기" else int(eff * 50)        # 탱크 약 50L(전기는 약 400km) 기준 주행 가능 거리
    one_way = travel.km(origin, data.region_center(spec.region)) * 1.3
    local = 25 * (spec.nights + 1)                          # 현지 하루 약 25km 가정
    total = one_way * 2 + local
    word = "충전" if fuel == "전기" else "주유"
    out = []
    if one_way >= 100:
        out.append({"at": "출발 전", "text": f"편도 약 {one_way:,.0f}km예요. 출발 전에 {word}를 가득 해 두세요"
                                          f"(가득 시 약 {rng:,}km 주행 추정)."})
    if one_way > rng * 0.6:
        out.append({"at": f"가는 길 약 {one_way / 2:,.0f}km 지점", "text": f"중간 휴게소에서 한 번 {word}하세요(편도가 주행 가능 거리의 60%를 넘어요)."})
    if total > rng * 0.8:
        out.append({"at": "귀가 전", "text": f"왕복·현지 합계 약 {total:,.0f}km라 돌아오기 전 현지에서 {word}해 두면 안심이에요."})
    if fuel == "전기":
        out.append({"at": "참고", "text": "고속도로 휴게소 급속충전기는 주말에 붐빌 수 있어요. 충전 앱으로 미리 확인해 주세요."})
    return out
