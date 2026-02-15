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

# Archivos de datos
HISTORY_FILE = 'price_history.json'
SUBSCRIBERS_FILE = 'telegram_subscribers.json'
LAST_BRECHA_FILE = 'last_brecha.json'

# Configuracion Telegram
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
BRECHA_CHANGE_THRESHOLD = 5.0  # Umbral de cambio para alerta

# Variable global para el bot
telegram_bot = None

# ============== FUNCIONES DE DATOS ==============

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)

def load_subscribers():
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_subscribers(subscribers):
    with open(SUBSCRIBERS_FILE, 'w') as f:
        json.dump(subscribers, f)

def load_last_brecha():
    if os.path.exists(LAST_BRECHA_FILE):
        try:
            with open(LAST_BRECHA_FILE, 'r') as f:
                return json.load(f)
        except:
            return None
    return None

def save_last_brecha(brecha_data):
    with open(LAST_BRECHA_FILE, 'w') as f:
        json.dump(brecha_data, f)

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
                subscribers.append(chat_id)
                save_subscribers(subscribers)
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
                subscribers.remove(chat_id)
                save_subscribers(subscribers)
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

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_callback))

        # Ignorar cualquier otro mensaje de texto
        from telegram.ext import MessageHandler, filters
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ignore_messages))

        # Programar notificaciones (hora UTC)
        # 8:00 AM Venezuela = 12:00 UTC
        # 2:00 PM Venezuela = 18:00 UTC
        # 10:00 PM Venezuela = 02:00 UTC
        job_queue = application.job_queue

        from datetime import time as dt_time
        job_queue.run_daily(scheduled_job_wrapper, time=dt_time(hour=12, minute=0), name='morning')
        job_queue.run_daily(scheduled_job_wrapper, time=dt_time(hour=18, minute=0), name='afternoon')
        job_queue.run_daily(scheduled_job_wrapper, time=dt_time(hour=2, minute=0), name='night')

        # Verificar cambio de brecha cada hora
        job_queue.run_repeating(brecha_check_wrapper, interval=3600, first=60, name='brecha_check')

        print("Bot de Telegram iniciado")
        print("  - Notificaciones: 8:00 AM, 2:00 PM, 10:00 PM (Venezuela)")
        print("  - Verificacion de brecha: cada hora")

        application.run_polling(allowed_updates=Update.ALL_TYPES)

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    print("Bot de Telegram ejecutandose en thread separado")

# ============== JOBS DEL SCHEDULER ==============

def update_prices_job():
    print(f"[{datetime.now().isoformat()}] Actualizando precios...")
    try:
        current_data = fetch_and_calculate_prices()
        history = load_history()
        history.append(current_data)
        save_history(history)
        print(f"[{datetime.now().isoformat()}] Precios actualizados")
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error: {e}")

# ============== RUTAS API ==============

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

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
        history = load_history()
        history.append(current_data)
        save_history(history)
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
    """Inicializa scheduler y bot de Telegram"""
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
