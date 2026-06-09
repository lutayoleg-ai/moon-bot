import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.contrib.middlewares.logging import LoggingMiddleware
import logging
import ssl
import certifi
import warnings
import os
import gc
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
import sqlite3
import signal

warnings.filterwarnings('ignore')

os.environ['MPLCONFIGDIR'] = '/tmp/matplotlib'

# === ТОКЕН ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = 414210743
CHANNEL_ID = os.environ.get("CHANNEL_ID")

if not BOT_TOKEN:
    raise ValueError("❌ Токен не найден")

# === ПАРАМЕТРЫ СТРАТЕГИИ ===
STRATEGY = {
    'MA_FAST': 10,
    'MA_SLOW': 30,
    'ADX_THRESHOLD': 20,
    'STOP_LOSS': 0.06,
    'TAKE_PROFIT': 0.12,
    'DAILY_LOSS_LIMIT': 0.03,
    'CAPITAL': 100000,
    'POSITION_SIZE': 0.25
}

COMMISSION = 0.003
MOEX_TIMEOUT = 15

# === НАСТРОЙКА ЛОГИРОВАНИЯ ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === 17 АКТИВОВ ===
TICKERS = {
    "SBER": {"name": "Сбер", "return_bull": 3.62, "return_bear": 4.52},
    "VTBR": {"name": "ВТБ", "return_bull": 5.31, "return_bear": 5.35},
    "GAZP": {"name": "Газпром", "return_bull": 4.40, "return_bear": 3.35},
    "LKOH": {"name": "Лукойл", "return_bull": 2.98, "return_bear": 3.43},
    "ROSN": {"name": "Роснефть", "return_bull": 4.18, "return_bear": 3.04},
    "TATN": {"name": "Татнефть", "return_bull": 3.26, "return_bear": 2.79},
    "NLMK": {"name": "НЛМК", "return_bull": 4.84, "return_bear": 3.91},
    "GMKN": {"name": "Норникель", "return_bull": 4.60, "return_bear": 3.55},
    "MTLR": {"name": "Мечел", "return_bull": 5.41, "return_bear": 4.55},
    "ALRS": {"name": "Алроса", "return_bull": 4.73, "return_bear": 3.91},
    "AFLT": {"name": "Аэрофлот", "return_bull": 4.33, "return_bear": 4.58},
    "YDEX": {"name": "Яндекс", "return_bull": 2.31, "return_bear": 3.52},
    "OZON": {"name": "OZON", "return_bull": 3.92, "return_bear": 4.65},
    "MGNT": {"name": "Магнит", "return_bull": 4.62, "return_bear": 3.51},
    "CBOM": {"name": "МКБ", "return_bull": 4.46, "return_bear": 3.65},
    "WUSH": {"name": "Whoosh", "return_bull": 4.86, "return_bear": 3.93},
    "ASTR": {"name": "Астра", "return_bull": 3.77, "return_bear": 3.12},
}
ALL_TICKERS = list(TICKERS.keys())

# === ЛУННЫЕ ДАННЫЕ ===
LUNAR_PHASES = {
    "full_moons": [
        ("2026-01-03", "13:04"), ("2026-02-02", "01:10"), ("2026-03-03", "14:39"),
        ("2026-04-02", "05:13"), ("2026-05-01", "20:24"), ("2026-05-31", "11:46"),
        ("2026-06-30", "02:58"), ("2026-07-29", "17:37"), ("2026-08-28", "07:19"),
        ("2026-09-26", "19:50"), ("2026-10-26", "07:13"), ("2026-11-24", "17:55"),
        ("2026-12-24", "04:29"),
    ],
    "new_moons": [
        ("2026-01-18", "22:53"), ("2026-02-17", "15:03"), ("2026-03-19", "04:26"),
        ("2026-04-17", "14:54"), ("2026-05-16", "23:03"), ("2026-06-15", "05:56"),
        ("2026-07-14", "12:45"), ("2026-08-12", "20:37"), ("2026-09-11", "06:27"),
        ("2026-10-10", "18:50"), ("2026-11-09", "10:02"), ("2026-12-09", "03:52"),
        ("2027-01-07", "23:25"),
    ]
}

