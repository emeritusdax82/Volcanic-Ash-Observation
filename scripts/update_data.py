import html as html_lib
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

WIB = timezone(timedelta(hours=7))
VAAC_URL = 'https://www.bom.gov.au/products/Volc_ash_latest.shtml'
BMKG_URL = 'https://www.bmkg.go.id/siaran-pers/imbas-sebaran-abu-vulknaik-anak-krakatau-8-bandara-ditutup-sementara-bmkg-minta-waspada-dan-tenang'
CURRENT_FILE = 'data/current.json'


def fetch(url):
    req = Request(url, headers={'User-Agent': 'Volcanic-Ash-Observation/1.1'})
    with urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', errors='ignore')


def clean_html(text):
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def parse_vaac(html):
    text = clean_html(html)
    # Find every Krakatau advisory and select the highest advisory number.
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
    return {
        'advisory': m.group(3),
        'dtg': m.group(2),
        'status': 'verified',
        'lower_level': f'SFC/FL{lower.group(1)}' if lower else None,
        'lower_direction': lower.group(2).upper() if lower else None,
        'higher_level': f'SFC/FL{higher.group(1)}' if higher else None,
        'higher_direction': higher.group(2).upper() if higher else None,
        'raw_summary': block[:2200],
    }


def parse_bmkg(html):
    text = clean_html(html)
    m = re.search(
        r'ketinggian hingga\s*15\.000 kaki.*?bergerak ke arah barat dengan kecepatan\s*15 knot',
        text,
        re.I,
    )
    affected = None
    affected_m = re.search(
        r'mencakup sebagian\s+(.*?)(?:, serta perairan|\.)',
        text,
        re.I,
    )
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


def load_previous():
    try:
        with open(CURRENT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def main():
    previous = load_previous()
    now = datetime.now(WIB).isoformat(timespec='seconds')
    errors = []

    try:
        vaac = parse_vaac(fetch(VAAC_URL))
    except Exception as exc:
        errors.append(f'VAAC: {exc}')
        vaac = previous.get('sources', {}).get('vaac', {'status': 'STALE'}) if previous else {'status': 'UNAVAILABLE'}
        vaac['status'] = 'stale' if previous else 'unavailable'

    try:
        bmkg = parse_bmkg(fetch(BMKG_URL))
    except Exception as exc:
        errors.append(f'BMKG: {exc}')
        bmkg = previous.get('sources', {}).get('bmkg', {'status': 'STALE'}) if previous else {'status': 'unavailable'}
        bmkg['status'] = 'stale' if previous else 'unavailable'

    # Conservative decision: an unavailable/stale critical source never upgrades risk to safe.
    vaac_ok = vaac.get('status') == 'verified'
    bmkg_ok = bmkg.get('status') == 'verified'
    status = 'HIGH'
    recommendation = 'CONTINUE PJJ'
    confidence = 'HIGH' if vaac_ok and bmkg_ok else 'REDUCED'

    data = {
        'status': status,
        'recommendation': recommendation,
        'confidence': confidence,
        'fetched_at': now,
        'source_errors': errors,
        'sources': {
            'bmkg': bmkg,
            'vaac': vaac,
            'pvmbg': {'status': 'Level III / Siaga'},
            'air_quality': {'status': 'NOT_CONNECTED', 'ispu': None, 'pm25': None},
        },
        'decision': {
            'ash_trajectory': 'CRITICAL' if vaac_ok else 'DATA STALE',
            'air_quality': 'DATA NEEDED',
            'school_exposure': 'VERIFY',
            'travel': 'WATCH',
            'official_guidance': 'ACTIVE',
        },
    }

    with open(CURRENT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
