from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from bs4 import BeautifulSoup
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import os
import atexit
import threading
import asyncio
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

warnings.filterwarnings('ignore')

app = Flask(__name__, static_folder='static')
CORS(app)

# Configuracion
DATABASE_URL = os.environ.get('DATABASE_URL')
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
REFRESH_API_KEY = os.environ.get('REFRESH_API_KEY')
# JSON del service account de Firebase (contenido inline o ruta a archivo)
# Se usa para push a dispositivos Android via FCM
FIREBASE_SERVICE_ACCOUNT = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
# Credenciales APNs para push a dispositivos iOS (directo, sin Firebase)
APNS_TEAM_ID = os.environ.get('APNS_TEAM_ID')
APNS_KEY_ID = os.environ.get('APNS_KEY_ID')
APNS_KEY = os.environ.get('APNS_KEY')  # contenido del .p8 inline o ruta a archivo
APNS_TOPIC = os.environ.get('APNS_TOPIC', 'com.brechacambiaria.app')
APNS_USE_SANDBOX = os.environ.get('APNS_USE_SANDBOX', '').lower() in ('1', 'true', 'yes')
BRECHA_CHANGE_THRESHOLD = 5.0
VE_TZ = ZoneInfo("America/Caracas")

# Archivos JSON (fallback si no hay PostgreSQL)
HISTORY_FILE = 'price_history.json'
SUBSCRIBERS_FILE = 'telegram_subscribers.json'
LAST_BRECHA_FILE = 'last_brecha.json'
DEVICE_TOKENS_FILE = 'device_tokens.json'

# ============== CONEXION POSTGRESQL ==============

_db_pool = None
_db_pool_lock = threading.Lock()

def _get_db_pool():
    """Crea el pool de conexiones una sola vez y lo retorna"""
    global _db_pool
    if not DATABASE_URL:
        return None
    if _db_pool is None:
        with _db_pool_lock:
            if _db_pool is None:
                try:
                    from psycopg2 import pool
                    # Render usa postgres:// pero psycopg2 necesita postgresql://
                    db_url = DATABASE_URL.replace('postgres://', 'postgresql://')
                    _db_pool = pool.ThreadedConnectionPool(minconn=1, maxconn=5, dsn=db_url)
                except Exception as e:
                    print(f"Error creando pool de PostgreSQL: {e}")
                    return None
    return _db_pool

@contextmanager
def db_connection():
    """Presta una conexion del pool y la devuelve al salir.
    Si hubo un error, la conexion se descarta para no reusar
    una conexion en mal estado."""
    db_pool = _get_db_pool()
    if db_pool is None:
        raise RuntimeError("PostgreSQL no disponible")
    conn = db_pool.getconn()
    try:
        yield conn
    except Exception:
        db_pool.putconn(conn, close=True)
        conn = None
        raise
    finally:
        if conn is not None:
            db_pool.putconn(conn)

def has_database():
    """Indica si hay PostgreSQL configurado y accesible"""
    return _get_db_pool() is not None

