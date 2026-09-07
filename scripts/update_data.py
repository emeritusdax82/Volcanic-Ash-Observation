import html as html_lib
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode

WIB = timezone(timedelta(hours=7))
VAAC_URL = 'https://www.bom.gov.au/products/Volc_ash_latest.shtml'
BMKG_URL = 'https://www.bmkg.go.id/siaran-pers/imbas-sebaran-abu-vulknaik-anak-krakatau-8-bandara-ditutup-sementara-bmkg-minta-waspada-dan-tenang'
AQ_URL = 'https://air-quality-api.open-meteo.com/v1/air-quality'
SCHOOL_LAT = -6.0995
SCHOOL_LON = 106.7197
CURRENT_FILE = 'data/current.json'


def fetch(url):
    req = Request(url, headers={'User-Agent': 'Volcanic-Ash-Observation/1.3'})
    with urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', errors='ignore')


def clean_html(text):
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def parse_coord(token):
    m = re.fullmatch(r'S(\d{4})\s+E(\d{5})', token.strip(), re.I)
    if not m:
        raise ValueError(f'Invalid VAAC coordinate: {token}')
    lat_raw, lon_raw = m.groups()
    lat = -(int(lat_raw[:2]) + int(lat_raw[2:]) / 60.0)
    lon = int(lon_raw[:3]) + int(lon_raw[3:]) / 60.0
    return [round(lat, 5), round(lon, 5)]


def parse_polygon(text, label):
    m = re.search(label + r'\s+([^\n]+?)(?=\s+(?:SFC/FL\d+|RMK:|NXT ADVISORY:|$))', text, re.I)
    if not m:
        return None
    coords = re.findall(r'S\d{4}\s+E\d{5}', m.group(1), re.I)
    if len(coords) < 3:
        return None
    return [parse_coord(x) for x in coords]


