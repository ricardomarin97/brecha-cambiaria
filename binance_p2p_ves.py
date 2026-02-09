import requests
import json

def get_p2p_ads(fiat="VES", asset="USDT", trade_type="BUY", rows=10):
    """
    Obtiene anuncios P2P de Binance

    Args:
        fiat: Moneda fiat (VES para Bolivar Venezolano)
        asset: Criptomoneda (USDT, BTC, etc.)
        trade_type: BUY o SELL
        rows: Cantidad de resultados
    """
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"

    payload = {
        "fiat": fiat,
        "page": 1,
        "rows": rows,
        "tradeType": trade_type,
        "asset": asset,
        "countries": [],
        "proMerchantAds": False,
        "publisherType": "merchant",
        "payTypes": []
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error en la solicitud: {e}")
        return None

def display_ads(data, trade_type):
    """Muestra los anuncios de forma legible"""
    if not data or "data" not in data:
        print("No se encontraron datos")
        return

    ads = data.get("data", [])

    if not ads:
        print("No hay anuncios disponibles")
        return

    print(f"\n{'='*70}")
    print(f" ANUNCIOS P2P - {trade_type} USDT con Bolivares (VES)")
    print(f"{'='*70}\n")

    for i, ad in enumerate(ads, 1):
        adv = ad.get("adv", {})
        advertiser = ad.get("advertiser", {})

        price = adv.get("price", "N/A")
        min_amount = adv.get("minSingleTransAmount", "N/A")
        max_amount = adv.get("maxSingleTransAmount", "N/A")
        available = adv.get("surplusAmount", "N/A")

        nickname = advertiser.get("nickName", "N/A")
        month_orders = advertiser.get("monthOrderCount", 0)
        month_rate = advertiser.get("monthFinishRate", 0)

        # Metodos de pago
        pay_methods = [tm.get("identifier", "") for tm in adv.get("tradeMethods", [])]

        print(f"#{i} {nickname}")
        print(f"   Precio: {price} VES por USDT")
        print(f"   Disponible: {available} USDT")
        print(f"   Limite: {min_amount} - {max_amount} VES")
        print(f"   Ordenes/mes: {month_orders} | Tasa completado: {float(month_rate)*100:.1f}%")
        print(f"   Metodos de pago: {', '.join(pay_methods)}")
        print(f"{'-'*70}")

def main():
    print("\nConsultando anuncios P2P de Binance...")
    print("Par: USDT / VES (Bolivar Venezolano)")

    # Obtener anuncios de COMPRA (usuarios quieren comprar USDT)
    print("\n[1] Obteniendo anuncios de COMPRA...")
    buy_data = get_p2p_ads(fiat="VES", asset="USDT", trade_type="BUY", rows=5)
    display_ads(buy_data, "COMPRA")

    # Obtener anuncios de VENTA (usuarios quieren vender USDT)
    print("\n[2] Obteniendo anuncios de VENTA...")
    sell_data = get_p2p_ads(fiat="VES", asset="USDT", trade_type="SELL", rows=5)
    display_ads(sell_data, "VENTA")

    # Mostrar spread si hay datos (se usa índice 1 para omitir anuncios promocionados)
    if buy_data and sell_data:
        try:
            buy_price = float(buy_data["data"][1]["adv"]["price"])
            sell_price = float(sell_data["data"][1]["adv"]["price"])
            spread = ((buy_price - sell_price) / sell_price) * 100

            print(f"\n{'='*70}")
            print(f" RESUMEN DE PRECIOS")
            print(f"{'='*70}")
            print(f" Precio COMPRA (2do):  {buy_price:,.2f} VES")
            print(f" Precio VENTA (2do):   {sell_price:,.2f} VES")
            print(f" Spread:               {spread:.2f}%")
            print(f"{'='*70}\n")
        except (KeyError, IndexError, ValueError) as e:
            print(f"No se pudo calcular el spread: {e}")

if __name__ == "__main__":
    main()