def init_database():
    """Crea las tablas si no existen"""
    if not has_database():
        print("PostgreSQL no disponible, usando archivos JSON")
        return False

    try:
        with db_connection() as conn:
            cur = conn.cursor()

            # Tabla de historial de precios
            cur.execute('''
                CREATE TABLE IF NOT EXISTS price_history (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    bcv_usd DECIMAL(10,2),
                    bcv_eur DECIMAL(10,2),
                    usdt_avg DECIMAL(10,2),
                    brecha_usdt_usd DECIMAL(10,2),
                    brecha_usdt_eur DECIMAL(10,2),
                    brecha_eur_usd DECIMAL(10,2),
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Promedios de compra/venta de Binance por separado
            cur.execute('ALTER TABLE price_history ADD COLUMN IF NOT EXISTS usdt_buy DECIMAL(10,2)')
            cur.execute('ALTER TABLE price_history ADD COLUMN IF NOT EXISTS usdt_sell DECIMAL(10,2)')

            # Tabla de suscriptores de Telegram
            cur.execute('''
                CREATE TABLE IF NOT EXISTS telegram_subscribers (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT UNIQUE NOT NULL,
                    subscribed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Tabla de dispositivos moviles (push notifications FCM)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS device_tokens (
                    id SERIAL PRIMARY KEY,
                    token TEXT UNIQUE NOT NULL,
                    platform VARCHAR(20),
                    registered_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Tabla de configuracion (para guardar ultima brecha)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS app_settings (
                    key VARCHAR(50) PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Indice para busquedas por fecha
            cur.execute('''
                CREATE INDEX IF NOT EXISTS idx_price_history_timestamp
                ON price_history(timestamp DESC)
            ''')

            conn.commit()
            cur.close()
        print("PostgreSQL inicializado correctamente")
        return True
    except Exception as e:
        print(f"Error inicializando PostgreSQL: {e}")
        return False

# ============== FUNCIONES DE DATOS ==============

HISTORY_COLUMNS = '''timestamp, bcv_usd, bcv_eur, usdt_avg,
                     brecha_usdt_usd, brecha_usdt_eur, brecha_eur_usd,
                     usdt_buy, usdt_sell'''

def _row_to_entry(row):
    """Convierte una fila de price_history a dict con timestamp ISO + Z"""
    ts = row[0]
    if ts:
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        timestamp_str = ts.isoformat() + 'Z'
    else:
        timestamp_str = None
    return {
        "timestamp": timestamp_str,
        "bcv_usd": float(row[1]) if row[1] is not None else None,
        "bcv_eur": float(row[2]) if row[2] is not None else None,
        "usdt_avg": float(row[3]) if row[3] is not None else None,
        "brecha_usdt_usd": float(row[4]) if row[4] is not None else None,
        "brecha_usdt_eur": float(row[5]) if row[5] is not None else None,
        "brecha_eur_usd": float(row[6]) if row[6] is not None else None,
        "usdt_buy": float(row[7]) if row[7] is not None else None,
        "usdt_sell": float(row[8]) if row[8] is not None else None
    }

def _load_history_json():
    """Carga el historial completo desde el archivo JSON (solo desarrollo)"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def load_history(start=None, end=None, limit=100, offset=0):
    """Carga historial filtrado. start/end son datetime (UTC naive) opcionales.
    Retorna (registros en orden ascendente, total de registros que calzan)."""
    if has_database():
        try:
            where = []
            params = []
            if start:
                where.append('timestamp >= %s')
                params.append(start)
            if end:
                where.append('timestamp <= %s')
                params.append(end)
            where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute(f'SELECT COUNT(*) FROM price_history {where_sql}', params)
                total = cur.fetchone()[0]
                cur.execute(f'''
                    SELECT {HISTORY_COLUMNS}
                    FROM price_history {where_sql}
                    ORDER BY timestamp DESC
                    LIMIT %s OFFSET %s
                ''', params + [limit, offset])
                rows = cur.fetchall()
                cur.close()

            history = [_row_to_entry(row) for row in rows]
            history.reverse()  # el API entrega orden ascendente
            return history, total
        except Exception as e:
            print(f"Error cargando historial de PostgreSQL: {e}")
            return [], 0

    # Fallback a JSON (desarrollo local)
    history = _load_history_json()
    if start or end:
        filtered = []
        for entry in history:
            if not entry.get('timestamp'):
                continue
            entry_time = parse_iso_datetime(entry['timestamp'])
            if start and entry_time < start:
                continue
            if end and entry_time > end:
                continue
            filtered.append(entry)
        history = filtered
    total = len(history)
    if offset:
        history = history[:max(total - offset, 0)]
    return history[-limit:], total

def load_latest_entry():
    """Retorna el registro mas reciente o None"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute(f'''
                    SELECT {HISTORY_COLUMNS}
                    FROM price_history
                    ORDER BY timestamp DESC
                    LIMIT 1
                ''')
                row = cur.fetchone()
                cur.close()
            return _row_to_entry(row) if row else None
        except Exception as e:
            print(f"Error cargando ultimo registro: {e}")
            return None

    history = _load_history_json()
    return history[-1] if history else None

def get_history_stats():
    """Retorna (total de registros, timestamp mas viejo, mas reciente)"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM price_history')
                total, oldest, newest = cur.fetchone()
                cur.close()

            def fmt(ts):
                if not ts:
                    return None
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                return ts.isoformat() + 'Z'
            return total, fmt(oldest), fmt(newest)
        except Exception as e:
            print(f"Error cargando estadisticas: {e}")
            return 0, None, None

    history = _load_history_json()
    if not history:
        return 0, None, None
    return len(history), history[0].get('timestamp'), history[-1].get('timestamp')

def save_history_entry(data):
    """Guarda un registro en el historial"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                timestamp = data.get('timestamp', '').replace('Z', '')
                cur.execute('''
                    INSERT INTO price_history
                    (timestamp, bcv_usd, bcv_eur, usdt_avg, brecha_usdt_usd,
                     brecha_usdt_eur, brecha_eur_usd, usdt_buy, usdt_sell)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    timestamp,
                    data.get('bcv_usd'),
                    data.get('bcv_eur'),
                    data.get('usdt_avg'),
                    data.get('brecha_usdt_usd'),
                    data.get('brecha_usdt_eur'),
                    data.get('brecha_eur_usd'),
                    data.get('usdt_buy'),
                    data.get('usdt_sell')
                ))
                conn.commit()
                cur.close()
            return True
        except Exception as e:
            print(f"Error guardando en PostgreSQL: {e}")
            return False

    # Fallback a JSON
    history = _load_history_json()
    history.append(data)
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)
    return True

def load_subscribers():
    """Carga lista de suscriptores de Telegram"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT chat_id FROM telegram_subscribers')
                rows = cur.fetchall()
                cur.close()
            return [row[0] for row in rows]
        except Exception as e:
            print(f"Error cargando suscriptores: {e}")
            return []

    # Fallback a JSON
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def add_subscriber(chat_id):
    """Agrega un suscriptor"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO telegram_subscribers (chat_id)
                    VALUES (%s)
                    ON CONFLICT (chat_id) DO NOTHING
                ''', (chat_id,))
                conn.commit()
                cur.close()
            return True
        except Exception as e:
            print(f"Error agregando suscriptor: {e}")
            return False

    # Fallback a JSON
    subscribers = load_subscribers()
    if chat_id not in subscribers:
        subscribers.append(chat_id)
        with open(SUBSCRIBERS_FILE, 'w') as f:
            json.dump(subscribers, f)
    return True

def remove_subscriber(chat_id):
    """Remueve un suscriptor"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute('DELETE FROM telegram_subscribers WHERE chat_id = %s', (chat_id,))
                conn.commit()
                cur.close()
            return True
        except Exception as e:
            print(f"Error removiendo suscriptor: {e}")
            return False

    # Fallback a JSON
    subscribers = load_subscribers()
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        with open(SUBSCRIBERS_FILE, 'w') as f:
            json.dump(subscribers, f)
    return True

def load_device_tokens():
    """Carga los dispositivos registrados como lista de (token, platform)"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT token, platform FROM device_tokens')
                rows = cur.fetchall()
                cur.close()
            return [(row[0], row[1]) for row in rows]
        except Exception as e:
            print(f"Error cargando tokens de dispositivos: {e}")
            return []

    # Fallback a JSON
    if os.path.exists(DEVICE_TOKENS_FILE):
        try:
            with open(DEVICE_TOKENS_FILE, 'r') as f:
                return [(d['token'], d.get('platform', 'unknown')) for d in json.load(f)]
        except:
            return []
    return []

def add_device_token(token, platform):
    """Registra un token de dispositivo movil"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO device_tokens (token, platform)
                    VALUES (%s, %s)
                    ON CONFLICT (token) DO NOTHING
                ''', (token, platform))
                conn.commit()
                cur.close()
            return True
        except Exception as e:
            print(f"Error registrando dispositivo: {e}")
            return False

    # Fallback a JSON
    devices = [{'token': t, 'platform': p} for t, p in load_device_tokens()]
    if not any(d['token'] == token for d in devices):
        devices.append({'token': token, 'platform': platform})
        with open(DEVICE_TOKENS_FILE, 'w') as f:
            json.dump(devices, f)
    return True

