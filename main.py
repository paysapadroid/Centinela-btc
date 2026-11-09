import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime

# ==========================================
# CONFIGURACIÓN PERSONAL
# ==========================================
TELEGRAM_BOT_TOKEN = "PEGA_AQUI_TU_TOKEN_DE_BOTFATHER"
TELEGRAM_CHAT_ID = "PEGA_AQUI_TU_CHAT_ID_NUMERICO"

SYMBOL = "BTCUSDT"
BINANCE_SPOT_BASE = "https://api.binance.com"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# ==========================================
# FUNCIONES AUXILIARES & TELEGRAM
# ==========================================
def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

def get_klines(symbol: str, interval: str, limit: int = 250):
    url = f"{BINANCE_SPOT_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df

def calculate_rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# ==========================================
# FILTROS DE PROTOCOLO & LIQUIDEZ
# ==========================================
def get_daily_indicators():
    df_daily = get_klines(SYMBOL, "1d", limit=220)
    df_daily["ma200"] = df_daily["close"].rolling(window=200).mean()
    df_daily["rsi14"] = calculate_rsi(df_daily["close"], period=14)
    
    current_price = df_daily["close"].iloc[-1]
    ma200 = df_daily["ma200"].iloc[-1]
    rsi14 = df_daily["rsi14"].iloc[-1]
    vol_current = df_daily["volume"].iloc[-1]
    vol_prev = df_daily["volume"].iloc[-2]
    
    return {
        "price": current_price,
        "ma200": ma200,
        "above_ma200": current_price > ma200,
        "rsi": rsi14,
        "volume_increasing": vol_current > vol_prev
    }

def get_funding_rate():
    # Consulta la tasa de financiación de Binance Futures
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate"
    params = {"symbol": SYMBOL, "limit": 1}
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    if data:
        return float(data[0]["fundingRate"]) * 100 # en porcentaje
    return 0.0

def check_depth_walls(current_price: float, threshold_pct: float = 0.03):
    # Evalúa muros en el libro de órdenes dentro de un 3% del precio
    url = f"{BINANCE_SPOT_BASE}/api/v3/depth"
    params = {"symbol": SYMBOL, "limit": 500}
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    
    asks = [(float(p), float(qty)) for p, qty in data["asks"]]
    bids = [(float(p), float(qty)) for p, qty in data["bids"]]
    
    # Identificar si hay órdenes individuales anómalas (muros)
    avg_ask_qty = np.mean([qty for _, qty in asks])
    massive_sell_walls = [
        (p, qty) for p, qty in asks 
        if p <= current_price * (1 + threshold_pct) and qty > avg_ask_qty * 5
    ]
    
    return massive_sell_walls

# ==========================================
# BUCLE DE EJECUCIÓN CONTINUA
# ==========================================
def run_sentinel():
    print("Iniciando Centinela BTC SPOT...")
    send_telegram_alert("🛡️ *Centinela BTC Iniciado*: Monitoreando Binance SPOT, MA200, RSI y Liquidez.")
    
    last_trigger_date = None

    while True:
        try:
            now = datetime.now()
            daily = get_daily_indicators()
            funding = get_funding_rate()
            walls = check_depth_walls(daily["price"])
            
            # 1. Gatillo de las 10:00 AM (UY)
            if now.hour == 10 and now.minute == 0 and last_trigger_date != now.date():
                last_trigger_date = now.date()
                
                status_ma = "✅ Por encima" if daily["above_ma200"] else "🚨 Por debajo"
                vol_status = "Creciente" if daily["volume_increasing"] else "Plano / Decreciente"
                
                report = (
                    f"⏰ *Gatillo 10:00 AM - Validación Diaria*\n\n"
                    f"• *Precio actual*: ${daily['price']:,.2f}\n"
                    f"• *MA200 Diaria*: ${daily['ma200']:,.2f} ({status_ma})\n"
                    f"• *RSI 14*: {daily['rsi']:.1f}\n"
                    f"• *Volumen*: {vol_status}\n"
                    f"• *Funding Rate*: {funding:.4f}%\n"
                )
                
                # Evaluación de filtros
                if daily["rsi"] > 62:
                    report += "\n⚠️ *Red Flag*: RSI en zona de techo/sobrecalentamiento (>62)."
                elif daily["rsi"] < 50 and daily["above_ma200"]:
                    report += "\n🎯 *Oportunidad*: RSI frío (<50) con estructura alcista sobre MA200."
                    
                if not daily["volume_increasing"]:
                    report += "\n⚠️ *Alerta Escalón Roto*: Volumen no acompaña la vela."
                    
                if walls:
                    top_wall = walls[0]
                    report += f"\n🧱 *Muro de venta detectado*: {top_wall[1]:.2f} BTC en ${top_wall[0]:,.2f}"

                send_telegram_alert(report)

            # 2. Alertas críticas intradía (Revisión cada 5 minutos)
            if funding > 0.05:  # Funding rate excesivo (sobrecalentamiento de largos)
                send_telegram_alert(f"⚠️ *Alerta de Liquidez*: Funding Rate disparado al {funding:.4f}%. Riesgo de barrido de apalancados.")
                time.sleep(1800)  # Evitar spam de alertas

        except Exception as e:
            print(f"Error en el ciclo de monitoreo: {e}")
        
        time.sleep(30)  # Pausa de 30 segundos entre iteraciones

if __name__ == "__main__":
    run_sentinel()
