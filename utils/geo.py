import difflib
import hashlib
import json
import os
import re
import time

import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
GEOJSON_PATH = os.path.join(DATA_DIR, "india.geojson")

_GEO = None
_NOMINATIM_THROTTLE = {}
USER_AGENT = "crop-recommendation-system/2.0 (student project)"


def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)
    return CACHE_DIR


def cache_path(key):
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return os.path.join(ensure_cache_dir(), digest + ".json")


def cache_get(key, max_age_hours=24 * 30):
    path = cache_path(key)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > max_age_hours * 3600:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def cache_set(key, value):
    try:
        with open(cache_path(key), "w", encoding="utf-8") as fh:
            json.dump(value, fh)
    except OSError:
        pass


def _rings(geometry):
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    return []


def _bbox(coords):
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return min(xs), min(ys), max(xs), max(ys)


def _point_in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def load_districts(force=False):
    global _GEO
    if _GEO is not None and not force:
        return _GEO
    with open(GEOJSON_PATH, "r", encoding="utf-8") as fh:
        gj = json.load(fh)
    districts = []
    for feature in gj["features"]:
        props = feature.get("properties") or {}
        for poly in _rings(feature.get("geometry") or {}):
            if not poly:
                continue
            outer = poly[0]
            districts.append(
                {
                    "district": props.get("district"),
                    "state": props.get("st_nm"),
                    "dt_code": props.get("dt_code"),
                    "st_code": props.get("st_code"),
                    "bbox": _bbox(outer),
                    "outer": outer,
                    "holes": poly[1:],
                }
            )
    _GEO = districts
    return districts


def district_for_point(lat, lon):
    for d in load_districts():
        xmin, ymin, xmax, ymax = d["bbox"]
        if not (xmin <= lon <= xmax and ymin <= lat <= ymax):
            continue
        if not _point_in_ring(lon, lat, d["outer"]):
            continue
        if any(_point_in_ring(lon, lat, hole) for hole in d["holes"]):
            continue
        return {
            "district": d["district"],
            "state": d["state"],
            "dt_code": d["dt_code"],
            "source": "Survey of India district polygons (bundled GeoJSON)",
        }
    return None


def normalize_name(name):
    if not name:
        return ""
    text = str(name).upper()
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


STATE_ALIASES = {
    "ORISSA": "ODISHA",
    "UTTARANCHAL": "UTTARAKHAND",
    "CHATISGARH": "CHHATTISGARH",
    "CHATTISGARH": "CHHATTISGARH",
    "HIMACHAL": "HIMACHAL PRADESH",
    "ANDHRA": "ANDHRA PRADESH",
    "PONDICHERRY": "PUDUCHERRY",
    "DADAR NAGAR HAVELI": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "DAMAN AND DUI": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "ANDAMAN AND NICOBAR ISLANDS": "ANDAMAN AND NICOBAR ISLANDS",
    "JAMMU AND KASHMIR": "JAMMU AND KASHMIR",
}

# Split / reorganised states whose districts are interchangeable for rainfall lookups.
COMPATIBLE_STATE_PAIRS = [
    frozenset(("TELANGANA", "ANDHRA PRADESH")),
    frozenset(("JAMMU AND KASHMIR", "LADAKH")),
    frozenset(("DAMAN AND DIU", "DADRA AND NAGAR HAVELI AND DAMAN AND DIU")),
]

DELHI_SUBDISTRICTS = {
    "NEW DELHI",
    "CENTRAL DELHI",
    "EAST DELHI",
    "NORTH DELHI",
    "NE DELHI",
    "NW DELHI",
    "SOUTH DELHI",
    "SW DELHI",
    "WEST DELHI",
}

# Renamed / reorganised Indian districts, keyed both directions in normalised form.
DISTRICT_ALIASES = {}
for _pair in [
    ("GURGAON", "GURUGRAM"),
    ("MEWAT", "NUH"),
    ("ALLAHABAD", "PRAYAGRAJ"),
    ("BURDWAN", "PURBA BARDHAMAN"),
    ("BARDHAMAN", "PURBA BARDHAMAN"),
    ("NAWANSHAHR", "SHAHID BHAGAT SINGH NAGAR"),
    ("MUKTSAR", "SRI MUKTSAR SAHIB"),
    ("SONEPUR", "SUBARNAPUR"),
    ("NAWAPARA", "NUAPADA"),
    ("BOUDHGARH", "BOUDH"),
    ("NORTH CACHAR", "DIMA HASAO"),
    ("KANSHIRAM NAGAR", "KASGANJ"),
    ("SANT RAVIDAS", "BHADOHI"),
    ("MAHAMAYA NAGAR", "HATHRAS"),
    ("JYOTIBA PHULE", "AMROHA"),
    ("SAHUJI MAHARA", "MOHALI"),
    ("SAHIBZADA AJIT SINGH NAGAR", "MOHALI"),
    ("BHABUA", "RAMGARH"),
    ("KHERI LAKHIMP", "LAKHIMPUR KHERI"),
    ("GARHWAL PAURI", "PAURI GARHWAL"),
    ("GARHWAL TEHRI", "TEHRI GARHWAL"),
    ("EAST MIDNAPOR", "PURBA MEDINIPUR"),
    ("WEST MIDNAPOR", "PASCHIM MEDINIPUR"),
    ("NORTH DINAJPUR", "UTTAR DINAJPUR"),
    ("SOUTH DINAJPUR", "DAKSHIN DINAJPUR"),
    ("DHEMAJI LAKHI", "DHEMAJI"),
    ("CHIRANG BONGAI", "CHIRANG"),
    ("ANJAW LOHIT", "ANJAW"),
    ("TAWANG W KAME", "TAWANG"),
    ("SUBANSIRI F D", "LOWER SUBANSIRI"),
    ("KEPHRIE", "KIPHIRE"),
    ("CHHIMTUIPUI", "LAWNGTLAI"),
    ("LADAKH LEH", "LEH"),
    ("N M ANDAMAN", "NORTH AND MIDDLE ANDAMAN"),
    ("S ANDAMAN", "SOUTH ANDAMAN"),
    ("DNH", "DADRA AND NAGAR HAVELI"),
    ("MAHAMAYA NAGA", "HATHRAS"),
    ("KANSHIRAM NAG", "KASGANJ"),
    ("KANKER NORH", "UTTAR BASTAR KANKER"),
    ("KANKER NORTH", "UTTAR BASTAR KANKER"),
    ("KOWARDHA", "KABIRDHAM"),
    ("KUDDAPAH", "YSR KADAPA"),
    ("CUDDAPAH", "YSR KADAPA"),
    ("GULBARGA", "KALABURAGI"),
    ("BELGAUM", "BELAGAVI"),
    ("TUMKUR", "TUMAKURU"),
    ("MYSORE", "MYSURU"),
    ("BELLARY", "BALLARI"),
    ("DANTEWADA", "DAKSHIN BASTAR DANTEWADA"),
    ("BIDAR", "BIDAR"),
] + [(name, "DELHI") for name in DELHI_SUBDISTRICTS]:
    DISTRICT_ALIASES.setdefault(normalize_name(_pair[0]), normalize_name(_pair[1]))
    DISTRICT_ALIASES.setdefault(normalize_name(_pair[1]), normalize_name(_pair[1]))

