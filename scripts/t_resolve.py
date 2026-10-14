import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.resolver import resolve_district

tests = [
    ("LUDHIANA", "PUNJAB"),
    ("THRISSUR", "KERALA"),
    ("LUCKNOW", "UTTAR PRADESH"),
    ("GANJAM", "ORISSA"),
    ("SHIMLA", "HIMACHAL"),
    ("NE DELHI", "DELHI"),
    ("THANE", "MAHARASHTRA"),
    ("KOTTAYAM", "KERALA"),
]
for d, s in tests:
    try:
        r = resolve_district(d, s, month=9)
        loc, c, soil = r["location"], r["climate"], r["soil"]
        print(
            f"{d}, {s} -> {loc['district']}, {loc['state']} | lat {r['lat']} lon {r['lon']}"
            f" | pH {soil['ph']} | rainN {c['rainfall_normal_mm']} | T {c['temp_c']} | warn {len(r['warnings'])}"
        )
    except Exception as exc:
        print(f"{d}, {s} -> ERROR {exc}")