def get_lunar_info():
    msk = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk)
    next_full = None
    next_new = None
    
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if dt > now:
            next_full = dt
            break
    
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if dt > now:
            next_new = dt
            break
    
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if (now - dt).days <= 1 and (now - dt).days >= 0:
            return "полнолуние", dt, next_full, next_new
        if (dt - now).days == 1:
            return "полнолуние_завтра", dt, next_full, next_new
    
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if (now - dt).days <= 1 and (now - dt).days >= 0:
            return "новолуние", dt, next_full, next_new
        if (dt - now).days == 1:
            return "новолуние_завтра", dt, next_full, next_new
    
    new_moons = [msk.localize(datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")) for d, t in LUNAR_PHASES["new_moons"]]
    last_new = max([d for d in new_moons if d <= now], default=None)
    if last_new:
        days = (now - last_new).days
        return ("растущая" if days < 14 else "убывающая"), last_new, next_full, next_new
    
    return "обычный день", None, next_full, next_new

def get_days_until_full_moon():
    msk = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk)
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if dt > now:
            return (dt - now).days
    return None

def get_days_until_new_moon():
    msk = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk)
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if dt > now:
            return (dt - now).days
    return None

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
positions = {}
positions_lock = asyncio.Lock()
last_signal_sent = {}
daily_pnl = 0.0
last_reset_date = None
lunar_notified_days = set()

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, ticker TEXT, type TEXT, entry REAL, exit REAL, pnl_percent REAL, commission_percent REAL, is_manual INTEGER, capital REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_summary (date TEXT PRIMARY KEY, summary TEXT)''')

def save_trade(ticker, trade_type, entry, exit_price, pnl_percent, commission_percent, is_manual=False):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("INSERT INTO trades (date, ticker, type, entry, exit, pnl_percent, commission_percent, is_manual, capital) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (datetime.now().isoformat(), ticker, trade_type, entry, exit_price, pnl_percent, commission_percent, 1 if is_manual else 0, STRATEGY['CAPITAL']))

def get_stats():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*), SUM(pnl_percent), AVG(pnl_percent), SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) FROM trades")
        row = c.fetchone()
        total_trades = row[0] or 0
        total_pnl = row[1] or 0
        avg_pnl = row[2] or 0
        winning_trades = row[3] or 0
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        return {'total_trades': total_trades, 'total_pnl': total_pnl, 'avg_pnl': avg_pnl, 'win_rate': win_rate, 'winning_trades': winning_trades, 'losing_trades': total_trades - winning_trades}

def get_last_summary_date():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("SELECT date FROM daily_summary ORDER BY date DESC LIMIT 1")
        row = c.fetchone()
    return row[0] if row else None

def save_daily_summary(date, summary):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO daily_summary (date, summary) VALUES (?, ?)", (date, summary))

def clean_old_trades(days=30):
    with sqlite3.connect('bot_data.db') as conn:
        conn.execute("DELETE FROM trades WHERE date < datetime('now', ?)", (f'-{days} days',))

# === MOEX ===
class DataFetcher:
    moex_last_success = None
    moex_error_count = 0
    
    async def _fetch_json(self, url, params=None):
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
            timeout=aiohttp.ClientTimeout(total=MOEX_TIMEOUT),
            headers={'User-Agent': 'Mozilla/5.0'}
        ) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"MOEX вернул {resp.status} для {url}")
                    raise Exception(f"HTTP {resp.status}")
                return await resp.json()
    
    async def get_price(self, ticker):
        try:
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
            data = await self._fetch_json(url)
            
            md = data.get('marketdata', {})
            if md:
                cols = md.get('columns', [])
                rows = md.get('data', [])
                if rows:
                    for i, col in enumerate(cols):
                        if col.lower() in ('last', 'currentprice'):
                            if i < len(rows[0]) and rows[0][i]:
                                try:
                                    p = float(rows[0][i])
                                    if 0 < p < 20000:
                                        DataFetcher.moex_error_count = 0
                                        DataFetcher.moex_last_success = datetime.now()
                                        return p
                                except (ValueError, TypeError):
                                    pass
            return None
        except asyncio.TimeoutError:
            logger.error(f"Таймаут get_price({ticker})")
            DataFetcher.moex_error_count += 1
            return None
        except Exception as e:
            logger.error(f"Ошибка get_price({ticker}): {type(e).__name__}: {e}")
            DataFetcher.moex_error_count += 1
            return None
    
    async def fetch_candles_daily(self, ticker, days=100):
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}/candles.json"
            params = {'from': start.strftime('%Y-%m-%d'), 'till': end.strftime('%Y-%m-%d'), 'interval': 24}
            
            data = await self._fetch_json(url, params)
            candles = data.get('candles', {})
            rows = candles.get('data', [])
            cols = candles.get('columns', [])
            
            if rows and len(rows) >= 3:
                idx_date = next((i for i, c in enumerate(cols) if c.lower() in ('begin', 'date')), None)
                idx_close = next((i for i, c in enumerate(cols) if c.lower() in ('close', 'value')), None)
                if idx_date is not None and idx_close is not None:
                    records = []
                    for row in rows:
                        if len(row) > max(idx_date, idx_close):
                            try:
                                records.append({
                                    'date': pd.to_datetime(row[idx_date]),
                                    'close': float(row[idx_close])
                                })
                            except:
                                pass
                    if len(records) >= 5:
                        df = pd.DataFrame(records).sort_values('date').reset_index(drop=True)
                        return df
            return None
        except asyncio.TimeoutError:
            logger.error(f"Таймаут fetch_candles_daily({ticker})")
            return None
        except Exception as e:
            logger.error(f"Ошибка fetch_candles_daily({ticker}): {type(e).__name__}: {e}")
            return None
    
    async def healthcheck_moex(self):
        while True:
            try:
                price = await self.get_price("SBER")
                if price is not None and DataFetcher.moex_error_count >= 10:
                    logger.info("✅ MOEX снова доступен")
                    DataFetcher.moex_error_count = 0
                elif DataFetcher.moex_error_count >= 10:
                    logger.critical("MOEX недоступен более 30 минут!")
            except Exception as e:
                logger.error(f"Healthcheck MOEX ошибка: {e}")
            await asyncio.sleep(180)

data_fetcher = DataFetcher()

# === ИНДИКАТОРЫ ===
def calculate_adx(df, period=14):
    if len(df) < period + 5:
        return 20
    high = df['high'] if 'high' in df.columns else df['close']
    low = df['low'] if 'low' in df.columns else df['close']
    close = df['close']
    
    plus_dm = high.diff()
    minus_dm = low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm > 0] = 0
    
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (abs(minus_dm).rolling(period).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.rolling(period).mean().iloc[-1]
    return adx if not np.isnan(adx) else 20

def get_trend(df):
    if df is None or len(df) < 30:
        return None
    ma10 = df['close'].rolling(10).mean().iloc[-1]
    ma30 = df['close'].rolling(30).mean().iloc[-1]
    if ma10 > ma30:
        return "bullish"
    elif ma10 < ma30:
        return "bearish"
    return "neutral"

def calc_trend_for_ticker(df):
    if df is None or len(df) < 30:
        return "недостаточно данных"
    ma18 = df['close'].rolling(18).mean().iloc[-1]
    ma50 = df['close'].rolling(50).mean().iloc[-1] if len(df) >= 50 else ma18
    if np.isnan(ma18) or np.isnan(ma50):
        return "недостаточно данных"
    spread = abs(ma18 - ma50) / ma50 * 100
    return "боковик" if spread < 0.7 else ("бычий" if ma18 > ma50 else "медвежий")

# === РАСЧЁТ P&L ===
def calculate_pnl_percent(entry_price, exit_price, direction):
    position_value = STRATEGY['CAPITAL'] * STRATEGY['POSITION_SIZE']
    shares = position_value / entry_price
    
    if direction == 'long':
        pnl_rub = (exit_price - entry_price) * shares
    else:
        pnl_rub = (entry_price - exit_price) * shares
    
    commission_rub = (entry_price * shares * COMMISSION) + (exit_price * shares * COMMISSION)
    pnl_rub_net = pnl_rub - commission_rub
    
    pnl_percent = (pnl_rub_net / STRATEGY['CAPITAL']) * 100
    commission_percent = (commission_rub / STRATEGY['CAPITAL']) * 100
    
    return pnl_percent, commission_percent

# === ИНФОРМАЦИЯ ПО АКТИВУ (без графика) ===
async def get_asset_info(ticker):
    """Возвращает текстовую информацию по активу (MA18, MA50, ADX, рекомендация)"""
    df = await data_fetcher.fetch_candles_daily(ticker, 100)
    price = await data_fetcher.get_price(ticker)
    
    if df is None or price is None or price <= 0:
        return None, "Нет данных от MOEX"
    
    # Расчёт индикаторов для информации
    ma18 = df['close'].rolling(18).mean().iloc[-1] if len(df) >= 18 else None
    ma50 = df['close'].rolling(50).mean().iloc[-1] if len(df) >= 50 else None
    adx = calculate_adx(df)
    trend = get_trend(df)
    
    # Определяем рекомендацию
    recommendation = None
    if trend == "bullish" and adx > STRATEGY['ADX_THRESHOLD']:
        recommendation = f"Рекомендую открыть LONG по {TICKERS[ticker]['name']} ({ticker}) по цене {price:.2f} ₽"
    elif trend == "bearish" and adx > STRATEGY['ADX_THRESHOLD']:
        recommendation = f"Рекомендую открыть SHORT по {TICKERS[ticker]['name']} ({ticker}) по цене {price:.2f} ₽"
    
    adx_status = "тренд" if adx > STRATEGY['ADX_THRESHOLD'] else "флет"
    trend_ru = "БЫЧИЙ 🟢" if trend == "bullish" else "МЕДВЕЖИЙ 🔴" if trend == "bearish" else "НЕЙТРАЛЬНО"
    
    # Формируем сообщение
    msg = f"📊 {TICKERS[ticker]['name']} ({ticker})\n"
    msg += f"💰 Текущая: {price:.2f} ₽\n"
    if ma18 and ma50:
        msg += f"📈 MA18: {ma18:.2f} | MA50: {ma50:.2f}\n"
    msg += f"📊 ADX: {adx:.1f} ({adx_status})\n"
    
    if recommendation:
        msg += f"✅ {recommendation}"
    else:
        msg += "❌ Сигналов нет, ожидайте"
    
    return msg, None

# === ОТПРАВКА СИГНАЛОВ (с рекомендацией вместо /open) ===
async def check_and_send_all_signals():
    global last_signal_sent
    
    async with positions_lock:
        signals = []
        for ticker in ALL_TICKERS:
            if ticker == "SBER":
                continue
            if ticker in positions and positions[ticker].get('type') is not None:
                continue
            signal, data, _ = await get_signal_for_ticker(ticker)
            if signal and data and data.get('price') is not None and data['price'] > 0:
                signals.append({
                    'ticker': ticker,
                    'signal': signal,
                    'data': data,
                    'adx': data['adx']
                })
    
    if not signals:
        return
    
    signals.sort(key=lambda x: x['adx'], reverse=True)
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    
    msg = f"🔔🔔🔔 НАЙДЕНЫ СИГНАЛЫ 🔔🔔🔔\n\n"
    msg += f"⏰ {now.strftime('%H:%M')} | Найдено {len(signals)} сигналов\n\n"
    msg += f"САМЫЕ СИЛЬНЫЕ (ТОП-3):\n\n"
    
    top_count = min(3, len(signals))
    for i in range(top_count):
        s = signals[i]
        data = s['data']
        emoji = "🟢" if s['signal'] == 'LONG' else "🔴"
        direction = "LONG" if s['signal'] == 'LONG' else "SHORT"
        msg += f"{emoji} {data['name']} ({s['ticker']}) | {direction} | ADX {data['adx']}\n"
        if s['signal'] == 'LONG':
            msg += f"   ✅ Рекомендую открыть LONG по {data['name']} ({s['ticker']}) по цене {data['price']:.2f} ₽\n\n"
        else:
            msg += f"   ✅ Рекомендую открыть SHORT по {data['name']} ({s['ticker']}) по цене {data['price']:.2f} ₽\n\n"
    
    if len(signals) > top_count:
        msg += f"ОСТАЛЬНЫЕ {len(signals) - top_count} СИГНАЛОВ:\n\n"
        for i in range(top_count, len(signals)):
            s = signals[i]
            data = s['data']
            emoji = "🟢" if s['signal'] == 'LONG' else "🔴"
            direction = "LONG" if s['signal'] == 'LONG' else "SHORT"
            msg += f"{emoji} {data['name']} ({s['ticker']}) | {direction} | ADX {data['adx']}\n"
            if s['signal'] == 'LONG':
                msg += f"   ✅ Рекомендую открыть LONG по {data['name']} ({s['ticker']}) по цене {data['price']:.2f} ₽\n\n"
            else:
                msg += f"   ✅ Рекомендую открыть SHORT по {data['name']} ({s['ticker']}) по цене {data['price']:.2f} ₽\n\n"
    
    msg += f"\n🤖 Сигналы сгенерированы в {now.strftime('%H:%M')}"
    
    try:
        await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
        for s in signals:
            last_signal_sent[s['ticker']] = f"{s['signal']}_{int(s['data']['price'])}"
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
    
    gc.collect()

async def send_sber_hourly():
    global positions, daily_pnl
    
    if not CHANNEL_ID:
        return
    
    await reset_daily_pnl()
    
    signal, data, explanation = await get_sber_signal_detailed()
    
    if data is None or data.get('price') is None or data['price'] <= 0:
        logger.error("Нет корректной цены для Сбера, пропускаем сигнал")
        return
    
    price = data['price']
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    
    trend_ru = "БЫЧИЙ 🟢" if data.get('trend') == 'bullish' else "МЕДВЕЖИЙ 🔴" if data.get('trend') == 'bearish' else "НЕЙТРАЛЬНО ⚪"
    
    exit_needed = False
    exit_reason = None
    sber_position = None
    sber_entry = None
    
    async with positions_lock:
        sber_position = positions.get("SBER", {}).get('type')
        sber_entry = positions.get("SBER", {}).get('entry_price') if sber_position else None
    
    if sber_position and sber_entry:
        if sber_position == 'long':
            pnl_check = (price - sber_entry) / sber_entry * 100
        else:
            pnl_check = (sber_entry - price) / sber_entry * 100
        if pnl_check <= -STRATEGY['STOP_LOSS'] * 100:
            exit_needed = True
            exit_reason = f"Стоп-лосс: {pnl_check:.1f}%"
        elif pnl_check >= STRATEGY['TAKE_PROFIT'] * 100:
            exit_needed = True
            exit_reason = f"Тейк-профит: {pnl_check:.1f}%"
    
    if signal:
        msg = f"🔔🔔🔔 СБЕР — СИГНАЛ К {signal} !!! 🔔🔔🔔\n\n"
        msg += f"💰 Цена: {price:.2f} ₽\n"
        msg += f"📈 Тренд: {trend_ru}\n"
        msg += f"📊 ADX: {data['adx']}\n"
        msg += f"📊 MA10: {data['ma10']:.2f} | MA30: {data['ma30']:.2f}\n\n"
        msg += f"🎯 ПЛАН СДЕЛКИ:\n"
        msg += f"   Вход: {price:.2f} ₽\n"
        msg += f"   🛑 Стоп: {data['stop']:.2f} (-{STRATEGY['STOP_LOSS']*100:.0f}%)\n"
        msg += f"   🎯 Тейк: {data['target']:.2f} (+{STRATEGY['TAKE_PROFIT']*100:.0f}%)\n\n"
        msg += f"📊 Тип сигнала: {data['signal_type']}\n\n"
        msg += f"🤖 Сигнал сгенерирован в {now.strftime('%H:%M')}\n"
        
        if signal == 'LONG':
            msg += f"✅ Рекомендую открыть LONG по Сберу (SBER) по цене {price:.2f} ₽"
        else:
            msg += f"✅ Рекомендую открыть SHORT по Сберу (SBER) по цене {price:.2f} ₽"
    else:
        msg = f"📊 СБЕР - МОНИТОРИНГ {now.strftime('%H:%M')}\n\n"
        msg += f"💰 Цена: {price:.2f} ₽\n"
        msg += f"📈 Тренд: {trend_ru}\n"
        msg += f"📊 ADX: {data['adx']:.1f}\n"
        msg += f"📊 MA10: {data['ma10']:.2f} | MA30: {data['ma30']:.2f}\n\n"
        msg += f"❌ СИГНАЛА НЕТ\n\n"
        msg += f"📋 ПРИЧИНА:\n{explanation if explanation else 'Условия для входа не выполнены'}\n\n"
        msg += f"💡 Следующая проверка через час"
    
    if sber_position:
        pnl = (price - sber_entry) / sber_entry * 100 if sber_position == 'long' else (sber_entry - price) / sber_entry * 100
        msg += f"\n\n📌 ПОЗИЦИЯ ПО СБЕРУ: {sber_position.upper()}\n   P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%"
    
    if exit_needed:
        msg += f"\n\n🚨 ВЫХОД ИЗ ПОЗИЦИИ ПО СБЕРУ\n{exit_reason}"
        
        pnl_percent, commission_percent = calculate_pnl_percent(sber_entry, price, sber_position)
        
        async with positions_lock:
            daily_pnl += pnl_percent
            save_trade("SBER", sber_position, sber_entry, price, pnl_percent, commission_percent, positions.get("SBER", {}).get('is_manual', False))
            positions["SBER"] = {'type': None, 'entry_price': None, 'entry_time': None, 'is_manual': False}
    
    elif signal and not sber_position:
        async with positions_lock:
            positions["SBER"] = {
                'type': signal.lower(),
                'entry_price': price,
                'entry_time': now,
                'is_manual': False
            }
        msg += f"\n\n✅ ВХОД {signal} по сигналу бота"
    
    try:
        await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
    
    gc.collect()

async def reset_daily_pnl():
    global daily_pnl, last_reset_date
    msk = pytz.timezone('Europe/Moscow')
    today = datetime.now(msk).date()
    if last_reset_date != today:
        daily_pnl = 0.0
        last_reset_date = today

async def get_all_trends():
    results = {}
    for ticker in ALL_TICKERS:
        df = await data_fetcher.fetch_candles_daily(ticker, 100)
        price = await data_fetcher.get_price(ticker)
        trend = calc_trend_for_ticker(df)
        results[ticker] = {**TICKERS[ticker], "price": price, "trend": trend}
    return results

def get_tickers_list_text():
    text = "📋 ДОСТУПНЫЕ ТИКЕРЫ (17 активов)\n\n"
    for i, (ticker, info) in enumerate(TICKERS.items(), 1):
        text += f"{i}. {info['name']} ({ticker})\n"
    return text

# === АНАЛИЗ СИГНАЛА ===
async def get_signal_for_ticker(ticker):
    df = await data_fetcher.fetch_candles_daily(ticker, 100)
    price = await data_fetcher.get_price(ticker)
    
    if df is None or price is None or price <= 0:
        return None, None, "Нет данных от MOEX"
    
    trend = get_trend(df)
    adx = calculate_adx(df)
    
    ma10 = df['close'].rolling(10).mean()
    ma30 = df['close'].rolling(30).mean()
    last_ma10 = ma10.iloc[-1]
    last_ma30 = ma30.iloc[-1]
    prev_ma10 = ma10.iloc[-2] if len(ma10) > 1 else last_ma10
    prev_ma30 = ma30.iloc[-2] if len(ma30) > 1 else last_ma30
    
    golden_cross = (last_ma10 > last_ma30) and (prev_ma10 <= prev_ma30)
    dead_cross = (last_ma10 < last_ma30) and (prev_ma10 >= prev_ma30)
    
    if trend == "bullish" and (adx > STRATEGY['ADX_THRESHOLD'] or golden_cross):
        stop_price = price * (1 - STRATEGY['STOP_LOSS'])
        target_price = price * (1 + STRATEGY['TAKE_PROFIT'])
        return "LONG", {
            'ticker': ticker,
            'name': TICKERS[ticker]['name'],
            'price': price,
            'trend': trend,
            'adx': round(adx, 1),
            'target': target_price,
            'stop': stop_price,
            'signal_type': "ЗОЛОТОЕ ПЕРЕСЕЧЕНИЕ" if golden_cross else "ТРЕНД",
            'ma10': last_ma10,
            'ma30': last_ma30
        }, None
    
    if trend == "bearish" and (adx > STRATEGY['ADX_THRESHOLD'] or dead_cross):
        stop_price = price * (1 + STRATEGY['STOP_LOSS'])
        target_price = price * (1 - STRATEGY['TAKE_PROFIT'])
        return "SHORT", {
            'ticker': ticker,
            'name': TICKERS[ticker]['name'],
            'price': price,
            'trend': trend,
            'adx': round(adx, 1),
            'target': target_price,
            'stop': stop_price,
            'signal_type': "МЁРТВОЕ ПЕРЕСЕЧЕНИЕ" if dead_cross else "ТРЕНД",
            'ma10': last_ma10,
            'ma30': last_ma30
        }, None
    
    return None, {
        'ticker': ticker,
        'name': TICKERS[ticker]['name'],
        'price': price,
        'trend': trend,
        'adx': adx,
        'ma10': last_ma10,
        'ma30': last_ma30
    }, f"ADX = {adx:.1f} (нужно > {STRATEGY['ADX_THRESHOLD']})"

async def get_sber_signal_detailed():
    signal, data, explanation = await get_signal_for_ticker("SBER")
    if data is None or data.get('price') is None or data['price'] <= 0:
        return None, None, "Нет данных от MOEX"
    if signal:
        return signal, data, None
    else:
        reasons = []
        if data.get('adx', 0) < STRATEGY['ADX_THRESHOLD']:
            reasons.append(f"⚠️ ADX = {data['adx']:.1f} (нужно > {STRATEGY['ADX_THRESHOLD']}) — рынок во флете")
        if data.get('trend') == 'bearish':
            reasons.append(f"📉 Тренд медвежий — для LONG нужен бычий")
        elif data.get('trend') == 'bullish':
            reasons.append(f"📈 Тренд бычий — для SHORT нужен медвежий")
        if not reasons:
            reasons.append("Условия для входа не выполнены")
        return None, data, "\n".join(reasons)

# === ЛУННАЯ СТРАТЕГИЯ ===
async def lunar_notify():
    global lunar_notified_days
    while True:
        days_until_full = get_days_until_full_moon()
        days_until_new = get_days_until_new_moon()
        
        if days_until_full is not None and days_until_full <= 3 and days_until_full not in lunar_notified_days:
            lunar_notified_days.add(days_until_full)
            if days_until_full == 3:
                await bot.send_message(MY_CHAT_ID, f"🌕 ЧЕРЕЗ 3 ДНЯ ПОЛНОЛУНИЕ\nГотовьтесь к точке входа")
            elif days_until_full == 2:
                await bot.send_message(MY_CHAT_ID, f"🌕 ЧЕРЕЗ 2 ДНЯ ПОЛНОЛУНИЕ")
            elif days_until_full == 1:
                await bot.send_message(MY_CHAT_ID, f"🌕 ЗАВТРА ПОЛНОЛУНИЕ — ТОЧКА ВХОДА")
        
        if days_until_new is not None and days_until_new <= 3 and days_until_new not in lunar_notified_days:
            lunar_notified_days.add(days_until_new)
            if days_until_new == 3:
                await bot.send_message(MY_CHAT_ID, f"🌑 ЧЕРЕЗ 3 ДНЯ НОВОЛУНИЕ\nОжидайте повышенную волатильность")
            elif days_until_new == 2:
                await bot.send_message(MY_CHAT_ID, f"🌑 ЧЕРЕЗ 2 ДНЯ НОВОЛУНИЕ")
            elif days_until_new == 1:
                await bot.send_message(MY_CHAT_ID, f"🌑 ЗАВТРА НОВОЛУНИЕ\nБудьте осторожны с позициями")
        
        await asyncio.sleep(3600)

async def daily_lunar_summary():
    if not CHANNEL_ID:
        return
    msk = pytz.timezone('Europe/Moscow')
    today = datetime.now(msk).strftime('%Y-%m-%d')
    if get_last_summary_date() == today:
        return
    ph, _, nxt_full, nxt_new = get_lunar_info()
    trends = {}
    for ticker in ALL_TICKERS:
        df = await data_fetcher.fetch_candles_daily(ticker, 100)
        trend = calc_trend_for_ticker(df)
        trends[ticker] = trend
    long_cnt = sum(1 for t in trends.values() if t == 'бычий')
    short_cnt = sum(1 for t in trends.values() if t == 'медвежий')
    txt = f"🌙 {datetime.now(msk).strftime('%d.%m.%Y')}\n"
    if nxt_full:
        txt += f"🌕 Полнолуние {nxt_full.strftime('%d.%m.%Y')}\n"
    if nxt_new:
        txt += f"🌑 Новолуние {nxt_new.strftime('%d.%m.%Y')}\n"
    txt += f"🟢 LONG: {long_cnt}  🔴 SHORT: {short_cnt}\n💡 /status /balance"
    save_daily_summary(today, txt)
    try:
        await bot.send_message(CHANNEL_ID, txt, parse_mode='Markdown')
    except:
        pass

async def daily_loop():
    while True:
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        if now.hour == 10 and now.minute < 5:
            await daily_lunar_summary()
            clean_old_trades(30)
        await asyncio.sleep(60)

async def sber_hourly_loop():
    await asyncio.sleep(10)
    last_sent_hour = None
    while True:
        msk = pytz.timezone('Europe/Moscow')
        now = datetime.now(msk)
        current_hour = now.hour
        current_minute = now.minute
        if 10 <= current_hour <= 22 and current_minute < 3 and last_sent_hour != current_hour:
            await send_sber_hourly()
            last_sent_hour = current_hour
        await asyncio.sleep(60)

async def all_signals_check_loop():
    await asyncio.sleep(30)
    last_check_hour = None
    while True:
        msk = pytz.timezone('Europe/Moscow')
        now = datetime.now(msk)
        current_hour = now.hour
        if 10 <= current_hour <= 22 and last_check_hour != current_hour:
            await check_and_send_all_signals()
            last_check_hour = current_hour
        await asyncio.sleep(60)

# === НАСТРОЙКА БОТА ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# === КЛАВИАТУРА ===
keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 Фазы Луны"), KeyboardButton(text="📊 Информация")],
        [KeyboardButton(text="📋 Тикеры"), KeyboardButton(text="🚨 Срочный срез")],
    ],
    resize_keyboard=True
)

# === КОМАНДЫ ===
@dp.message_handler(commands=['start'])
async def start_cmd(m):
    await m.answer(
        "📊 АНАЛИТИК\n\n"
        "🔹 СБЕР (сигналы каждый час с 10:00 до 22:00)\n"
        "   Стратегия: MA10/MA30 + ADX | Стоп 6% | Тейк 12%\n"
        f"   Капитал: {STRATEGY['CAPITAL']:,} ₽ | Размер позиции: {STRATEGY['POSITION_SIZE']*100:.0f}%\n\n"
        "🔹 ОСТАЛЬНЫЕ 16 АКТИВОВ\n"
        "   Проверяются каждый час, при сигнале присылается список\n\n"
        "🔹 ЛУННАЯ СТРАТЕГИЯ\n"
        "   Ежедневная сводка в 10:00 | Уведомления за 3 дня до полнолуния и новолуния\n\n"
        "🔹 КОМАНДЫ:\n"
        "   /status — состояние по Сберу\n"
        "   /open SBER LONG 310 — открыть сделку\n"
        "   /close — закрыть позицию\n"
        "   /balance — статистика\n"
        "   /tickers — список всех тикеров\n\n"
        "🔹 КНОПКИ:\n"
        "   🌙 Фазы Луны — информация о луне\n"
        "   📊 Информация — данные по активу (введите тикер)\n"
        "   📋 Тикеры — список тикеров\n"
        "   🚨 Срочный срез — моментальный анализ всех 17 активов",
        reply_markup=keyboard, parse_mode='HTML')

@dp.message_handler(commands=['tickers'])
async def tickers_cmd(m):
    await m.answer(get_tickers_list_text(), parse_mode='HTML')

@dp.message_handler(commands=['status'])
async def status_cmd(m):
    price = await data_fetcher.get_price("SBER")
    df = await data_fetcher.fetch_candles_daily("SBER", 100)
    if price is None or df is None or price <= 0:
        await m.answer("⚠️ Нет данных")
        return
    trend = get_trend(df)
    adx = calculate_adx(df)
    ma10 = df['close'].rolling(10).mean().iloc[-1]
    ma30 = df['close'].rolling(30).mean().iloc[-1]
    trend_ru = "БЫЧИЙ 🟢" if trend == "bullish" else "МЕДВЕЖИЙ 🔴" if trend == "bearish" else "БОКОВИК ⚪"
    msg = f"📊 СБЕР - СТАТУС\n\n💰 Цена: {price:.2f} ₽\n"
    msg += f"📈 Тренд: {trend_ru}\n📊 MA10: {ma10:.2f} | MA30: {ma30:.2f}\n📈 ADX: {adx:.1f}\n"
    
    async with positions_lock:
        sber_pos = positions.get("SBER", {}).get('type')
        sber_entry = positions.get("SBER", {}).get('entry_price') if sber_pos else None
    
    if sber_pos and sber_entry:
        pnl = (price - sber_entry) / sber_entry * 100 if sber_pos == 'long' else (sber_entry - price) / sber_entry * 100
        msg += f"\n📌 ПОЗИЦИЯ: {sber_pos.upper()}\n   Вход: {sber_entry:.2f} ₽\n   P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n"
    else:
        msg += f"\n📌 ПОЗИЦИЯ: НЕТ\n"
    msg += f"\n📅 Дневной P&L: {'+' if daily_pnl >= 0 else ''}{daily_pnl:.2f}%"
    await m.answer(msg, parse_mode='HTML')
    gc.collect()

@dp.message_handler(commands=['open'])
async def open_cmd(m):
    global positions
    
    parts = m.text.split()
    if len(parts) != 4 or parts[2].upper() not in ['LONG', 'SHORT']:
        await m.answer("📝 /open SBER LONG 310.50\nили\n📝 /open GAZP SHORT 180.20")
        return
    
    ticker = parts[1].upper()
    if ticker not in TICKERS:
        await m.answer(f"❌ Тикер {ticker} не найден. Список: /tickers")
        return
    
    async with positions_lock:
        if ticker in positions and positions[ticker].get('type') is not None:
            await m.answer(f"⚠️ Уже есть открытая позиция по {TICKERS[ticker]['name']}. Сначала закройте /close")
            return
        
        direction = parts[2].upper()
        try:
            entry_price = float(parts[3])
        except:
            await m.answer("❌ Неверная цена")
            return
        
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        if ticker not in positions:
            positions[ticker] = {}
        positions[ticker]['type'] = direction.lower()
        positions[ticker]['entry_price'] = entry_price
        positions[ticker]['entry_time'] = now
        positions[ticker]['is_manual'] = True
    
    stop = entry_price * (1 - STRATEGY['STOP_LOSS']) if direction == 'LONG' else entry_price * (1 + STRATEGY['STOP_LOSS'])
    take = entry_price * (1 + STRATEGY['TAKE_PROFIT']) if direction == 'LONG' else entry_price * (1 - STRATEGY['TAKE_PROFIT'])
    msg = f"✅ Ручное открытие {direction} по {TICKERS[ticker]['name']} ({ticker})\n💰 Вход: {entry_price:.2f}\n🛑 Стоп: {stop:.2f}\n🎯 Тейк: {take:.2f}"
    await m.answer(msg)

@dp.message_handler(commands=['close'])
async def close_cmd(m):
    global positions, daily_pnl
    
    async with positions_lock:
        active_ticker = None
        active_pos = None
        for ticker, pos in positions.items():
            if pos.get('type') is not None:
                active_ticker = ticker
                active_pos = pos
                break
        
        if not active_ticker or active_pos is None:
            await m.answer("⚠️ Нет открытой позиции")
            return
    
    price = await data_fetcher.get_price(active_ticker)
    if not price or price <= 0:
        await m.answer("⚠️ Нет цены")
        return
    
    pnl_percent, commission_percent = calculate_pnl_percent(active_pos['entry_price'], price, active_pos['type'])
    
    async with positions_lock:
        daily_pnl += pnl_percent
        save_trade(active_ticker, active_pos['type'], active_pos['entry_price'], price, pnl_percent, commission_percent, active_pos.get('is_manual', False))
        positions[active_ticker] = {'type': None, 'entry_price': None, 'entry_time': None, 'is_manual': False}
    
    msg = f"✅ Закрыто {active_pos['type'].upper()} по {TICKERS[active_ticker]['name']} ({active_ticker})\n💰 Вход: {active_pos['entry_price']:.2f}\n💰 Выход: {price:.2f}\n📊 P&L: {pnl_percent:+.2f}%\n💸 Комиссия: {commission_percent:.2f}%"
    await m.answer(msg)

@dp.message_handler(commands=['balance'])
async def balance_cmd(m):
    stats = get_stats()
    price = await data_fetcher.get_price("SBER")
    msg = f"📊 СТАТИСТИКА ПО СДЕЛКАМ\n\n"
    msg += f"💰 Цена Сбера: {price:.2f} ₽\n\n" if price else ""
    msg += f"📈 ОБЩАЯ\n   Всего сделок: {stats['total_trades']}\n"
    msg += f"   Прибыльных: {stats['winning_trades']}\n   Убыточных: {stats['losing_trades']}\n"
    msg += f"   Win Rate: {stats['win_rate']:.1f}%\n   Общий P&L: {stats['total_pnl']:+.2f}%\n"
    msg += f"   Средний P&L: {stats['avg_pnl']:+.2f}%\n\n📅 СЕГОДНЯ\n   P&L: {daily_pnl:+.2f}%"
    await m.answer(msg, parse_mode='HTML')

# === КНОПКИ ===
@dp.message_handler(lambda msg: msg.text == "🌙 Фазы Луны")
async def btn_lunar(m):
    ph, dt, next_full, next_new = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    days_full = get_days_until_full_moon()
    days_new = get_days_until_new_moon()
    
    txt = f"🌙 {ph.upper()}\n📅 {now.strftime('%d.%m.%Y')}"
    
    if next_full:
        txt += f"\n\n🌕 ПОЛНОЛУНИЕ: {next_full.strftime('%d.%m.%Y %H:%M')}"
        if days_full is not None:
            txt += f"\n   ⏳ До полнолуния: {days_full} дн."
    
    if next_new:
        txt += f"\n\n🌑 НОВОЛУНИЕ: {next_new.strftime('%d.%m.%Y %H:%M')}"
        if days_new is not None:
            txt += f"\n   ⏳ До новолуния: {days_new} дн."
    
    await m.answer(txt)

@dp.message_handler(lambda msg: msg.text == "📊 Информация")
async def btn_info(m):
    await m.answer("Введите тикер из списка:\n" + ", ".join(ALL_TICKERS))

@dp.message_handler(lambda msg: msg.text == "📋 Тикеры")
async def btn_tickers(m):
    await m.answer(get_tickers_list_text(), parse_mode='HTML')

@dp.message_handler(lambda msg: msg.text == "🚨 Срочный срез")
async def btn_emergency_snapshot(m):
    await m.answer("🚨 Срочный срез... Анализирую все 17 активов 🔍")
    
    signals = []
    async with positions_lock:
        for ticker in ALL_TICKERS:
            if ticker == "SBER":
                continue
            if ticker in positions and positions[ticker].get('type') is not None:
                continue
            signal, data, _ = await get_signal_for_ticker(ticker)
            if signal and data and data.get('price') is not None and data['price'] > 0:
                signals.append({
                    'ticker': ticker,
                    'signal': signal,
                    'data': data,
                    'adx': data['adx']
                })
    
    sber_signal, sber_data, sber_expl = await get_sber_signal_detailed()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    
    msg = f"🚨 СРОЧНЫЙ СРЕЗ 🚨\n\n"
    msg += f"⏰ {now.strftime('%H:%M:%S')}\n\n"
    
    if sber_signal:
        msg += f"🔔 СБЕР: СИГНАЛ {sber_signal}\n"
        msg += f"   Цена: {sber_data['price']:.2f} | ADX: {sber_data['adx']}\n"
        msg += f"   🛑 {sber_data['stop']:.2f} | 🎯 {sber_data['target']:.2f}\n"
        if sber_signal == 'LONG':
            msg += f"   ✅ Рекомендую открыть LONG по Сберу (SBER) по цене {sber_data['price']:.2f} ₽\n\n"
        else:
            msg += f"   ✅ Рекомендую открыть SHORT по Сберу (SBER) по цене {sber_data['price']:.2f} ₽\n\n"
    else:
        msg += f"⚪ СБЕР: НЕТ СИГНАЛА\n"
        msg += f"   Цена: {sber_data['price']:.2f} | ADX: {sber_data['adx']:.1f}\n"
        msg += f"   {sber_expl.split(chr(10))[0] if sber_expl else 'Условия не выполнены'}\n\n"
    
    if signals:
        signals.sort(key=lambda x: x['adx'], reverse=True)
        msg += f"📊 СИГНАЛЫ ПО ОСТАЛЬНЫМ ({len(signals)})\n\n"
        for s in signals:
            data = s['data']
            emoji = "🟢" if s['signal'] == 'LONG' else "🔴"
            direction = "LONG" if s['signal'] == 'LONG' else "SHORT"
            msg += f"{emoji} {data['name']} ({s['ticker']}) | {direction} | ADX {data['adx']}\n"
            if s['signal'] == 'LONG':
                msg += f"   ✅ Рекомендую открыть LONG по {data['name']} ({s['ticker']}) по цене {data['price']:.2f} ₽\n\n"
            else:
                msg += f"   ✅ Рекомендую открыть SHORT по {data['name']} ({s['ticker']}) по цене {data['price']:.2f} ₽\n\n"
    else:
        msg += f"⚪ СИГНАЛОВ ПО ОСТАЛЬНЫМ НЕТ\n"
    
    msg += f"\n🤖 Срез выполнен вручную"
    
    await m.answer(msg, parse_mode='HTML')
    gc.collect()

@dp.message_handler(lambda msg: msg.text.upper() in ALL_TICKERS)
async def info_by_ticker(m):
    ticker = m.text.upper()
    msg = await m.answer(f"📊 Загружаю данные по {TICKERS[ticker]['name']}...")
    
    info_msg, error = await get_asset_info(ticker)
    
    if error:
        await msg.edit_text(error)
    else:
        await msg.delete()
        await m.answer(info_msg, parse_mode='HTML')
    
    gc.collect()

# === ВЕБ-ДАШБОРД (ОТКЛЮЧЁН) ===
async def dashboard(req):
    return web.Response(text="Дашборд отключён для экономии памяти. Работает Telegram-бот.", content_type='text/html')

async def moex_health(req):
    status = "ok" if DataFetcher.moex_error_count < 5 else "degraded"
    return web.json_response({
        "status": status,
        "last_success": DataFetcher.moex_last_success.isoformat() if DataFetcher.moex_last_success else None,
        "error_count": DataFetcher.moex_error_count
    })

async def web_server():
    # Веб-сервер отключён для экономии памяти
    logger.info("🌐 Веб-сервер отключён для экономии памяти")
    return

# === ЗАПУСК ===
async def main():
    init_db()
    asyncio.create_task(data_fetcher.healthcheck_moex())
    
    tasks = [
        asyncio.create_task(daily_loop()),
        asyncio.create_task(lunar_notify()),
        asyncio.create_task(sber_hourly_loop()),
        asyncio.create_task(all_signals_check_loop()),
    ]
    
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("📢 Получен сигнал завершения, останавливаю бота...")
        stop_event.set()
    
    loop.add_signal_handler(signal.SIGTERM, signal_handler)
    loop.add_signal_handler(signal.SIGINT, signal_handler)
    
    logger.info("🚀 Бот запущен, начинаю polling...")
    
    async def run_polling():
        try:
            await dp.start_polling()
        except Exception as e:
            logger.error(f"❌ Ошибка в polling: {e}")
        finally:
            stop_event.set()
    
    polling_task = asyncio.create_task(run_polling())
    await stop_event.wait()
    
    logger.info("🛑 Останавливаю бота...")
    
    try:
        await dp.stop_polling()
    except Exception as e:
        logger.error(f"Ошибка при остановке polling: {e}")
    
    polling_task.cancel()
    
    for task in tasks:
        task.cancel()
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    if bot:
        try:
            await bot.close()
        except Exception as e:
            logger.error(f"Ошибка закрытия bot: {e}")
    
    logger.info("✅ Бот остановлен")

async def run_bot_with_retry():
    while True:
        try:
            await main()
        except Exception as e:
            logger.error(f"❌ Бот упал с ошибкой: {e}. Перезапуск через 10 секунд...")
            await asyncio.sleep(10)
        else:
            break

if __name__ == "__main__":
    print("=" * 50)
    print("АНАЛИТИК | ОПТИМИЗИРОВАННАЯ ВЕРСИЯ")
    print("Сбер: сигналы каждый час | Остальные: только при сигнале")
    print("Кнопка «Информация» — данные по активу без графика")
    print("Срочный срез — с рекомендациями вместо команд")
    print("Дашборд отключён для экономии памяти")
    print(f"Капитал: {STRATEGY['CAPITAL']:,} ₽ | Размер позиции: {STRATEGY['POSITION_SIZE']*100:.0f}%")
    print("=" * 50)
    
    asyncio.run(run_bot_with_retry())
