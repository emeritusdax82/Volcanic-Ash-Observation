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
IQAIR_URL = 'https://www.iqair.com/id/air-quality/indonesia/banten/tangerang'
AQIIN_URL = 'https://www.aqi.in/id/dashboard/indonesia/banten/tangerang'
SCHOOL_LAT = -6.0995
SCHOOL_LON = 106.7197
CURRENT_FILE = 'data/current.json'


def fetch(url):
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; Volcanic-Ash-Observation/2.0; +https://github.com/emeritusdax82/Volcanic-Ash-Observation)'})
    with urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', errors='ignore')


def clean_html(text):
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def parse_coord(token):
    m = re.fullmatch(r'([NS])(\d{4})\s+([EW])(\d{5})', token.strip(), re.I)
    if not m: raise ValueError(f'Invalid VAAC coordinate: {token}')
    lat_raw, lon_raw = m.group(2), m.group(4)
    lat = int(lat_raw[:2]) + int(lat_raw[2:]) / 60.0
    lon = int(lon_raw[:3]) + int(lon_raw[3:]) / 60.0
    if m.group(1).upper() == 'S': lat = -lat
    if m.group(3).upper() == 'W': lon = -lon
    return [round(lat, 5), round(lon, 5)]


def extract_polygon(section):
    m = re.search(r'SFC/FL\d+\s+(.*?)(?=\s+SFC/FL\d+\s+|\s+RMK:|\s+NXT ADVISORY:|$)', section, re.I)
    if not m: return None
    coords = [parse_coord(x) for x in re.findall(r'[NS]\d{4}\s+[EW]\d{5}', m.group(1), re.I)]
    return coords if len(coords) >= 3 else None


def parse_vaac(html):
    text = clean_html(html)
    blocks = re.split(r'(?=VA ADVISORY\s+DTG:)', text, flags=re.I)
    candidates = []
    for block in blocks:
        if not re.search(r'VAAC:\s*DARWIN', block, re.I): continue
        if not re.search(r'VOLCANO:\s*KRAKATAU\b', block, re.I): continue
        adv = re.search(r'ADVISORY NR:\s*2026/(\d+)', block, re.I)
        dtg = re.search(r'DTG:\s*(202609\d{2}/\d{4}Z)', block, re.I)
        if adv and dtg: candidates.append((int(adv.group(1)), dtg.group(1), block))
    if not candidates: raise RuntimeError('Latest Darwin Krakatau advisory not found')
    _, dtg, block = max(candidates, key=lambda x: x[0])
    adv = re.search(r'ADVISORY NR:\s*(2026/\d+)', block, re.I).group(1)
    levels = re.findall(r'VA TO FL(\d+)\s+MOV\s+([A-Z]+)', block, re.I)
    low = levels[0] if levels else None
    high = levels[1] if len(levels) > 1 else None
    obs = re.search(r'OBS VA CLD:\s*(.*?)(?=\s+FCST VA CLD \+6 HR:|\s+RMK:|\s+NXT ADVISORY:|$)', block, re.I)
    polygons = {'observed': extract_polygon(obs.group(1)) if obs else None}
    for hours in (6, 12, 18):
        m = re.search(rf'FCST VA CLD \+{hours} HR:\s*(.*?)(?=\s+FCST VA CLD \+\d+ HR:|\s+RMK:|\s+NXT ADVISORY:|$)', block, re.I)
        polygons[f'forecast_{hours}h'] = extract_polygon(m.group(1)) if m else None
    if not any(polygons.values()): raise RuntimeError(f'Krakatau advisory {adv} found but no low-level polygons parsed')
    return {'advisory':adv,'dtg':dtg,'status':'verified','volcano':'KRAKATAU','lower_level':f'SFC/FL{low[0]}' if low else None,'lower_direction':low[1].upper() if low else None,'higher_level':f'FL{high[0]}' if high else None,'higher_direction':high[1].upper() if high else None,'low_level_polygons':polygons,'raw_summary':block[:3000]}