def parse_vaac(html):
    text = clean_html(html)
    pattern = re.compile(
        r'(VA ADVISORY).*?DTG:\s*(202609\d{2}/\d{4}Z).*?'
        r'VAAC:\s*DARWIN.*?VOLCANO:\s*KRAKATAU.*?'
        r'ADVISORY NR:\s*(2026/(\d+)).*?(?=VA ADVISORY|$)',
        re.I,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        raise RuntimeError('Latest Darwin Krakatau advisory not found')
    m = max(matches, key=lambda x: int(x.group(4)))
    block = m.group(0)
    lower = re.search(r'VA TO FL(\d+)\s+MOV\s+([A-Z]+)', block, re.I)
    higher = re.search(r'VA TO FL(\d+)\s+MOV\s+([A-Z]+)', block[lower.end():] if lower else block, re.I)

    observed_low = parse_polygon(block, r'OBS VA CLD:\s*SFC/FL\d+')
    forecast6_low = parse_polygon(block, r'FCST VA CLD \+6 HR:\s*[^\s]+\s*SFC/FL\d+')
    forecast12_low = parse_polygon(block, r'FCST VA CLD \+12 HR:\s*[^\s]+\s*SFC/FL\d+')
    forecast18_low = parse_polygon(block, r'FCST VA CLD \+18 HR:\s*[^\s]+\s*SFC/FL\d+')

    return {
        'advisory': m.group(3), 'dtg': m.group(2), 'status': 'verified',
        'lower_level': f'SFC/FL{lower.group(1)}' if lower else None,
        'lower_direction': lower.group(2).upper() if lower else None,
        'higher_level': f'SFC/FL{higher.group(1)}' if higher else None,
        'higher_direction': higher.group(2).upper() if higher else None,
        'low_level_polygons': {
            'observed': observed_low,
            'forecast_6h': forecast6_low,
            'forecast_12h': forecast12_low,
            'forecast_18h': forecast18_low,
        },
        'raw_summary': block[:2200],
    }


def parse_bmkg(html):
    text = clean_html(html)
    m = re.search(r'ketinggian hingga\s*15\.000 kaki.*?bergerak ke arah barat dengan kecepatan\s*15 knot', text, re.I)
    affected = None
    affected_m = re.search(r'mencakup sebagian\s+(.*?)(?:, serta perairan|\.)', text, re.I)
    if affected_m:
        affected = affected_m.group(1).strip()
    return {
        'status': 'verified' if m else 'page_fetched',
        'ash_altitude_ft': 15000 if m else None,
        'direction': 'W' if m else None,
        'speed_kt': 15 if m else None,
        'affected_region': affected,
        'source_note': 'Latest BMKG Anak Krakatau press release fetched by scheduled workflow.',
    }


def parse_air_quality():
    params = urlencode({
        'latitude': SCHOOL_LAT, 'longitude': SCHOOL_LON,
        'current': 'pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,european_aqi',
        'timezone': 'Asia/Jakarta',
    })
    raw = json.loads(fetch(AQ_URL + '?' + params))
    current = raw.get('current', {})
    if current.get('pm2_5') is None and current.get('pm10') is None:
        raise RuntimeError('No current PM data returned')
    return {
        'status': 'verified_model', 'source': 'Open-Meteo Air Quality API / CAMS model',
        'source_type': 'modelled, not ground sensor', 'latitude': SCHOOL_LAT, 'longitude': SCHOOL_LON,
        'pm25': current.get('pm2_5'), 'pm10': current.get('pm10'), 'european_aqi': current.get('european_aqi'),
        'carbon_monoxide': current.get('carbon_monoxide'), 'nitrogen_dioxide': current.get('nitrogen_dioxide'),
        'sulphur_dioxide': current.get('sulphur_dioxide'), 'ozone': current.get('ozone'),
        'observed_at': current.get('time'),
        'note': 'PM values are atmospheric model estimates for the school coordinate, not official Indonesian ISPU station readings.',
    }


def load_previous():
    try:
        with open(CURRENT_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception: return None


def main():
    previous = load_previous(); now = datetime.now(WIB).isoformat(timespec='seconds'); errors = []
    try: vaac = parse_vaac(fetch(VAAC_URL))
    except Exception as exc:
        errors.append(f'VAAC: {exc}'); vaac = previous.get('sources', {}).get('vaac', {'status':'STALE'}) if previous else {'status':'UNAVAILABLE'}; vaac['status'] = 'stale' if previous else 'unavailable'
    try: bmkg = parse_bmkg(fetch(BMKG_URL))
    except Exception as exc:
        errors.append(f'BMKG: {exc}'); bmkg = previous.get('sources', {}).get('bmkg', {'status':'STALE'}) if previous else {'status':'unavailable'}; bmkg['status'] = 'stale' if previous else 'unavailable'
    try: air_quality = parse_air_quality()
    except Exception as exc:
        errors.append(f'AQ: {exc}'); air_quality = previous.get('sources', {}).get('air_quality', {'status':'NOT_CONNECTED','pm25':None,'pm10':None}) if previous else {'status':'UNAVAILABLE','pm25':None,'pm10':None}; air_quality['status'] = 'stale_model' if previous else 'unavailable'

    vaac_ok = vaac.get('status') == 'verified'; bmkg_ok = bmkg.get('status') == 'verified'; aq_ok = air_quality.get('pm25') is not None or air_quality.get('pm10') is not None
    data = {
        'status': 'HIGH', 'recommendation': 'CONTINUE PJJ',
        'confidence': 'HIGH' if vaac_ok and bmkg_ok and aq_ok else 'REDUCED',
        'fetched_at': now, 'source_errors': errors,
        'sources': {'bmkg': bmkg, 'vaac': vaac, 'pvmbg': {'status':'Level III / Siaga'}, 'air_quality': air_quality},
        'decision': {'ash_trajectory':'CRITICAL' if vaac_ok else 'DATA STALE', 'air_quality':'MODEL CONNECTED' if aq_ok else 'DATA NEEDED', 'school_exposure':'VERIFY', 'travel':'WATCH', 'official_guidance':'ACTIVE'},
    }
    with open(CURRENT_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)

if __name__ == '__main__': main()
