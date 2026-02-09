from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from bs4 import BeautifulSoup
import warnings
from datetime import datetime
import json
import os
import atexit

warnings.filterwarnings('ignore')

app = Flask(__name__, static_folder='static')
CORS(app)

HISTORY_FILE = 'price_history.json'

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(history):
    # Historial permanente - sin limite de registros
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)

def get_bcv_prices():
    """Obtiene el precio del dolar y euro oficial del BCV"""
    try:
        response = requests.get('https://www.bcv.org.ve/', verify=False, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')

        prices = {'usd': None, 'eur': None}

        # Dolar
        dolar_section = soup.find('div', {'id': 'dolar'})
        if dolar_section:
            strong = dolar_section.find('strong')
            if strong:
                valor = strong.get_text(strip=True)
                valor = valor.replace('.', '').replace(',', '.')
                prices['usd'] = float(valor)

        # Euro
        euro_section = soup.find('div', {'id': 'euro'})
        if euro_section:
            strong = euro_section.find('strong')
            if strong:
                valor = strong.get_text(strip=True)
                valor = valor.replace('.', '').replace(',', '.')
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
            # Omitir primer anuncio (índice 0) ya que suele ser promocionado
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
    weighted_sum = sum(ad["price"] * ad["available"] for ad in ads)
    return weighted_sum / total_weight

def fetch_and_calculate_prices():
    """Obtiene precios y calcula brechas. Retorna dict con todos los datos."""
    bcv_prices = get_bcv_prices()
    binance_data = get_binance_p2p_prices()

    buy_avg = calculate_weighted_average(binance_data["buy"])
    sell_avg = calculate_weighted_average(binance_data["sell"])
    usdt_avg = (buy_avg + sell_avg) / 2 if buy_avg and sell_avg else None

    # Brecha USDT vs Dolar BCV
    brecha_usdt_usd = None
    if bcv_prices['usd'] and usdt_avg:
        brecha_usdt_usd = ((usdt_avg - bcv_prices['usd']) / bcv_prices['usd']) * 100

    # Brecha USDT vs Euro BCV
    brecha_usdt_eur = None
    if bcv_prices['eur'] and usdt_avg:
        brecha_usdt_eur = ((usdt_avg - bcv_prices['eur']) / bcv_prices['eur']) * 100

    # Brecha Euro BCV vs Dolar BCV
    brecha_eur_usd = None
    if bcv_prices['usd'] and bcv_prices['eur']:
        brecha_eur_usd = ((bcv_prices['eur'] - bcv_prices['usd']) / bcv_prices['usd']) * 100

    timestamp = datetime.now().isoformat()

    return {
        "timestamp": timestamp,
        "bcv_usd": bcv_prices['usd'],
        "bcv_eur": bcv_prices['eur'],
        "usdt_avg": round(usdt_avg, 2) if usdt_avg else None,
        "brecha_usdt_usd": round(brecha_usdt_usd, 2) if brecha_usdt_usd else None,
        "brecha_usdt_eur": round(brecha_usdt_eur, 2) if brecha_usdt_eur else None,
        "brecha_eur_usd": round(brecha_eur_usd, 2) if brecha_eur_usd else None
    }

def update_prices_job():
    """Job del scheduler: obtiene precios y guarda en historial."""
    print(f"[{datetime.now().isoformat()}] Ejecutando actualizacion automatica de precios...")
    try:
        current_data = fetch_and_calculate_prices()
        history = load_history()
        history.append(current_data)
        save_history(history)
        print(f"[{datetime.now().isoformat()}] Precios actualizados correctamente")
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error en actualizacion automatica: {e}")

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/prices')
def get_prices():
    """Devuelve el ultimo registro del historial (solo lectura, no escribe)."""
    history = load_history()
    if history:
        return jsonify(history[-1])
    # Si no hay historial, devolver estructura vacia
    return jsonify({
        "timestamp": None,
        "bcv_usd": None,
        "bcv_eur": None,
        "usdt_avg": None,
        "brecha_usdt_usd": None,
        "brecha_usdt_eur": None,
        "brecha_eur_usd": None
    })

@app.route('/api/latest')
def get_latest():
    """Devuelve el ultimo precio sin agregar al historial."""
    history = load_history()
    if history:
        return jsonify(history[-1])
    return jsonify({
        "timestamp": None,
        "bcv_usd": None,
        "bcv_eur": None,
        "usdt_avg": None,
        "brecha_usdt_usd": None,
        "brecha_usdt_eur": None,
        "brecha_eur_usd": None
    })

@app.route('/api/refresh', methods=['POST'])
def refresh_prices():
    """Fuerza una actualizacion inmediata de precios."""
    try:
        current_data = fetch_and_calculate_prices()
        history = load_history()
        history.append(current_data)
        save_history(history)
        return jsonify({"success": True, "data": current_data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/history')
def get_history():
    """
    Devuelve historial con filtros opcionales.
    Parametros:
    - start: fecha/hora inicio (ISO format)
    - end: fecha/hora fin (ISO format)
    - limit: maximo de registros (default: 100)
    - offset: para paginacion (default: 0)
    """
    history = load_history()

    start = request.args.get('start')
    end = request.args.get('end')
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)

    # Filtrar por rango de fechas si se especifica
    if start or end:
        filtered = []
        for entry in history:
            if not entry.get('timestamp'):
                continue
            entry_time = datetime.fromisoformat(entry['timestamp'])

            if start:
                start_time = datetime.fromisoformat(start)
                if entry_time < start_time:
                    continue

            if end:
                end_time = datetime.fromisoformat(end)
                if entry_time > end_time:
                    continue

            filtered.append(entry)
        history = filtered

    # Aplicar offset y limit
    total = len(history)
    history = history[offset:offset + limit]

    return jsonify({
        "data": history,
        "total": total,
        "limit": limit,
        "offset": offset
    })

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)

    # Iniciar scheduler para actualizacion automatica cada 60 segundos
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=update_prices_job, trigger="interval", seconds=60)
    scheduler.start()
    print("Scheduler iniciado: actualizacion automatica cada 60 segundos")

    # Ejecutar una actualizacion inicial al arrancar
    update_prices_job()

    # Asegurar que el scheduler se detenga al cerrar la app
    atexit.register(lambda: scheduler.shutdown())

    print("Servidor iniciando en http://localhost:5000")
    app.run(debug=True, port=5000, use_reloader=False)