def parse_bmkg(html):
    text = clean_html(html)
    m = re.search(r'ketinggian hingga\s*15\.000 kaki.*?bergerak ke arah barat dengan kecepatan\s*15 knot', text, re.I)
    affected_m = re.search(r'mencakup sebagian\s+(.*?)(?:, serta perairan|\.)', text, re.I)
    return {'status':'verified' if m else 'page_fetched','ash_altitude_ft':15000 if m else None,'direction':'W' if m else None,'speed_kt':15 if m else None,'affected_region':affected_m.group(1).strip() if affected_m else None,'source_note':'Latest BMKG Anak Krakatau press release fetched by scheduled workflow.'}


def parse_air_quality():
    params = urlencode({'latitude':SCHOOL_LAT,'longitude':SCHOOL_LON,'current':'pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,european_aqi','timezone':'Asia/Jakarta'})
    raw = json.loads(fetch(AQ_URL + '?' + params)); current = raw.get('current', {})
    if current.get('pm2_5') is None and current.get('pm10') is None: raise RuntimeError('No current PM data returned')
    return {'status':'verified_model','source':'Open-Meteo Air Quality API / CAMS model','source_type':'modelled, not ground sensor','latitude':SCHOOL_LAT,'longitude':SCHOOL_LON,'pm25':current.get('pm2_5'),'pm10':current.get('pm10'),'european_aqi':current.get('european_aqi'),'carbon_monoxide':current.get('carbon_monoxide'),'nitrogen_dioxide':current.get('nitrogen_dioxide'),'sulphur_dioxide':current.get('sulphur_dioxide'),'ozone':current.get('ozone'),'observed_at':current.get('time'),'note':'PM values are atmospheric model estimates for the school coordinate, not official Indonesian ISPU station readings.'}


def parse_iqair(html):
    text = clean_html(html)
    aqi = re.search(r'(\d{1,3})\s+AQI\+?\s+US', text, re.I)
    pm25 = re.search(r'PM2\.5.*?(\d+(?:\.\d+)?)\s*µg/m³', text, re.I)
    pm10 = re.search(r'PM10.*?(\d+(?:\.\d+)?)\s*µg/m³', text, re.I)
    if not aqi and not pm25: raise RuntimeError('IQAir Tangerang values not found')
    return {'status':'verified_web','source':'IQAir Tangerang','source_url':IQAIR_URL,'source_type':'secondary station/aggregate; not official Indonesian ISPU','location':'Tangerang, Banten','aqi_us':int(aqi.group(1)) if aqi else None,'pm25':float(pm25.group(1)) if pm25 else None,'pm10':float(pm10.group(1)) if pm10 else None,'fetched_at':datetime.now(WIB).isoformat(timespec='seconds')}


def parse_aqi_in(html):
    text = clean_html(html)
    aqi = re.search(r'AQI\s*Langsung\s*(\d{1,3})\s+AQI\s*\(US\)', text, re.I)
    if not aqi:
        aqi = re.search(r'(\d{1,3})\s+AQI\s*\(US\)', text, re.I)
    pm25 = re.search(r'PM2\.5\s*[:：]?\s*(\d+(?:\.\d+)?)\s*µg/m³', text, re.I)
    pm10 = re.search(r'pm10\s*[:：]?\s*(\d+(?:\.\d+)?)\s*µg/m³', text, re.I)
    updated = re.search(r'(?:Terakhir Diperbarui|Last Updated)\s*:?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)', text, re.I)
    if not aqi and not pm25: raise RuntimeError('AQI.in Tangerang page did not expose current AQI/PM values')
    return {'status':'verified_web','source':'AQI.in Tangerang','source_url':AQIIN_URL,'source_type':'secondary air-quality platform; not official Indonesian ISPU','location':'Tangerang, Banten','aqi_us':int(aqi.group(1)) if aqi else None,'pm25':float(pm25.group(1)) if pm25 else None,'pm10':float(pm10.group(1)) if pm10 else None,'page_updated_at':updated.group(1) if updated else None,'fetched_at':datetime.now(WIB).isoformat(timespec='seconds'),'note':'Regional corroboration. Use measured local/official sensor data in preference when available.'}