def remove_device_token(token):
    """Elimina un token de dispositivo (invalido o desregistrado)"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute('DELETE FROM device_tokens WHERE token = %s', (token,))
                conn.commit()
                cur.close()
            return True
        except Exception as e:
            print(f"Error eliminando dispositivo: {e}")
            return False

    # Fallback a JSON
    devices = [{'token': t, 'platform': p} for t, p in load_device_tokens() if t != token]
    with open(DEVICE_TOKENS_FILE, 'w') as f:
        json.dump(devices, f)
    return True

def _load_setting(key, fallback_file):
    """Carga un valor JSON de app_settings (o del archivo local en desarrollo)"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT value FROM app_settings WHERE key = %s', (key,))
                row = cur.fetchone()
                cur.close()
            return json.loads(row[0]) if row else None
        except Exception as e:
            print(f"Error cargando {key}: {e}")
            return None

    # Fallback a JSON
    if os.path.exists(fallback_file):
        try:
            with open(fallback_file, 'r') as f:
                return json.load(f)
        except:
            return None
    return None

def _save_setting(key, data, fallback_file):
    """Guarda un valor JSON en app_settings (o en archivo local en desarrollo)"""
    if has_database():
        try:
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                ''', (key, json.dumps(data)))
                conn.commit()
                cur.close()
            return True
        except Exception as e:
            print(f"Error guardando {key}: {e}")
            return False

    # Fallback a JSON
    with open(fallback_file, 'w') as f:
        json.dump(data, f)
    return True

def load_last_brecha():
    """Carga la ultima brecha guardada"""
    return _load_setting('last_brecha', LAST_BRECHA_FILE)

def save_last_brecha(brecha_data):
    """Guarda la ultima brecha"""
    return _save_setting('last_brecha', brecha_data, LAST_BRECHA_FILE)

def load_last_bcv():
    """Carga los ultimos valores del BCV guardados"""
    return _load_setting('last_bcv', 'last_bcv.json')

def save_last_bcv(bcv_data):
    """Guarda los ultimos valores del BCV"""
    return _save_setting('last_bcv', bcv_data, 'last_bcv.json')

# ============== FUNCIONES DE PRECIOS ==============

def get_bcv_prices():
    try:
        response = requests.get('https://www.bcv.org.ve/', verify=False, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        prices = {'usd': None, 'eur': None}

        dolar_section = soup.find('div', {'id': 'dolar'})
        if dolar_section:
            strong = dolar_section.find('strong')
            if strong:
                valor = strong.get_text(strip=True).replace('.', '').replace(',', '.')
                prices['usd'] = float(valor)

        euro_section = soup.find('div', {'id': 'euro'})
        if euro_section:
            strong = euro_section.find('strong')
            if strong:
                valor = strong.get_text(strip=True).replace('.', '').replace(',', '.')
                prices['eur'] = float(valor)

        return prices
    except Exception as e:
        print(f"Error obteniendo BCV: {e}")
        return {'usd': None, 'eur': None}

def get_binance_p2p_prices():
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    results = {"buy": [], "sell": []}

    for trade_type in ["BUY", "SELL"]:
        payload = {
            "fiat": "VES",
            "page": 1,
            "rows": 10,
            "tradeType": trade_type,
            "asset": "USDT",
            "countries": [],
            "proMerchantAds": False,
            "publisherType": "merchant",
            "payTypes": []
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            data = response.json()
            for ad in data.get("data", [])[1:]:
                adv = ad.get("adv", {})
                price = float(adv.get("price", 0))
                available = float(adv.get("surplusAmount", 0))
                if available >= 50 and 300 < price < 1000:
                    results[trade_type.lower()].append({
                        "price": price,
                        "available": available
                    })
        except Exception as e:
            print(f"Error obteniendo Binance {trade_type}: {e}")
    return results

def calculate_weighted_average(ads):
    if not ads:
        return None
    total_weight = sum(ad["available"] for ad in ads)
    if total_weight == 0:
        return None
    return sum(ad["price"] * ad["available"] for ad in ads) / total_weight

def fetch_and_calculate_prices():
    bcv_prices = get_bcv_prices()
    binance_data = get_binance_p2p_prices()

    buy_avg = calculate_weighted_average(binance_data["buy"])
    sell_avg = calculate_weighted_average(binance_data["sell"])
    usdt_avg = (buy_avg + sell_avg) / 2 if buy_avg and sell_avg else None

    brecha_usdt_usd = None
    if bcv_prices['usd'] and usdt_avg:
        brecha_usdt_usd = ((usdt_avg - bcv_prices['usd']) / bcv_prices['usd']) * 100

    brecha_usdt_eur = None
    if bcv_prices['eur'] and usdt_avg:
        brecha_usdt_eur = ((usdt_avg - bcv_prices['eur']) / bcv_prices['eur']) * 100

    brecha_eur_usd = None
    if bcv_prices['usd'] and bcv_prices['eur']:
        brecha_eur_usd = ((bcv_prices['eur'] - bcv_prices['usd']) / bcv_prices['usd']) * 100

    timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + 'Z'

    return {
        "timestamp": timestamp,
        "bcv_usd": bcv_prices['usd'],
        "bcv_eur": bcv_prices['eur'],
        "usdt_avg": round(usdt_avg, 2) if usdt_avg is not None else None,
        "usdt_buy": round(buy_avg, 2) if buy_avg is not None else None,
        "usdt_sell": round(sell_avg, 2) if sell_avg is not None else None,
        "brecha_usdt_usd": round(brecha_usdt_usd, 2) if brecha_usdt_usd is not None else None,
        "brecha_usdt_eur": round(brecha_usdt_eur, 2) if brecha_usdt_eur is not None else None,
        "brecha_eur_usd": round(brecha_eur_usd, 2) if brecha_eur_usd is not None else None
    }

def get_latest_data():
    latest = load_latest_entry()
    if latest:
        return latest
    return fetch_and_calculate_prices()

# ============== NOTIFICACIONES PUSH (FCM) ==============

_fcm_creds = None
_fcm_project_id = None
_fcm_lock = threading.Lock()

def _get_fcm_credentials():
    """Carga (una sola vez) las credenciales del service account de Firebase.
    Retorna (credentials, project_id) o (None, None) si no esta configurado."""
    global _fcm_creds, _fcm_project_id
    if not FIREBASE_SERVICE_ACCOUNT:
        return None, None
    if _fcm_creds is None:
        with _fcm_lock:
            if _fcm_creds is None:
                try:
                    from google.oauth2 import service_account
                    raw = FIREBASE_SERVICE_ACCOUNT.strip()
                    if raw.startswith('{'):
                        info = json.loads(raw)
                    else:
                        with open(raw, 'r') as f:
                            info = json.load(f)
                    _fcm_creds = service_account.Credentials.from_service_account_info(
                        info, scopes=['https://www.googleapis.com/auth/firebase.messaging'])
                    _fcm_project_id = info.get('project_id')
                except Exception as e:
                    print(f"Error cargando credenciales de Firebase: {e}")
                    return None, None
    return _fcm_creds, _fcm_project_id

def _send_fcm(tokens, title, body):
    """Envia push a dispositivos Android via FCM HTTP v1"""
    if not tokens:
        return 0
    creds, project_id = _get_fcm_credentials()
    if not creds or not project_id:
        return 0

    try:
        from google.auth.transport.requests import Request as GoogleAuthRequest
        if not creds.valid:
            creds.refresh(GoogleAuthRequest())
    except Exception as e:
        print(f"Error refrescando token de FCM: {e}")
        return 0

    url = f'https://fcm.googleapis.com/v1/projects/{project_id}/messages:send'
    headers = {
        'Authorization': f'Bearer {creds.token}',
        'Content-Type': 'application/json'
    }

    sent = 0
    for token in tokens:
        payload = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body}
            }
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                sent += 1
            elif resp.status_code in (400, 404) and 'UNREGISTERED' in resp.text:
                print(f"Token FCM invalido, eliminando: {token[:20]}...")
                remove_device_token(token)
            else:
                print(f"Error FCM {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"Error enviando push FCM: {e}")
    return sent

_apns_jwt = {'token': None, 'issued_at': 0}

def _get_apns_jwt():
    """Genera (y cachea ~40 min) el JWT ES256 para autenticar contra APNs"""
    import time
    now = time.time()
    if _apns_jwt['token'] and now - _apns_jwt['issued_at'] < 2400:
        return _apns_jwt['token']

    import jwt as pyjwt
    raw = APNS_KEY.strip()
    if raw.startswith('-----'):
        key = raw
    else:
        with open(raw, 'r') as f:
            key = f.read()

    token = pyjwt.encode(
        {'iss': APNS_TEAM_ID, 'iat': int(now)},
        key,
        algorithm='ES256',
        headers={'kid': APNS_KEY_ID}
    )
    _apns_jwt['token'] = token
    _apns_jwt['issued_at'] = now
    return token

def _send_apns(tokens, title, body):
    """Envia push a dispositivos iOS directo via APNs (HTTP/2)"""
    if not tokens:
        return 0
    if not (APNS_TEAM_ID and APNS_KEY_ID and APNS_KEY):
        return 0

    try:
        import httpx
        auth_token = _get_apns_jwt()
    except Exception as e:
        print(f"Error preparando APNs: {e}")
        return 0

    base = 'https://api.sandbox.push.apple.com' if APNS_USE_SANDBOX else 'https://api.push.apple.com'
    headers = {
        'authorization': f'bearer {auth_token}',
        'apns-topic': APNS_TOPIC,
        'apns-push-type': 'alert',
        'apns-priority': '10'
    }
    payload = {'aps': {'alert': {'title': title, 'body': body}, 'sound': 'default'}}

    sent = 0
    try:
        with httpx.Client(http2=True, timeout=10) as client:
            for token in tokens:
                try:
                    resp = client.post(f'{base}/3/device/{token}', json=payload, headers=headers)
                    if resp.status_code == 200:
                        sent += 1
                    elif resp.status_code == 410 or 'BadDeviceToken' in resp.text:
                        print(f"Token APNs invalido, eliminando: {token[:20]}...")
                        remove_device_token(token)
                    else:
                        print(f"Error APNs {resp.status_code}: {resp.text[:200]}")
                except Exception as e:
                    print(f"Error enviando push APNs: {e}")
    except Exception as e:
        print(f"Error de conexion APNs: {e}")
    return sent

def send_push_to_all(title, body):
    """Envia una notificacion push a todos los dispositivos registrados:
    Android via FCM, iOS via APNs. Retorna cuantas se enviaron."""
    devices = load_device_tokens()
    if not devices:
        return 0

    android_tokens = [t for t, p in devices if p != 'ios']
    ios_tokens = [t for t, p in devices if p == 'ios']

    sent = _send_fcm(android_tokens, title, body) + _send_apns(ios_tokens, title, body)
    if sent:
        print(f"[{datetime.now()}] Push enviado a {sent}/{len(devices)} dispositivos")
    return sent

# ============== FUNCIONES DE TELEGRAM ==============

def format_venezuela_timestamp(timestamp_str):
    """Convierte un timestamp ISO UTC a texto en hora de Venezuela"""
    try:
        if timestamp_str:
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1]
            dt = datetime.fromisoformat(timestamp_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(VE_TZ).strftime("%d/%m/%Y %H:%M:%S")
    except:
        pass
    return datetime.now(VE_TZ).strftime("%d/%m/%Y %H:%M:%S")

def format_telegram_message(data, is_alert=False):
    timestamp = format_venezuela_timestamp(data.get("timestamp", ""))

    alert_header = "🚨 *ALERTA DE CAMBIO*\n" if is_alert else ""

    bcv_usd = data.get('bcv_usd') or 0
    bcv_eur = data.get('bcv_eur') or 0
    usdt_avg = data.get('usdt_avg') or 0
    brecha_usdt_usd = data.get('brecha_usdt_usd') or 0
    brecha_usdt_eur = data.get('brecha_usdt_eur') or 0
    brecha_eur_usd = data.get('brecha_eur_usd') or 0

    return f"""{alert_header}📊 *BRECHA CAMBIARIA VENEZUELA*
━━━━━━━━━━━━━━━━━━━━━

💵 *Dolar BCV:* `{bcv_usd:,.2f} VES`
💶 *Euro BCV:* `{bcv_eur:,.2f} VES`
💰 *USDT Binance:* `{usdt_avg:,.2f} VES`

📉 *Brechas Cambiarias:*
   • USDT vs $ BCV: `{brecha_usdt_usd:.2f}%`
   • USDT vs € BCV: `{brecha_usdt_eur:.2f}%`
   • € BCV vs $ BCV: `{brecha_eur_usd:.2f}%`

🕐 _{timestamp} (Hora Venezuela)_

━━━━━━━━━━━━━━━━━━━━━
_Tasas y Brechas de USDT con BCV actualizadas cada minuto. Incluye histórico y calculadora comparativa._

_Información con fines educativos/informativos_

🤖 Bot: t.me/brechacambiariabot
🌐 https://brecha-cambiaria.com
"""

def format_alert_message(data, old_brecha, new_brecha, change):
    base_msg = format_telegram_message(data, is_alert=True)
    direction = "subio" if change > 0 else "bajo"
    alert_info = f"""
⚠️ *La brecha USDT vs $ BCV {direction}*
   • Anterior: `{old_brecha:.2f}%`
   • Actual: `{new_brecha:.2f}%`
   • Cambio: `{change:+.2f}%`
"""
    return base_msg + alert_info

def format_bcv_update_message(data, old_bcv, changes):
    """Formatea mensaje de actualizacion del BCV"""
    timestamp = format_venezuela_timestamp(data.get("timestamp", ""))

    bcv_usd = data.get('bcv_usd') or 0
    bcv_eur = data.get('bcv_eur') or 0
    usdt_avg = data.get('usdt_avg') or 0
    brecha_usdt_usd = data.get('brecha_usdt_usd') or 0
    brecha_usdt_eur = data.get('brecha_usdt_eur') or 0
    brecha_eur_usd = data.get('brecha_eur_usd') or 0

    changes_text = ""
    for currency, change_info in changes.items():
        old_val = change_info['old']
        new_val = change_info['new']
        diff = new_val - old_val
        diff_pct = (diff / old_val * 100) if old_val else 0
        direction = "📈" if diff > 0 else "📉"
        currency_name = "Dólar" if currency == 'usd' else "Euro"

        changes_text += f"""
{direction} *{currency_name} BCV actualizado*
   • Anterior: `{old_val:,.2f} VES`
   • Actual: `{new_val:,.2f} VES`
   • Cambio: `{diff:+,.2f} VES ({diff_pct:+.2f}%)`
"""

    return f"""🔔 *ACTUALIZACION BCV*
━━━━━━━━━━━━━━━━━━━━━
{changes_text}
💵 *Dólar BCV:* `{bcv_usd:,.2f} VES`
💶 *Euro BCV:* `{bcv_eur:,.2f} VES`
💰 *USDT Binance:* `{usdt_avg:,.2f} VES`

📉 *Brechas Cambiarias:*
   • USDT vs $ BCV: `{brecha_usdt_usd:.2f}%`
   • USDT vs € BCV: `{brecha_usdt_eur:.2f}%`
   • € BCV vs $ BCV: `{brecha_eur_usd:.2f}%`

🕐 _{timestamp} (Hora Venezuela)_

━━━━━━━━━━━━━━━━━━━━━
_Información con fines educativos/informativos_

🤖 Bot: t.me/brechacambiariabot
🌐 https://brecha-cambiaria.com
"""

async def send_telegram_message(bot, chat_id, message):
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
        return True
    except Exception as e:
        print(f"Error enviando mensaje a {chat_id}: {e}")
        return False

async def send_scheduled_notification(bot):
    subscribers = load_subscribers()
    if not subscribers:
        print(f"[{datetime.now()}] No hay suscriptores para notificar")
        return

    try:
        data = get_latest_data()
        if data.get("bcv_usd") is None:
            print(f"[{datetime.now()}] No hay datos disponibles")
            return

        message = format_telegram_message(data)

        for chat_id in subscribers:
            await send_telegram_message(bot, chat_id, message)
            print(f"[{datetime.now()}] Notificacion enviada a {chat_id}")

        if data.get("brecha_usdt_usd") is not None:
            save_last_brecha({
                "brecha_usdt_usd": data["brecha_usdt_usd"],
                "timestamp": data.get("timestamp")
            })

    except Exception as e:
        print(f"[{datetime.now()}] Error en notificacion: {e}")

async def check_brecha_change(bot):
    subscribers = load_subscribers()

    try:
        data = get_latest_data()
        current_brecha = data.get("brecha_usdt_usd")

        if current_brecha is None:
            return

        last_brecha_data = load_last_brecha()

        if last_brecha_data is None:
            save_last_brecha({
                "brecha_usdt_usd": current_brecha,
                "timestamp": data.get("timestamp")
            })
            return

        old_brecha = last_brecha_data.get("brecha_usdt_usd", 0)
        change = current_brecha - old_brecha

        if abs(change) >= BRECHA_CHANGE_THRESHOLD:
            print(f"[{datetime.now()}] Cambio detectado: {old_brecha:.2f}% -> {current_brecha:.2f}%")

            message = format_alert_message(data, old_brecha, current_brecha, change)

            for chat_id in subscribers:
                await send_telegram_message(bot, chat_id, message)
                print(f"[{datetime.now()}] Alerta enviada a {chat_id}")

            direction = "subió" if change > 0 else "bajó"
            push_body = (f"La brecha USDT/$ BCV {direction} de {old_brecha:.2f}% "
                         f"a {current_brecha:.2f}% ({change:+.2f}%)")
            await asyncio.to_thread(send_push_to_all, "🚨 Alerta de brecha cambiaria", push_body)

            save_last_brecha({
                "brecha_usdt_usd": current_brecha,
                "timestamp": data.get("timestamp")
            })

    except Exception as e:
        print(f"[{datetime.now()}] Error verificando brecha: {e}")

async def check_bcv_update(bot):
    """Verifica si el BCV actualizo sus tasas y notifica"""
    subscribers = load_subscribers()

    try:
        data = get_latest_data()
        current_usd = data.get("bcv_usd")
        current_eur = data.get("bcv_eur")

        if current_usd is None and current_eur is None:
            return

        last_bcv_data = load_last_bcv()

        if last_bcv_data is None:
            # Primera vez, guardar y salir
            save_last_bcv({
                "bcv_usd": current_usd,
                "bcv_eur": current_eur,
                "timestamp": data.get("timestamp")
            })
            print(f"[{datetime.now()}] BCV inicial guardado: USD={current_usd}, EUR={current_eur}")
            return

        old_usd = last_bcv_data.get("bcv_usd")
        old_eur = last_bcv_data.get("bcv_eur")

        changes = {}

        # Verificar cambio en dolar
        if old_usd and current_usd and old_usd != current_usd:
            changes['usd'] = {'old': old_usd, 'new': current_usd}

        # Verificar cambio en euro
        if old_eur and current_eur and old_eur != current_eur:
            changes['eur'] = {'old': old_eur, 'new': current_eur}

        if changes:
            print(f"[{datetime.now()}] Actualizacion BCV detectada: {changes}")

            message = format_bcv_update_message(data, last_bcv_data, changes)

            for chat_id in subscribers:
                await send_telegram_message(bot, chat_id, message)
                print(f"[{datetime.now()}] Notificacion BCV enviada a {chat_id}")

            push_parts = []
            for currency, change_info in changes.items():
                old_val, new_val = change_info['old'], change_info['new']
                diff_pct = ((new_val - old_val) / old_val * 100) if old_val else 0
                currency_name = "Dólar" if currency == 'usd' else "Euro"
                push_parts.append(f"{currency_name}: {old_val:,.2f} → {new_val:,.2f} Bs ({diff_pct:+.2f}%)")
            push_body = " • ".join(push_parts)
            if data.get('brecha_usdt_usd') is not None:
                push_body += f" • Brecha USDT: {data['brecha_usdt_usd']:.2f}%"
            await asyncio.to_thread(send_push_to_all, "🔔 BCV actualizó sus tasas", push_body)

            # Actualizar ultimo BCV
            save_last_bcv({
                "bcv_usd": current_usd,
                "bcv_eur": current_eur,
                "timestamp": data.get("timestamp")
            })

    except Exception as e:
        print(f"[{datetime.now()}] Error verificando actualizacion BCV: {e}")

def run_telegram_bot():
    """Ejecuta el bot de Telegram en un thread separado"""
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN no configurado. Bot de Telegram desactivado.")
        return

    try:
        from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
    except ImportError:
        print("python-telegram-bot no instalado. Bot de Telegram desactivado.")
        return

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("📊 Consultar Brecha", callback_data="brecha")],
            [
                InlineKeyboardButton("🔔 Suscribirse", callback_data="subscribe"),
                InlineKeyboardButton("🔕 Desuscribirse", callback_data="unsubscribe")
            ]
        ]
        await update.message.reply_text(
            "📈 *Bot Brecha Cambiaria Venezuela*\n\n"
            "Recibe notificaciones automaticas:\n"
            "• 8:00 AM, 2:00 PM y 10:00 PM\n"
            "• Alertas cuando la brecha cambie mas del 5%\n\n"
            "Presiona los botones para interactuar:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id

        keyboard = [
            [InlineKeyboardButton("📊 Consultar Brecha", callback_data="brecha")],
            [
                InlineKeyboardButton("🔔 Suscribirse", callback_data="subscribe"),
                InlineKeyboardButton("🔕 Desuscribirse", callback_data="unsubscribe")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if query.data == "brecha":
            await query.edit_message_text("⏳ Consultando datos...")
            try:
                data = get_latest_data()
                if data.get("bcv_usd") is None:
                    await query.edit_message_text(
                        "❌ Error obteniendo datos. Intenta de nuevo.",
                        reply_markup=reply_markup
                    )
                    return
                message = format_telegram_message(data)
                await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)
            except Exception as e:
                await query.edit_message_text(f"❌ Error: {str(e)}", reply_markup=reply_markup)

        elif query.data == "subscribe":
            subscribers = load_subscribers()
            if chat_id not in subscribers:
                add_subscriber(chat_id)
                await query.edit_message_text(
                    "✅ *Suscrito exitosamente*\n\n"
                    "Recibiras notificaciones:\n"
                    "• 8:00 AM, 2:00 PM y 10:00 PM\n"
                    "• Alertas de cambio mayor al 5%",
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(
                    "ℹ️ Ya estas suscrito a las notificaciones.",
                    reply_markup=reply_markup
                )

        elif query.data == "unsubscribe":
            subscribers = load_subscribers()
            if chat_id in subscribers:
                remove_subscriber(chat_id)
                await query.edit_message_text(
                    "🔕 *Desuscrito exitosamente*\n\n"
                    "Ya no recibiras notificaciones automaticas.",
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(
                    "ℹ️ No estabas suscrito.",
                    reply_markup=reply_markup
                )

    async def scheduled_job_wrapper(context):
        await send_scheduled_notification(context.bot)

    async def brecha_check_wrapper(context):
        await check_brecha_change(context.bot)

    async def bcv_check_wrapper(context):
        await check_bcv_update(context.bot)

    async def ignore_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ignora cualquier mensaje de texto y recuerda usar botones"""
        keyboard = [
            [InlineKeyboardButton("📊 Consultar Brecha", callback_data="brecha")],
            [
                InlineKeyboardButton("🔔 Suscribirse", callback_data="subscribe"),
                InlineKeyboardButton("🔕 Desuscribirse", callback_data="unsubscribe")
            ]
        ]
        await update.message.reply_text(
            "⚠️ Este bot solo funciona con botones.\n\nUsa las opciones de abajo:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def run_bot_async():
        from telegram.ext import MessageHandler, filters
        from datetime import time as dt_time

        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ignore_messages))

        # Programar notificaciones (hora UTC)
        # 8:00 AM Venezuela = 12:00 UTC
        # 2:00 PM Venezuela = 18:00 UTC
        # 10:00 PM Venezuela = 02:00 UTC
        job_queue = application.job_queue
        job_queue.run_daily(scheduled_job_wrapper, time=dt_time(hour=12, minute=0), name='morning')
        job_queue.run_daily(scheduled_job_wrapper, time=dt_time(hour=18, minute=0), name='afternoon')
        job_queue.run_daily(scheduled_job_wrapper, time=dt_time(hour=2, minute=0), name='night')

        # Verificar cambio de brecha cada hora
        job_queue.run_repeating(brecha_check_wrapper, interval=3600, first=60, name='brecha_check')

        # Verificar actualizacion del BCV cada 5 minutos
        job_queue.run_repeating(bcv_check_wrapper, interval=300, first=30, name='bcv_check')

        print("Bot de Telegram iniciado")
        print("  - Notificaciones: 8:00 AM, 2:00 PM, 10:00 PM (Venezuela)")
        print("  - Verificacion de brecha: cada hora")
        print("  - Verificacion de BCV: cada 5 minutos")

        # Iniciar sin señales (compatible con threads)
        await application.initialize()
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        # Mantener el bot corriendo
        while True:
            await asyncio.sleep(3600)

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot_async())
        except Exception as e:
            print(f"Error en bot de Telegram: {e}")

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    print("Bot de Telegram ejecutandose en thread separado")

# ============== JOBS DEL SCHEDULER ==============

def update_prices_job():
    print(f"[{datetime.now().isoformat()}] Actualizando precios...")
    try:
        current_data = fetch_and_calculate_prices()
        save_history_entry(current_data)
        print(f"[{datetime.now().isoformat()}] Precios actualizados")
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error: {e}")

# ============== RUTAS API ==============

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js')

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('static', 'sitemap.xml')

@app.route('/robots.txt')
def robots():
    return send_from_directory('static', 'robots.txt')

@app.route('/api/stats')
def get_stats():
    subscribers = load_subscribers()
    total, oldest, newest = get_history_stats()

    return jsonify({
        "subscribers": len(subscribers),
        "total_records": total,
        "oldest_record": oldest,
        "newest_record": newest,
        "database": "PostgreSQL" if has_database() else "JSON"
    })

@app.route('/og-image.jpg')
def og_image():
    return send_from_directory('static', 'og-image.jpg')

@app.route('/favicon.png')
def favicon():
    return send_from_directory('static', 'favicon.png')

@app.route('/favicon.ico')
def favicon_ico():
    return send_from_directory('static', 'favicon.png')

@app.route('/openapi.json')
def openapi_spec():
    return send_from_directory('static', 'openapi.json')

@app.route('/api')
def api_docs():
    return '''<!DOCTYPE html>
