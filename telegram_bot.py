import requests
from bs4 import BeautifulSoup
import warnings
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

warnings.filterwarnings('ignore')

# Cargar variables de entorno desde .env
load_dotenv()

# Configuracion
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

if not BOT_TOKEN:
    raise ValueError("Error: TELEGRAM_BOT_TOKEN no esta configurado. Crea un archivo .env con tu token.")
SUBSCRIBERS_FILE = 'telegram_subscribers.json'
HISTORY_FILE = 'price_history.json'
LAST_BRECHA_FILE = 'last_brecha.json'

# Umbral de cambio para notificacion (5%)
BRECHA_CHANGE_THRESHOLD = 5.0

def load_subscribers():
    """Carga la lista de chat_ids suscritos"""
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_subscribers(subscribers):
    """Guarda la lista de suscriptores"""
    with open(SUBSCRIBERS_FILE, 'w') as f:
        json.dump(subscribers, f)

def load_last_brecha():
    """Carga la ultima brecha registrada para comparacion"""
    if os.path.exists(LAST_BRECHA_FILE):
        try:
            with open(LAST_BRECHA_FILE, 'r') as f:
                return json.load(f)
        except:
            return None
    return None

def save_last_brecha(brecha_data):
    """Guarda la brecha actual para comparacion futura"""
    with open(LAST_BRECHA_FILE, 'w') as f:
        json.dump(brecha_data, f)

def load_history():
    """Carga el historial de precios"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

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
    except:
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
        except:
            pass
    return results

def calculate_weighted_average(ads):
    if not ads:
        return None
    total_weight = sum(ad["available"] for ad in ads)
    if total_weight == 0:
        return None
    return sum(ad["price"] * ad["available"] for ad in ads) / total_weight

def get_latest_data():
    """Obtiene los datos mas recientes del historial o consulta en vivo"""
    history = load_history()

    if history:
        return history[-1]

    # Si no hay historial, consultar en vivo
    bcv_prices = get_bcv_prices()
    binance_data = get_binance_p2p_prices()

    buy_avg = calculate_weighted_average(binance_data["buy"])
    sell_avg = calculate_weighted_average(binance_data["sell"])
    usdt_avg = (buy_avg + sell_avg) / 2 if buy_avg and sell_avg else None

    brecha_usdt_usd = None
    brecha_usdt_eur = None
    brecha_eur_usd = None

    if bcv_prices['usd'] and usdt_avg:
        brecha_usdt_usd = ((usdt_avg - bcv_prices['usd']) / bcv_prices['usd']) * 100

    if bcv_prices['eur'] and usdt_avg:
        brecha_usdt_eur = ((usdt_avg - bcv_prices['eur']) / bcv_prices['eur']) * 100

    if bcv_prices['usd'] and bcv_prices['eur']:
        brecha_eur_usd = ((bcv_prices['eur'] - bcv_prices['usd']) / bcv_prices['usd']) * 100

    return {
        "bcv_usd": bcv_prices['usd'],
        "bcv_eur": bcv_prices['eur'],
        "usdt_avg": usdt_avg,
        "brecha_usdt_usd": brecha_usdt_usd,
        "brecha_usdt_eur": brecha_usdt_eur,
        "brecha_eur_usd": brecha_eur_usd,
        "timestamp": datetime.utcnow().isoformat() + 'Z'
    }

def format_message(data, is_alert=False):
    """Formatea el mensaje para enviar"""
    try:
        timestamp_str = data.get("timestamp", "")
        if timestamp_str:
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1]
            dt = datetime.fromisoformat(timestamp_str)
            # Convertir de UTC a Venezuela (UTC-4)
            from datetime import timedelta
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
    """Formatea mensaje de alerta con informacion del cambio"""
    base_msg = format_message(data, is_alert=True)

    direction = "subio" if change > 0 else "bajo"

    alert_info = f"""
⚠️ *La brecha USDT vs $ BCV {direction}*
   • Anterior: `{old_brecha:.2f}%`
   • Actual: `{new_brecha:.2f}%`
   • Cambio: `{change:+.2f}%`