def load_previous():
    try:
        with open(CURRENT_FILE, encoding='utf-8') as f: return json.load(f)
    except Exception: return None


def stale_copy(previous, key, default):
    value = previous.get('sources',{}).get(key, default) if previous else default
    value = dict(value)
    value['status'] = 'stale' if previous else 'unavailable'
    return value


def main():
    previous = load_previous(); now = datetime.now(WIB).isoformat(timespec='seconds'); errors=[]
    try: vaac = parse_vaac(fetch(VAAC_URL))
    except Exception as exc: errors.append(f'VAAC: {exc}'); vaac = stale_copy(previous,'vaac',{'status':'UNAVAILABLE'})
    try: bmkg = parse_bmkg(fetch(BMKG_URL))
    except Exception as exc: errors.append(f'BMKG: {exc}'); bmkg = stale_copy(previous,'bmkg',{'status':'unavailable'})
    try: air_quality = parse_air_quality()
    except Exception as exc: errors.append(f'AQ model: {exc}'); air_quality = stale_copy(previous,'air_quality',{'status':'unavailable','pm25':None,'pm10':None}); air_quality['status']='stale_model' if previous else 'unavailable'
    try:
        iqair=None; last=None
        for url in (IQAIR_URL, IQAIR_URL.replace('/id/','/as/')):
            try: iqair=parse_iqair(fetch(url)); break
            except Exception as exc: last=exc
        if iqair is None: raise last or RuntimeError('IQAir unavailable')
    except Exception as exc: errors.append(f'IQAir: {exc}'); iqair=stale_copy(previous,'iqair',{'status':'unavailable','aqi_us':None,'pm25':None,'pm10':None})
    try: aqi_in=parse_aqi_in(fetch(AQIIN_URL))
    except Exception as exc: errors.append(f'AQI.in: {exc}'); aqi_in=stale_copy(previous,'aqi_in',{'status':'unavailable','aqi_us':None,'pm25':None,'pm10':None,'source':'AQI.in Tangerang','source_url':AQIIN_URL})
    vaac_ok=vaac.get('status')=='verified'; bmkg_ok=bmkg.get('status')=='verified'; aq_ok=air_quality.get('pm25') is not None or air_quality.get('pm10') is not None
    iq_ok=iqair.get('aqi_us') is not None or iqair.get('pm25') is not None
    aqi_in_ok=aqi_in.get('aqi_us') is not None or aqi_in.get('pm25') is not None
    if aq_ok and (iq_ok or aqi_in_ok): aq_decision='MODEL + LOCAL CORROBORATION'
    elif aq_ok: aq_decision='MODEL CONNECTED'
    elif aqi_in_ok or iq_ok: aq_decision='CORROBORATION ONLY'
    else: aq_decision='DATA NEEDED'
    data={'status':'HIGH','recommendation':'CONTINUE PJJ','confidence':'HIGH' if vaac_ok and bmkg_ok and aq_ok else 'REDUCED','fetched_at':now,'source_errors':errors,'sources':{'bmkg':bmkg,'vaac':vaac,'pvmbg':{'status':'Level III / Siaga'},'air_quality':air_quality,'iqair':iqair,'aqi_in':aqi_in},'decision':{'ash_trajectory':'CRITICAL' if vaac_ok else 'DATA STALE','air_quality':aq_decision,'school_exposure':'VERIFY','travel':'WATCH','official_guidance':'ACTIVE'}}
    with open(CURRENT_FILE,'w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2)

if __name__ == '__main__': main()