<html>
<head>
    <title>API - Brecha Cambiaria Venezuela</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        SwaggerUIBundle({
            url: "/openapi.json",
            dom_id: '#swagger-ui',
            presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
            layout: "BaseLayout"
        });
    </script>
</body>
</html>'''

EMPTY_ENTRY = {
    "timestamp": None, "bcv_usd": None, "bcv_eur": None,
    "usdt_avg": None, "usdt_buy": None, "usdt_sell": None,
    "brecha_usdt_usd": None, "brecha_usdt_eur": None, "brecha_eur_usd": None
}

@app.route('/api/prices')
def get_prices():
    latest = load_latest_entry()
    return jsonify(latest if latest else EMPTY_ENTRY)

@app.route('/api/latest')
def get_latest():
    latest = load_latest_entry()
    return jsonify(latest if latest else EMPTY_ENTRY)

@app.route('/api/devices', methods=['POST'])
def register_device():
    """Registra un dispositivo movil para notificaciones push"""
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    platform = (data.get('platform') or 'unknown')[:20]

    if not token or len(token) > 4096:
        return jsonify({"success": False, "error": "token requerido"}), 400

    ok = add_device_token(token, platform)
    return jsonify({"success": bool(ok)})

@app.route('/api/refresh', methods=['POST'])
def refresh_prices():
    if not REFRESH_API_KEY or request.headers.get('X-API-Key') != REFRESH_API_KEY:
        return jsonify({"success": False, "error": "No autorizado"}), 401
    try:
        current_data = fetch_and_calculate_prices()
        save_history_entry(current_data)
        return jsonify({"success": True, "data": current_data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def parse_iso_datetime(date_string):
    try:
        if date_string.endswith('Z'):
            date_string = date_string[:-1]
        if '+' in date_string[10:]:
            date_string = date_string[:date_string.rfind('+')]
        elif date_string[10:].count('-') > 0:
            last_dash = date_string.rfind('-')
            if last_dash > 10:
                date_string = date_string[:last_dash]
        dt = datetime.fromisoformat(date_string)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception as e:
        print(f"Error parseando fecha: {e}")
        return datetime.now()

MAX_HISTORY_LIMIT = 15000

@app.route('/api/history')
def get_history():
    start = request.args.get('start')
    end = request.args.get('end')
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)

    limit = max(1, min(limit, MAX_HISTORY_LIMIT))
    offset = max(0, offset)
    start_dt = parse_iso_datetime(start) if start else None
    end_dt = parse_iso_datetime(end) if end else None

    history, total = load_history(start=start_dt, end=end_dt, limit=limit, offset=offset)

    return jsonify({"data": history, "total": total, "limit": limit, "offset": offset})

# ============== INICIALIZACION ==============

os.makedirs('static', exist_ok=True)

def init_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=update_prices_job, trigger="interval", seconds=60)
    scheduler.start()
    print("Scheduler de precios iniciado: actualizacion cada 60 segundos")
    update_prices_job()
    atexit.register(lambda: scheduler.shutdown())
    return scheduler

def init_app():
    """Inicializa base de datos, scheduler y bot de Telegram"""
    init_database()
    scheduler = init_scheduler()
    run_telegram_bot()
    return scheduler

# Detectar entorno
is_gunicorn = "gunicorn" in os.environ.get("SERVER_SOFTWARE", "")

if is_gunicorn:
    scheduler = init_app()

if __name__ == '__main__':
    scheduler = init_app()
    print("Servidor iniciando en http://localhost:5000")
    app.run(debug=True, port=5000, use_reloader=False)
