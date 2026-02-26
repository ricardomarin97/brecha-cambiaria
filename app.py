from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from bs4 import BeautifulSoup
import warnings
from datetime import datetime, timedelta
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
BRECHA_CHANGE_THRESHOLD = 5.0

# Archivos JSON (fallback si no hay PostgreSQL)
HISTORY_FILE = 'price_history.json'
SUBSCRIBERS_FILE = 'telegram_subscribers.json'
LAST_BRECHA_FILE = 'last_brecha.json'

# ============== CONEXION POSTGRESQL ==============

def get_db_connection():
    """Obtiene conexion a PostgreSQL"""
    if not DATABASE_URL:
        return None
    try:
        import psycopg2
        # Render usa postgres:// pero psycopg2 necesita postgresql://
        db_url = DATABASE_URL.replace('postgres://', 'postgresql://')
        conn = psycopg2.connect(db_url)
        return conn
    except Exception as e:
        print(f"Error conectando a PostgreSQL: {e}")
        return None

def init_database():
    """Crea las tablas si no existen"""
    conn = get_db_connection()
    if not conn:
        print("PostgreSQL no disponible, usando archivos JSON")
        return False

    try:
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

        # Tabla de suscriptores de Telegram
        cur.execute('''
            CREATE TABLE IF NOT EXISTS telegram_subscribers (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT UNIQUE NOT NULL,
                subscribed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
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
        conn.close()
        print("PostgreSQL inicializado correctamente")
        return True
    except Exception as e:
        print(f"Error inicializando PostgreSQL: {e}")
        return False

# ============== FUNCIONES DE DATOS ==============

def load_history():
    """Carga historial de precios"""
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('''
                SELECT timestamp, bcv_usd, bcv_eur, usdt_avg,
                       brecha_usdt_usd, brecha_usdt_eur, brecha_eur_usd
                FROM price_history
                ORDER BY timestamp ASC
            ''')
            rows = cur.fetchall()
            cur.close()
            conn.close()

            history = []
            for row in rows:
                # Convertir timestamp a formato ISO sin timezone info + Z
                ts = row[0]
                if ts:
                    if ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)
                    timestamp_str = ts.isoformat() + 'Z'
                else:
                    timestamp_str = None
                history.append({
                    "timestamp": timestamp_str,
                    "bcv_usd": float(row[1]) if row[1] else None,
                    "bcv_eur": float(row[2]) if row[2] else None,
                    "usdt_avg": float(row[3]) if row[3] else None,
                    "brecha_usdt_usd": float(row[4]) if row[4] else None,
                    "brecha_usdt_eur": float(row[5]) if row[5] else None,
                    "brecha_eur_usd": float(row[6]) if row[6] else None
                })
            return history
        except Exception as e:
            print(f"Error cargando historial de PostgreSQL: {e}")
            return []

    # Fallback a JSON
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_history_entry(data):
    """Guarda un registro en el historial"""
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            timestamp = data.get('timestamp', '').replace('Z', '')
            cur.execute('''
                INSERT INTO price_history
                (timestamp, bcv_usd, bcv_eur, usdt_avg, brecha_usdt_usd, brecha_usdt_eur, brecha_eur_usd)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                timestamp,
                data.get('bcv_usd'),
                data.get('bcv_eur'),
                data.get('usdt_avg'),
                data.get('brecha_usdt_usd'),
                data.get('brecha_usdt_eur'),
                data.get('brecha_eur_usd')
            ))
            conn.commit()
            cur.close()
            conn.close()
            return True
        except Exception as e:
            print(f"Error guardando en PostgreSQL: {e}")
            return False

    # Fallback a JSON
    history = load_history()
    history.append(data)
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)
    return True

def load_subscribers():
    """Carga lista de suscriptores de Telegram"""
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('SELECT chat_id FROM telegram_subscribers')
            rows = cur.fetchall()
            cur.close()
            conn.close()
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
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO telegram_subscribers (chat_id)
                VALUES (%s)
                ON CONFLICT (chat_id) DO NOTHING
            ''', (chat_id,))
            conn.commit()
            cur.close()
            conn.close()
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
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('DELETE FROM telegram_subscribers WHERE chat_id = %s', (chat_id,))
            conn.commit()
            cur.close()
            conn.close()
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

def load_last_brecha():
    """Carga la ultima brecha guardada"""
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM app_settings WHERE key = 'last_brecha'")
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                return json.loads(row[0])
            return None
        except Exception as e:
            print(f"Error cargando ultima brecha: {e}")
            return None

    # Fallback a JSON
    if os.path.exists(LAST_BRECHA_FILE):
        try:
            with open(LAST_BRECHA_FILE, 'r') as f:
                return json.load(f)
        except:
            return None
    return None

def load_last_bcv():
    """Carga los ultimos valores del BCV guardados"""
    LAST_BCV_FILE = 'last_bcv.json'
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM app_settings WHERE key = 'last_bcv'")
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                return json.loads(row[0])
            return None
        except Exception as e:
            print(f"Error cargando ultimo BCV: {e}")
            return None

    # Fallback a JSON
    if os.path.exists(LAST_BCV_FILE):
        try:
            with open(LAST_BCV_FILE, 'r') as f:
                return json.load(f)
        except:
            return None
    return None

def save_last_bcv(bcv_data):
    """Guarda los ultimos valores del BCV"""
    LAST_BCV_FILE = 'last_bcv.json'
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO app_settings (key, value, updated_at)
                VALUES ('last_bcv', %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = CURRENT_TIMESTAMP
            ''', (json.dumps(bcv_data), json.dumps(bcv_data)))
            conn.commit()
            cur.close()
            conn.close()
            return True
        except Exception as e:
            print(f"Error guardando ultimo BCV: {e}")
            return False

    # Fallback a JSON
    with open(LAST_BCV_FILE, 'w') as f:
        json.dump(bcv_data, f)
    return True

