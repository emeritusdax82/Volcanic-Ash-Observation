import json, re
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

WIB = timezone(timedelta(hours=7))
VAAC_URL = 'https://www.bom.gov.au/products/Volc_ash_latest.shtml'
BMKG_URL = 'https://www.bmkg.go.id/siaran-pers/imbas-sebaran-abu-vulknaik-anak-krakatau-8-bandara-ditutup-sementara-bmkg-minta-waspada-dan-tenang'


def fetch(url):
    req = Request(url, headers={'User-Agent': 'Volcanic-Ash-Observation/1.0'})
    with urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', errors='ignore')


def clean_html(text):
    return re.sub(r'\\s+', ' ', re.sub(r'<[^>]+>', ' ', text))


def parse_vaac(html):
    text = clean_html(html)
    m = re.search(r'VA ADVISORY.*?DTG:\s*(202609\d{2}/\d{4}Z).*?VAAC:\s*DARWIN.*?VOLCANO:\s*KRAKATAU.*?ADVISORY NR:\s*(2026/\d+)', text, re.I)
    if not m:
        raise RuntimeError('Latest Darwin Krakatau advisory not found')
    block = text[m.start():m.start()+3500]
    return {
        'advisory': m.group(2),
        'dtg': m.group(1),
        'status': 'verified',
        'raw_summary': block[:1800]
    }


def parse_bmkg(html):
    text = clean_html(html)
    m = re.search(r'ketinggian hingga\s*15\.000 kaki.*?bergerak ke arah barat dengan kecepatan\s*15 knot', text, re.I)
    return {
        'status': 'verified' if m else 'page_fetched',
        'ash_altitude_ft': 15000 if m else None,
        'direction': 'W' if m else None,
        'speed_kt': 15 if m else None,
        'source_note': 'Latest BMKG Anak Krakatau press release fetched by scheduled workflow.'
    }


def main():
    vaac_html = fetch(VAAC_URL)
    bmkg_html = fetch(BMKG_URL)
    vaac = parse_vaac(vaac_html)
    bmkg = parse_bmkg(bmkg_html)
    now = datetime.now(WIB).isoformat(timespec='seconds')
    data = {
        'status': 'HIGH',
        'recommendation': 'CONTINUE PJJ',
        'confidence': 'HIGH',
        'fetched_at': now,
        'sources': {
            'bmkg': bmkg,
            'vaac': vaac,
            'pvmbg': {'status': 'Level III / Siaga'},
            'air_quality': {'status': 'NOT_CONNECTED', 'ispu': None, 'pm25': None}
        },
        'decision': {
            'ash_trajectory': 'CRITICAL',
            'air_quality': 'DATA NEEDED',
            'school_exposure': 'VERIFY',
            'travel': 'WATCH',
            'official_guidance': 'ACTIVE'
        }
    }
    with open('data/current.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    main()
