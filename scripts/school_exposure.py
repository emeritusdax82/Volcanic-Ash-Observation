import json, math
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))
VOLCANO = (-6.102, 105.423)
CONFIG_PATH = 'data/school_config.json'
CURRENT_PATH = 'data/current.json'
OUTPUT_PATH = 'data/exposure.json'


def haversine_km(a, b):
    lat1, lon1 = map(math.radians, a); lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2-lat1, lon2-lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def bearing_deg(a, b):
    lat1, lon1 = map(math.radians, a); lat2, lon2 = map(math.radians, b)
    dlon = lon2-lon1
    y = math.sin(dlon)*math.cos(lat2)
    x = math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angular_difference(a, b): return abs((a-b+180) % 360 - 180)

def direction_to_deg(direction):
    return {'N':0,'NE':45,'E':90,'SE':135,'S':180,'SW':225,'W':270,'NW':315}.get((direction or '').upper())


def destination(point, bearing, distance_km):
    lat1, lon1 = map(math.radians, point); br = math.radians(bearing); d = distance_km / 6371.0
    lat2 = math.asin(math.sin(lat1)*math.cos(d) + math.cos(lat1)*math.sin(d)*math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br)*math.sin(d)*math.cos(lat1), math.cos(d)-math.sin(lat1)*math.sin(lat2))
    return [round(math.degrees(lat2), 5), round(math.degrees(lon2), 5)]


def classify_alignment(school_bearing, plume_direction):
    deg = direction_to_deg(plume_direction)
    if deg is None: return 'UNKNOWN'
    d = angular_difference(school_bearing, deg)
    if d <= 30: return 'TOWARD SCHOOL'
    if d <= 60: return 'PARTIALLY TOWARD'
    return 'AWAY / CROSSWIND'


def make_sector(direction, max_km=300, half_angle=35):
    deg = direction_to_deg(direction)
    if deg is None: return None
    left = destination(VOLCANO, deg-half_angle, max_km); right = destination(VOLCANO, deg+half_angle, max_km); tip = destination(VOLCANO, deg, max_km)
    return {'direction': direction, 'half_angle_deg': half_angle, 'radius_km': max_km, 'polygon': [[VOLCANO[0], VOLCANO[1]], left, tip, right, [VOLCANO[0], VOLCANO[1]]]}


def pm25_risk(pm25):
    if pm25 is None: return 2, 'PM2.5 unavailable; surface exposure cannot be cleared.'
    if pm25 >= 55: return 4, f'PM2.5 model estimate is {pm25:.1f} µg/m³ — high exposure concern.'
    if pm25 >= 35: return 3, f'PM2.5 model estimate is {pm25:.1f} µg/m³ — elevated exposure concern.'
    if pm25 >= 15: return 1, f'PM2.5 model estimate is {pm25:.1f} µg/m³ — monitor exposure.'
    return 0, f'PM2.5 model estimate is {pm25:.1f} µg/m³ — relatively low at model resolution.'


def main():
    with open(CONFIG_PATH, encoding='utf-8') as f: school = json.load(f)
    with open(CURRENT_PATH, encoding='utf-8') as f: current = json.load(f)

    coords = (school['latitude'], school['longitude'])
    distance = haversine_km(VOLCANO, coords); bearing = bearing_deg(VOLCANO, coords)
    bmkg = current.get('sources', {}).get('bmkg', {}); direction = bmkg.get('direction')
    alignment = classify_alignment(bearing, direction)
    score = 0; reasons = []

    ash_alt = bmkg.get('ash_altitude_ft')
    if ash_alt and ash_alt >= 15000: score += 2; reasons.append('Confirmed ash plume reaches approximately 15,000 ft.')
    if alignment == 'TOWARD SCHOOL': score += 4; reasons.append('Low-level plume direction is aligned toward the school.')
    elif alignment == 'PARTIALLY TOWARD': score += 2; reasons.append('School lies within the downwind sector, with partial alignment.')
    else: reasons.append('School is not in the simplified low-level downwind sector based on the reported direction.')

    if 'siaga' in current.get('sources', {}).get('pvmbg', {}).get('status', '').lower(): score += 2; reasons.append('Volcano remains at Level III / Siaga.')

    aq = current.get('sources', {}).get('air_quality', {})
    aq_score, aq_reason = pm25_risk(aq.get('pm25'))
    score += aq_score; reasons.append(aq_reason)
    if aq.get('source_type'): reasons.append('Air-quality input is modelled atmospheric data, not a ground-station ISPU reading.')

    if current.get('decision', {}).get('official_guidance') == 'ACTIVE': score += 2; reasons.append('Official guidance/impact remains active.')

    if score >= 9: recommendation, level = 'CONTINUE PJJ', 'CRITICAL'
    elif score >= 6: recommendation, level = 'PJJ — REVIEW SOON', 'HIGH'
    elif score >= 3: recommendation, level = 'MODIFIED / CONTROLLED RETURN', 'WATCH'
    else: recommendation, level = 'RETURN TO SCHOOL', 'LOW'

    confidence = 'HIGH'
    if alignment == 'UNKNOWN' or aq.get('pm25') is None: confidence = 'MEDIUM'
    if current.get('sources', {}).get('vaac', {}).get('status') != 'verified': confidence = 'LOW'

    output = {
        'fetched_at': datetime.now(WIB).isoformat(timespec='seconds'), 'school': school,
        'volcano': {'latitude': VOLCANO[0], 'longitude': VOLCANO[1]},
        'exposure': {'distance_km': round(distance,1), 'bearing_from_volcano_deg': round(bearing,1), 'plume_direction': direction, 'plume_alignment': alignment},
        'air_quality': {'pm25': aq.get('pm25'), 'pm10': aq.get('pm10'), 'european_aqi': aq.get('european_aqi'), 'status': aq.get('status'), 'source': aq.get('source')},
        'map': {'low_level_sector': make_sector(direction, 300, 35), 'high_level_sector': make_sector('SW', 700, 35), 'note': 'Sectors are simplified visual decision-support geometry, not official VAAC polygons.'},
        'score': score, 'risk_level': level, 'recommendation': recommendation, 'confidence': confidence,
        'reasons': reasons,
        'limitations': ['Decision-support only; not an official ash forecast.', 'Simplified sectors must not replace official VAAC polygon geometry or altitude-dependent wind analysis.', 'PM2.5 is currently modelled data; connect an official/local ground sensor or ISPU station before treating air quality as cleared.']
    }
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f: json.dump(output, f, ensure_ascii=False, indent=2)

if __name__ == '__main__': main()