"""
    return base_msg + alert_info

def get_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Consultar Brecha", callback_data="brecha")],
        [
            InlineKeyboardButton("🔔 Suscribirse", callback_data="subscribe"),
            InlineKeyboardButton("🔕 Desuscribirse", callback_data="unsubscribe")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📈 *Bot Brecha Cambiaria Venezuela*\n\n"
        "Recibe notificaciones automaticas:\n"
        "• 8:00 AM, 2:00 PM y 10:00 PM\n"
        "• Alertas cuando la brecha cambie mas del 5%\n\n"
        "Presiona los botones para interactuar:",
        parse_mode='Markdown',
        reply_markup=get_keyboard()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if query.data == "brecha":
        await query.edit_message_text("⏳ Consultando datos...")

        try:
            data = get_latest_data()

            if data.get("bcv_usd") is None or data.get("usdt_avg") is None:
                await query.edit_message_text(
                    "❌ Error obteniendo datos. Intenta de nuevo.",
                    reply_markup=get_keyboard()
                )
                return

            message = format_message(data)
            await query.edit_message_text(
                message,
                parse_mode='Markdown',
                reply_markup=get_keyboard()
            )
        except Exception as e:
            await query.edit_message_text(
                f"❌ Error: {str(e)}",
                reply_markup=get_keyboard()
            )

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
                reply_markup=get_keyboard()
            )
        else:
            await query.edit_message_text(
                "ℹ️ Ya estas suscrito a las notificaciones.",
                reply_markup=get_keyboard()
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
                reply_markup=get_keyboard()
            )
        else:
            await query.edit_message_text(
                "ℹ️ No estabas suscrito.",
                reply_markup=get_keyboard()
            )

async def send_scheduled_notification(context: ContextTypes.DEFAULT_TYPE):
    """Envia notificacion programada a todos los suscriptores"""
    subscribers = load_subscribers()
    if not subscribers:
        print(f"[{datetime.now()}] No hay suscriptores para notificar")
        return

    try:
        data = get_latest_data()
        if data.get("bcv_usd") is None:
            print(f"[{datetime.now()}] No hay datos disponibles para enviar")
            return

        message = format_message(data)

        for chat_id in subscribers:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode='Markdown'
                )
                print(f"[{datetime.now()}] Notificacion enviada a {chat_id}")
            except Exception as e:
                print(f"[{datetime.now()}] Error enviando a {chat_id}: {e}")

        # Actualizar ultima brecha registrada
        if data.get("brecha_usdt_usd") is not None:
            save_last_brecha({
                "brecha_usdt_usd": data["brecha_usdt_usd"],
                "timestamp": data.get("timestamp", datetime.utcnow().isoformat() + 'Z')
            })

    except Exception as e:
        print(f"[{datetime.now()}] Error en notificacion programada: {e}")

async def check_brecha_change(context: ContextTypes.DEFAULT_TYPE):
    """Verifica si la brecha cambio mas del umbral y notifica"""
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
            # Primera vez, guardar y salir
            save_last_brecha({
                "brecha_usdt_usd": current_brecha,
                "timestamp": data.get("timestamp", datetime.utcnow().isoformat() + 'Z')
            })
            return

        old_brecha = last_brecha_data.get("brecha_usdt_usd", 0)

        # Calcular cambio absoluto
        change = current_brecha - old_brecha

        if abs(change) >= BRECHA_CHANGE_THRESHOLD:
            print(f"[{datetime.now()}] Cambio de brecha detectado: {old_brecha:.2f}% -> {current_brecha:.2f}% (cambio: {change:+.2f}%)")

            message = format_alert_message(data, old_brecha, current_brecha, change)

            for chat_id in subscribers:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                    print(f"[{datetime.now()}] Alerta enviada a {chat_id}")
                except Exception as e:
                    print(f"[{datetime.now()}] Error enviando alerta a {chat_id}: {e}")

            # Actualizar ultima brecha
            save_last_brecha({
                "brecha_usdt_usd": current_brecha,
                "timestamp": data.get("timestamp", datetime.utcnow().isoformat() + 'Z')
            })

    except Exception as e:
        print(f"[{datetime.now()}] Error verificando cambio de brecha: {e}")

def main():
    print("Bot iniciado...")
    print(f"Umbral de alerta: {BRECHA_CHANGE_THRESHOLD}%")

    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))

    # Scheduler para notificaciones programadas
    # Hora Venezuela = UTC-4, asi que:
    # 8:00 AM Venezuela = 12:00 UTC
    # 2:00 PM Venezuela = 18:00 UTC
    # 10:00 PM Venezuela = 02:00 UTC (del dia siguiente)

    scheduler = AsyncIOScheduler(timezone='UTC')

    # Notificaciones programadas (3 veces al dia)
    scheduler.add_job(
        send_scheduled_notification,
        CronTrigger(hour=12, minute=0),  # 8:00 AM Venezuela
        args=[app],
        id='morning_notification'
    )
    scheduler.add_job(
        send_scheduled_notification,
        CronTrigger(hour=18, minute=0),  # 2:00 PM Venezuela
        args=[app],
        id='afternoon_notification'
    )
    scheduler.add_job(
        send_scheduled_notification,
        CronTrigger(hour=2, minute=0),  # 10:00 PM Venezuela
        args=[app],
        id='night_notification'
    )

    # Verificar cambio de brecha cada hora
    scheduler.add_job(
        check_brecha_change,
        CronTrigger(minute=0),  # Cada hora en punto
        args=[app],
        id='brecha_check'
    )

    scheduler.start()
    print("Scheduler iniciado:")
    print("  - Notificaciones: 8:00 AM, 2:00 PM, 10:00 PM (Venezuela)")
    print("  - Verificacion de brecha: cada hora")

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