def save_last_brecha(brecha_data):
    """Guarda la ultima brecha"""
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO app_settings (key, value, updated_at)
                VALUES ('last_brecha', %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = CURRENT_TIMESTAMP
            ''', (json.dumps(brecha_data), json.dumps(brecha_data)))
            conn.commit()
            cur.close()
            conn.close()
            return True
        except Exception as e:
            print(f"Error guardando ultima brecha: {e}")
            return False

    # Fallback a JSON
    with open(LAST_BRECHA_FILE, 'w') as f:
        json.dump(brecha_data, f)
    return True

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

    timestamp = datetime.utcnow().isoformat() + 'Z'

    return {
        "timestamp": timestamp,
        "bcv_usd": bcv_prices['usd'],
        "bcv_eur": bcv_prices['eur'],
        "usdt_avg": round(usdt_avg, 2) if usdt_avg else None,
        "brecha_usdt_usd": round(brecha_usdt_usd, 2) if brecha_usdt_usd else None,
        "brecha_usdt_eur": round(brecha_usdt_eur, 2) if brecha_usdt_eur else None,
        "brecha_eur_usd": round(brecha_eur_usd, 2) if brecha_eur_usd else None
    }

def get_latest_data():
    history = load_history()
    if history:
        return history[-1]
    return fetch_and_calculate_prices()

# ============== FUNCIONES DE TELEGRAM ==============

def format_telegram_message(data, is_alert=False):
    try:
        timestamp_str = data.get("timestamp", "")
        if timestamp_str:
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1]
            dt = datetime.fromisoformat(timestamp_str)
            dt_venezuela = dt - timedelta(hours=4)
            timestamp = dt_venezuela.strftime("%d/%m/%Y %H:%M:%S")
        else:
            timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    except:
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

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
    try:
        timestamp_str = data.get("timestamp", "")
        if timestamp_str:
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1]
            dt = datetime.fromisoformat(timestamp_str)
            dt_venezuela = dt - timedelta(hours=4)
            timestamp = dt_venezuela.strftime("%d/%m/%Y %H:%M:%S")
        else:
            timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    except:
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

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
    if not subscribers:
        return

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

            save_last_brecha({
                "brecha_usdt_usd": current_brecha,
                "timestamp": data.get("timestamp")
            })

    except Exception as e:
        print(f"[{datetime.now()}] Error verificando brecha: {e}")

async def check_bcv_update(bot):
    """Verifica si el BCV actualizo sus tasas y notifica"""
    subscribers = load_subscribers()
    if not subscribers:
        return

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
    history = load_history()

    oldest = None
    newest = None
    if history:
        oldest = history[0].get('timestamp')
        newest = history[-1].get('timestamp')

    return jsonify({
        "subscribers": len(subscribers),
        "total_records": len(history),
        "oldest_record": oldest,
        "newest_record": newest,
        "database": "PostgreSQL" if get_db_connection() else "JSON"
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

@app.route('/api/prices')
def get_prices():
    history = load_history()
    if history:
        return jsonify(history[-1])
    return jsonify({
        "timestamp": None, "bcv_usd": None, "bcv_eur": None,
        "usdt_avg": None, "brecha_usdt_usd": None,
        "brecha_usdt_eur": None, "brecha_eur_usd": None
    })

@app.route('/api/latest')
def get_latest():
    history = load_history()
    if history:
        return jsonify(history[-1])
    return jsonify({
        "timestamp": None, "bcv_usd": None, "bcv_eur": None,
        "usdt_avg": None, "brecha_usdt_usd": None,
        "brecha_usdt_eur": None, "brecha_eur_usd": None
    })

@app.route('/api/refresh', methods=['POST'])
def refresh_prices():
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

@app.route('/api/history')
def get_history():
    history = load_history()
    start = request.args.get('start')
    end = request.args.get('end')
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)

    if start or end:
        filtered = []
        for entry in history:
            if not entry.get('timestamp'):
                continue
            entry_time = parse_iso_datetime(entry['timestamp'])
            if start and entry_time < parse_iso_datetime(start):
                continue
            if end and entry_time > parse_iso_datetime(end):
                continue
            filtered.append(entry)
        history = filtered

    total = len(history)

    # Si hay más registros que el límite, tomar los más recientes
    if len(history) > limit:
        history = history[-limit:]
    elif offset > 0:
        history = history[offset:offset + limit]

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
