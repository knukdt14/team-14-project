"""
여행지 추첨에 필요한 지리 기능 모음.

- 시군구 목록 로드 (SGIS GeoJSON 우선, 없으면 CSV 폴백)
- 두 지점 사이 거리 계산
- 조건에 맞는 지역만 걸러내기
- 시군구 경계 안에서 무작위 좌표 뽑기
"""

import json
import math
import random
from pathlib import Path

import pandas as pd

# 프로젝트 루트 기준 경로
BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "data" / "sigungu.csv"
GEOJSON_PATH = BASE_DIR / "data" / "sgis" / "sigungu.geojson"

# shapely가 설치돼 있으면 실제 경계 안에서 좌표를 뽑는다.
try:
    from shapely.geometry import shape, Point

    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

# 화면에 쓸 시도 약칭. 강원 고성군 / 경남 고성군처럼 이름이 겹치는 곳이 있어서 필요하다.
SIDO_SHORT = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구",
    "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전",
    "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기",
    "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남",
    "전북특별자치도": "전북", "전라남도": "전남", "경상북도": "경북",
    "경상남도": "경남", "제주특별자치도": "제주",
}


def load_regions():
    """시군구 목록을 DataFrame으로 돌려준다.

    컬럼: sido, sigungu, lat, lng, coastal, metro, short, name
    """
    df = pd.read_csv(CSV_PATH)
    df["short"] = df["sido"].map(SIDO_SHORT)
    # 광역시는 "부산", 그 외는 "경남 통영시" 형태로 표기
    df["name"] = df.apply(
        lambda r: r["short"] if r["metro"] == 1 else f"{r['short']} {r['sigungu']}",
        axis=1,
    )
    return df


def load_boundaries():
    """SGIS 시군구 경계 GeoJSON을 읽어 {지역명: geometry} 형태로 돌려준다.

    파일이 없거나 shapely가 없으면 빈 dict를 돌려준다.
    이 경우 좌표는 대표 좌표 주변에서 뽑힌다.
    """
    if not HAS_SHAPELY or not GEOJSON_PATH.exists():
        return {}

    with open(GEOJSON_PATH, encoding="utf-8") as f:
        geojson = json.load(f)

    boundaries = {}
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        # SGIS 파일마다 속성 이름이 달라서 후보를 순서대로 찾는다
        name = props.get("SIG_KOR_NM") or props.get("sigungu") or props.get("name")
        if not name:
            continue
        boundaries[name] = shape(feature["geometry"])
    return boundaries


# ---------------------------------------------------------------------------
# 거리 계산
# ---------------------------------------------------------------------------

def haversine(lat1, lng1, lat2, lng2):
    """두 좌표 사이 직선 거리(km)."""
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# 필터
# ---------------------------------------------------------------------------

def filter_regions(df, origin, max_km=None, coastal_only=False, exclude_metro=True):
    """추첨 후보를 조건에 맞게 걸러낸다.

    origin        : 출발지 행 (Series). 자기 자신은 후보에서 빠진다.
    max_km        : 출발지 기준 최대 거리(km). None이면 제한 없음.
    coastal_only  : True면 바다 인접 시군구만
    exclude_metro : True면 특별시·광역시 제외
    """
    result = df.copy()

    # 거리 계산
    result["distance_km"] = result.apply(
        lambda row: haversine(origin["lat"], origin["lng"], row["lat"], row["lng"]),
        axis=1,
    ).round(0)

    # 출발지와 같은 곳은 제외
    result = result[result["name"] != origin["name"]]

    if exclude_metro:
        result = result[result["metro"] == 0]

    if coastal_only:
        result = result[result["coastal"] == 1]

    if max_km:
        result = result[result["distance_km"] <= max_km]

    return result.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 무작위 좌표 추출
# ---------------------------------------------------------------------------

def random_point_in_polygon(geom, max_try=200):
    """폴리곤 내부의 무작위 좌표를 뽑는다.

    경계 상자 안에서 점을 찍고, 폴리곤 안에 들어올 때까지 다시 시도한다.
    (point-in-polygon 검증)
    """
    min_lng, min_lat, max_lng, max_lat = geom.bounds
    for _ in range(max_try):
        lng = random.uniform(min_lng, max_lng)
        lat = random.uniform(min_lat, max_lat)
        if geom.contains(Point(lng, lat)):
            return lat, lng
    # 끝내 못 찾으면 중심점으로
    center = geom.centroid
    return center.y, center.x


def pick_point(region, boundaries=None):
    """해당 시군구 안의 무작위 좌표를 돌려준다.

    경계 데이터가 있으면 폴리곤 내부에서, 없으면 대표 좌표 주변에서 뽑는다.
    """
    if boundaries:
        geom = boundaries.get(region["sigungu"])
        if geom is not None:
            return random_point_in_polygon(geom)

    # 폴백: 대표 좌표에서 약 ±3km 정도 흔들기
    jitter = 0.03
    lat = region["lat"] + random.uniform(-jitter, jitter)
    lng = region["lng"] + random.uniform(-jitter, jitter)
    return round(lat, 5), round(lng, 5)


# ---------------------------------------------------------------------------
# 카드 뽑기
# ---------------------------------------------------------------------------

def draw_regions(pool, count=3, exclude_names=None):
    """후보 풀에서 서로 겹치지 않게 count개를 뽑는다."""
    exclude_names = exclude_names or []
    candidates = pool[~pool["name"].isin(exclude_names)]

    if len(candidates) == 0:
        return []

    n = min(count, len(candidates))
    picked = candidates.sample(n=n)
    return [row for _, row in picked.iterrows()]


def grade_of(distance_km):
    """출발지에서 먼 곳일수록 높은 등급을 준다.

    등급은 장식이 아니라 '얼마나 멀리 가는 여행인가'를 나타낸다.
    """
    if distance_km < 100:
        return "가벼움"
    if distance_km < 200:
        return "괜찮음"
    if distance_km < 320:
        return "본격적"
    return "대장정"