_ALIAS_KEYS = sorted(DISTRICT_ALIASES, key=len, reverse=True)


def _alias_lookup(text):
    hit = DISTRICT_ALIASES.get(text)
    if hit:
        return hit
    if len(text) >= 5:
        for key in _ALIAS_KEYS:
            if len(key) >= 5 and (key.startswith(text) or text.startswith(key)):
                return DISTRICT_ALIASES[key]
    return text


def states_compatible(a, b):
    if not a or not b:
        return True
    left, right = normalize_name(a), normalize_name(b)
    left = STATE_ALIASES.get(left, left)
    right = STATE_ALIASES.get(right, right)
    if left == right or left in right or right in left:
        return True
    return frozenset((left, right)) in COMPATIBLE_STATE_PAIRS


def _name_score(target, cand):
    left = _alias_lookup(DISTRICT_ALIASES.get(target, target))
    right = _alias_lookup(DISTRICT_ALIASES.get(cand, cand))
    score = difflib.SequenceMatcher(None, left, right).ratio()
    if len(left) >= 4 and len(right) >= 4 and (left.startswith(right) or right.startswith(left)):
        score = max(score, 0.95)
    return score


def match_district(name, state, candidates):
    """candidates: iterable of dicts with keys state, district (rainfall CSV rows)."""
    target = normalize_name(name)
    if not target:
        return None
    threshold = 0.6 if state else 0.72
    best, score = None, 0.0
    for row in candidates:
        cand = normalize_name(row["district"])
        cur = _name_score(target, cand)
        if row.get("state") and state and not states_compatible(row["state"], state):
            cur *= 0.5
        if cur > score:
            best, score = row, cur
    return best if score >= threshold else None


def _score_district(name, state, target_name, target_state):
    score = _name_score(normalize_name(target_name), normalize_name(name))
    if state and target_state and not states_compatible(state, target_state):
        score *= 0.5
    return score


def find_district(name, state=None):
    target = normalize_name(name)
    if not target:
        return None
    best, score = None, 0.0
    for d in load_districts():
        if not d["district"]:
            continue
        cur = _score_district(d["district"], d["state"], target, state)
        if cur > score:
            best, score = d, cur
    threshold = 0.6 if state else 0.72
    if not best or score < threshold:
        return None
    return best


def _centroid(ring):
    a = cx = cy = 0.0
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        cross = xi * yj - xj * yi
        a += cross
        cx += (xi + xj) * cross
        cy += (yi + yj) * cross
        j = i
    if a == 0:
        return None
    return (cx / (3 * a), cy / (3 * a))


def district_center(name, state=None):
    """Representative lat/lon for a district: true centroid if it falls inside the polygon, else bbox centre."""
    feat = find_district(name, state)
    if not feat:
        return None, None, None
    centroid = _centroid(feat["outer"])
    if centroid and _point_in_ring(centroid[0], centroid[1], feat["outer"]) and not any(
        _point_in_ring(centroid[0], centroid[1], hole) for hole in feat["holes"]
    ):
        lon, lat = centroid
        if lon is not None and lat is not None:
            return round(lat, 5), round(lon, 5), feat
    xmin, ymin, xmax, ymax = feat["bbox"]
    return round((ymin + ymax) / 2, 5), round((xmin + xmax) / 2, 5), feat


def reverse_geocode(lat, lon):
    key = f"nominatim|{round(float(lat), 4)}|{round(float(lon), 4)}"
    cached = cache_get(key, max_age_hours=24 * 30)
    if cached is not None:
        return cached
    last = _NOMINATIM_THROTTLE.get("t", 0)
    wait = 1.1 - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 12},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        _NOMINATIM_THROTTLE["t"] = time.time()
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    address = data.get("address", {})
    result = {
        "display_name": data.get("display_name"),
        "village": address.get("village") or address.get("suburb") or address.get("town") or address.get("city"),
        "district": address.get("state_district") or address.get("county") or address.get("city_district"),
        "state": address.get("state"),
        "pincode": address.get("postcode"),
        "source": "OpenStreetMap Nominatim",
    }
    cache_set(key, result)
    return result
