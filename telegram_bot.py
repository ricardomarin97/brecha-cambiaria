import requests
from bs4 import BeautifulSoup
import warnings
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from datetime import datetime

warnings.filterwarnings('ignore')

BOT_TOKEN = '8597304439:AAGC3wDqcjvsOO6tLU9eyxnK0G64rcxXWE0'

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
            "publisherType": None,
            "payTypes": []
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            data = response.json()
            for ad in data.get("data", []):
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

def get_brecha_info():
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
        "timestamp": datetime.now()
    }

def format_message(data):
    timestamp = data["timestamp"].strftime("%d/%m/%Y %H:%M:%S")
    return f"""
📊 *BRECHA CAMBIARIA VENEZUELA*
━━━━━━━━━━━━━━━━━━━━━

💵 *Dolar BCV:* `{data['bcv_usd']:,.2f} VES`
💶 *Euro BCV:* `{data['bcv_eur']:,.2f} VES`
💰 *USDT Binance:* `{data['usdt_avg']:,.2f} VES`

📉 *Brechas Cambiarias:*
   • USDT vs $ BCV: `{data['brecha_usdt_usd']:.2f}%`
   • USDT vs € BCV: `{data['brecha_usdt_eur']:.2f}%`
   • € BCV vs $ BCV: `{data['brecha_eur_usd']:.2f}%`

🕐 _{timestamp}_
"""

def get_keyboard():
    keyboard = [[InlineKeyboardButton("📊 Consultar Brecha", callback_data="brecha")]]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📈 *Bot Brecha Cambiaria Venezuela*\n\nPresiona el boton para consultar:",
        parse_mode='Markdown',
        reply_markup=get_keyboard()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "brecha":
        await query.edit_message_text("⏳ Consultando datos...")

        try:
            data = get_brecha_info()

            if data["bcv_usd"] is None or data["usdt_avg"] is None:
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

def main():
    print("Bot iniciado...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
