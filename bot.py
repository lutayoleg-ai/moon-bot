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
from aiohttp import web
import matplotlib.pyplot as plt
from io import BytesIO
import sqlite3
import ta

warnings.filterwarnings('ignore')

# === ТОКЕН ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = 414210743
CHANNEL_ID = os.environ.get("CHANNEL_ID")

if not BOT_TOKEN:
    raise ValueError("❌ Токен не найден")

# === КЭШ ===
data_cache = {}
cache_ttl = 60  # 1 минута для реального времени

def get_from_cache(key):
    if key in data_cache:
        data, ts = data_cache[key]
        if (datetime.now() - ts).seconds < cache_ttl:
            return data
        del data_cache[key]
    return None

def set_to_cache(key, data):
    data_cache[key] = (data, datetime.now())

def clear_cache():
    data_cache.clear()

# === АКТИВЫ ===
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

# === ПАРАМЕТРЫ СТРАТЕГИИ ===
STRATEGY = {
    'MA_FAST': 20,
    'MA_SLOW': 50,
    'RSI_OVERBOUGHT': 75,
    'RSI_OVERSOLD': 30,
    'VOLUME_RATIO_LONG': 1.5,
    'VOLUME_RATIO_SHORT': 1.5,
    'ADX_THRESHOLD': 25,
    'STOP_LOSS': 0.05,      # 5% стоп
    'TAKE_PROFIT': 0.08,    # 8% тейк
    'DAILY_LOSS_LIMIT': 0.03 # 3% просадка в день
}

# === СОСТОЯНИЕ ===
current_position = {'type': None, 'entry_price': None, 'entry_time': None, 'signal_type': None}
last_signal_sent = {'signal': None, 'price': None, 'time': None}
daily_pnl = 0.0
last_reset_date = None

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, type TEXT, entry REAL, exit REAL, pnl REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_summary (date TEXT PRIMARY KEY, summary TEXT)''')

def save_trade(trade_type, entry, exit_price, pnl):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("INSERT INTO trades (date, type, entry, exit, pnl) VALUES (?, ?, ?, ?, ?)",
                  (datetime.now().isoformat(), trade_type, entry, exit_price, pnl))

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
    ]
}

def get_lunar_info():
    msk = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk)
    next_full = None
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if dt > now:
            next_full = dt
            break
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if (now - dt).days <= 1 and (now - dt).days >= 0:
            return "полнолуние", dt, next_full
        if (dt - now).days == 1:
            return "полнолуние_завтра", dt, next_full
    return "обычный день", None, next_full

# === MOEX ===
class DataFetcher:
    def __init__(self):
        self.session = None

    async def get_session(self):
        if self.session is None or self.session.closed:
            ctx = ssl.create_default_context(cafile=certifi.where())
            conn = aiohttp.TCPConnector(ssl=ctx)
            self.session = aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=15), headers={'User-Agent': 'Mozilla/5.0'})
        return self.session

    async def get_price(self, ticker):
        key = f"price_{ticker}"
        cached = get_from_cache(key)
        if cached is not None:
            return cached
        try:
            s = await self.get_session()
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
            async with s.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
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
                                            if 1 < p < 20000:
                                                set_to_cache(key, p)
                                                return p
                                        except:
                                            pass
        except:
            pass
        return None

    async def fetch_candles_daily(self, ticker, days=100):
        """Дневные свечи для свинг-анализа"""
        key = f"daily_{ticker}_{days}"
        cached = get_from_cache(key)
        if cached is not None:
            return cached
        try:
            s = await self.get_session()
            end = datetime.now()
            start = end - timedelta(days=days)
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}/candles.json"
            params = {'from': start.strftime('%Y-%m-%d'), 'till': end.strftime('%Y-%m-%d'), 'interval': 24}
            async with s.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles = data.get('candles', {})
                    rows = candles.get('data', [])
                    cols = candles.get('columns', [])
                    if rows and len(rows) >= 3:
                        idx_date = next((i for i, c in enumerate(cols) if c.lower() in ('begin', 'date')), None)
                        idx_open = next((i for i, c in enumerate(cols) if c.lower() == 'open'), None)
                        idx_high = next((i for i, c in enumerate(cols) if c.lower() == 'high'), None)
                        idx_low = next((i for i, c in enumerate(cols) if c.lower() == 'low'), None)
                        idx_close = next((i for i, c in enumerate(cols) if c.lower() in ('close', 'value')), None)
                        idx_volume = next((i for i, c in enumerate(cols) if c.lower() == 'volume'), None)
                        if idx_date is not None and idx_close is not None:
                            records = []
                            for row in rows:
                                if len(row) > max(idx_date, idx_close):
                                    try:
                                        records.append({
                                            'date': pd.to_datetime(row[idx_date]),
                                            'open': float(row[idx_open]) if idx_open is not None and len(row) > idx_open else None,
                                            'high': float(row[idx_high]) if idx_high is not None and len(row) > idx_high else None,
                                            'low': float(row[idx_low]) if idx_low is not None and len(row) > idx_low else None,
                                            'close': float(row[idx_close]),
                                            'volume': float(row[idx_volume]) if idx_volume is not None and len(row) > idx_volume else 0
                                        })
                                    except:
                                        pass
                            if len(records) >= 5:
                                df = pd.DataFrame(records).sort_values('date').reset_index(drop=True)
                                set_to_cache(key, df)
                                return df
        except Exception as e:
            print(f"Ошибка fetch_candles_daily: {e}")
        return None

    async def fetch_candles_intraday(self, ticker, minutes=60):
        """15-минутные свечи для внутридневного анализа"""
        key = f"intraday_{ticker}_{minutes}"
        cached = get_from_cache(key)
        if cached is not None:
            return cached
        try:
            s = await self.get_session()
            end = datetime.now()
            start = end - timedelta(minutes=minutes)
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}/candles.json"
            params = {'from': start.strftime('%Y-%m-%d'), 'till': end.strftime('%Y-%m-%d'), 'interval': 15}
            async with s.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles = data.get('candles', {})
                    rows = candles.get('data', [])
                    cols = candles.get('columns', [])
                    if rows and len(rows) >= 10:
                        idx_date = next((i for i, c in enumerate(cols) if c.lower() in ('begin', 'date')), None)
                        idx_close = next((i for i, c in enumerate(cols) if c.lower() in ('close', 'value')), None)
                        idx_volume = next((i for i, c in enumerate(cols) if c.lower() == 'volume'), None)
                        if idx_date is not None and idx_close is not None:
                            records = []
                            for row in rows[-minutes//15:]:
                                if len(row) > max(idx_date, idx_close):
                                    try:
                                        records.append({
                                            'date': pd.to_datetime(row[idx_date]),
                                            'close': float(row[idx_close]),
                                            'volume': float(row[idx_volume]) if idx_volume is not None and len(row) > idx_volume else 0
                                        })
                                    except:
                                        pass
                            if len(records) >= 5:
                                df = pd.DataFrame(records).sort_values('date').reset_index(drop=True)
                                set_to_cache(key, df)
                                return df
        except Exception as e:
            print(f"Ошибка fetch_candles_intraday: {e}")
        return None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

data_fetcher = DataFetcher()

# === РАСЧЁТ ИНДИКАТОРОВ ===
def calculate_macd(series, fast=12, slow=26, signal=9):
    """Правильный MACD"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calculate_adx(df, period=14):
    """ADX для определения тренда"""
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

def calc_trend(df):
    if df is None or len(df) < 30:
        return "недостаточно данных"
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    ma50 = df['close'].rolling(50).mean().iloc[-1] if len(df) >= 50 else ma20
    if np.isnan(ma20) or np.isnan(ma50):
        return "недостаточно данных"
    spread = abs(ma20 - ma50) / ma50 * 100
    return "боковик" if spread < 0.7 else ("бычий" if ma20 > ma50 else "медвежий")

def calc_indicators(df):
    if df is None or len(df) < 30:
        return None
    rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi().iloc[-1]
    macd_line, macd_signal, _ = calculate_macd(df['close'], 12, 26, 9)
    return {
        'rsi': round(rsi, 1),
        'rsi_status': "перекупленность" if rsi > 70 else "перепроданность" if rsi < 30 else "нейтрально",
        'macd_status': "бычий" if macd_line.iloc[-1] > macd_signal.iloc[-1] else "медвежий"
    }

# === ГЕНЕРАЦИЯ СИГНАЛОВ ===
async def get_sber_swing_signal(df, price):
    """Свинг-сигнал (дневные данные)"""
    if df is None or len(df) < 50:
        return None, None
    
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    
    # MA
    ma20 = df['close'].rolling(20).mean()
    ma50 = df['close'].rolling(50).mean()
    last_ma20 = ma20.iloc[-1]
    last_ma50 = ma50.iloc[-1]
    prev_ma20 = ma20.iloc[-2] if len(ma20) > 1 else last_ma20
    prev_ma50 = ma50.iloc[-2] if len(ma50) > 1 else last_ma50
    
    # RSI
    rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi().iloc[-1]
    
    # MACD правильный
    macd_line, macd_signal, _ = calculate_macd(df['close'], 12, 26, 9)
    macd_bullish = macd_line.iloc[-1] > macd_signal.iloc[-1]
    
    # ADX
    adx = calculate_adx(df)
    
    # Объём
    volume_ratio = 1.0
    if 'volume' in df.columns and len(df) > 20:
        vol_avg = df['volume'].rolling(20).mean().iloc[-1]
        volume_ratio = df['volume'].iloc[-1] / vol_avg if vol_avg > 0 else 1.0
    
    # Пересечения MA
    ma_cross_up = (last_ma20 > last_ma50) and (prev_ma20 <= prev_ma50)
    ma_cross_down = (last_ma20 < last_ma50) and (prev_ma20 >= prev_ma50)
    
    # LONG условия
    long_cond = (
        (price > last_ma50 and last_ma20 > last_ma50) or ma_cross_up
    ) and volume_ratio > STRATEGY['VOLUME_RATIO_LONG'] and rsi < STRATEGY['RSI_OVERBOUGHT'] and adx > STRATEGY['ADX_THRESHOLD']
    
    # SHORT условия
    short_cond = (
        (price < last_ma50 and last_ma20 < last_ma50) or ma_cross_down
    ) and volume_ratio > STRATEGY['VOLUME_RATIO_SHORT'] and rsi > STRATEGY['RSI_OVERSOLD'] and adx > STRATEGY['ADX_THRESHOLD']
    
    if long_cond:
        return "LONG", {
            'price': price,
            'target': price * (1 + STRATEGY['TAKE_PROFIT']),
            'stop': price * (1 - STRATEGY['STOP_LOSS']),
            'rsi': round(rsi, 1),
            'adx': round(adx, 1),
            'ma20': round(last_ma20, 2),
            'ma50': round(last_ma50, 2),
            'volume_ratio': round(volume_ratio, 1),
            'macd': "бычий" if macd_bullish else "медвежий"
        }
    if short_cond:
        return "SHORT", {
            'price': price,
            'target': price * (1 - STRATEGY['TAKE_PROFIT']),
            'stop': price * (1 + STRATEGY['STOP_LOSS']),
            'rsi': round(rsi, 1),
            'adx': round(adx, 1),
            'ma20': round(last_ma20, 2),
            'ma50': round(last_ma50, 2),
            'volume_ratio': round(volume_ratio, 1),
            'macd': "медвежий" if not macd_bullish else "бычий"
        }
    
    return None, None

async def get_sber_intraday_signal(df, price):
    """Внутридневной сигнал (15-минутные данные)"""
    if df is None or len(df) < 20:
        return None, None
    
    last = df.iloc[-1]
    
    # Быстрая MA для внутридневного
    ma10 = df['close'].rolling(10).mean().iloc[-1]
    
    # RSI на 15-минутках
    rsi = ta.momentum.RSIIndicator(df['close'], window=10).rsi().iloc[-1]
    
    # MACD быстрый
    macd_line, macd_signal, _ = calculate_macd(df['close'], 5, 13, 5)
    macd_bullish = macd_line.iloc[-1] > macd_signal.iloc[-1]
    
    # Объём
    volume_ratio = 1.0
    if 'volume' in df.columns and len(df) > 10:
        vol_avg = df['volume'].rolling(10).mean().iloc[-1]
        volume_ratio = df['volume'].iloc[-1] / vol_avg if vol_avg > 0 else 1.0
    
    # LONG
    long_cond = (
        price > ma10 and
        volume_ratio > 1.2 and
        35 < rsi < 65 and
        macd_bullish and
        last['close'] > df['close'].iloc[-2] * 1.001  # небольшой рост
    )
    
    # SHORT
    short_cond = (
        price < ma10 and
        volume_ratio > 1.2 and
        35 < rsi < 65 and
        not macd_bullish and
        last['close'] < df['close'].iloc[-2] * 0.999  # небольшое падение
    )
    
    if long_cond:
        return "LONG", {
            'price': price,
            'target': price * 1.01,
            'stop': price * 0.995,
            'rsi': round(rsi, 1),
            'volume_ratio': round(volume_ratio, 1)
        }
    if short_cond:
        return "SHORT", {
            'price': price,
            'target': price * 0.99,
            'stop': price * 1.005,
            'rsi': round(rsi, 1),
            'volume_ratio': round(volume_ratio, 1)
        }
    
    return None, None

async def get_exit_signal(df, price, position_type):
    """Сигнал на выход из позиции"""
    if df is None or len(df) < 20 or position_type is None:
        return False, None
    
    rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi().iloc[-1]
    macd_line, macd_signal, _ = calculate_macd(df['close'], 12, 26, 9)
    
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    ma50 = df['close'].rolling(50).mean().iloc[-1]
    
    if position_type == 'long':
        # Выход из лонга
        if rsi > STRATEGY['RSI_OVERBOUGHT']:
            return True, f"RSI={rsi:.1f} (перекупленность)"
        if macd_line.iloc[-1] < macd_signal.iloc[-1]:
            return True, "MACD разворот вниз"
        if ma20 < ma50:
            return True, "MA20 ниже MA50"
            
    elif position_type == 'short':
        # Выход из шорта
        if rsi < STRATEGY['RSI_OVERSOLD']:
            return True, f"RSI={rsi:.1f} (перепроданность)"
        if macd_line.iloc[-1] > macd_signal.iloc[-1]:
            return True, "MACD разворот вверх"
        if ma20 > ma50:
            return True, "MA20 выше MA50"
    
    return False, None

async def reset_daily_pnl():
    global daily_pnl, last_reset_date
    msk = pytz.timezone('Europe/Moscow')
    today = datetime.now(msk).date()
    if last_reset_date != today:
        daily_pnl = 0.0
        last_reset_date = today

# === ОТПРАВКА СИГНАЛА ===
async def send_sber_signal():
    global current_position, last_signal_sent, daily_pnl
    
    if not CHANNEL_ID:
        return
    
    # Сброс дневного P&L
    await reset_daily_pnl()
    
    # Получаем данные
    df_daily = await data_fetcher.fetch_candles_daily("SBER", 100)
    df_intraday = await data_fetcher.fetch_candles_intraday("SBER", 120)
    price = await data_fetcher.get_price("SBER")
    
    if df_daily is None or price is None:
        return
    
    # Проверка лимита дневной просадки
    if daily_pnl < -STRATEGY['DAILY_LOSS_LIMIT']:
        if current_position['type']:
            msg = f"🚨 ДНЕВНОЙ ЛИМИТ ПРОСАДКИ ({daily_pnl*100:.1f}%)\nТорговля на сегодня остановлена"
            await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
            current_position['type'] = None
        return
    
    # Генерируем сигналы
    swing_signal, swing_data = await get_sber_swing_signal(df_daily, price)
    intra_signal, intra_data = await get_sber_intraday_signal(df_intraday, price)
    
    # Проверяем выход из позиции
    exit_needed, exit_reason = await get_exit_signal(df_daily, price, current_position['type'])
    
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    
    # Формируем сообщение ТОЛЬКО если сигнал изменился
    signal_key = f"{swing_signal}_{intra_signal}_{current_position['type']}"
    if signal_key == last_signal_sent.get('signal') and (now - last_signal_sent.get('time', datetime.min)).seconds < 300:
        return  # Не спамим
    
    msg = f"📊 <b>СБЕР</b> {now.strftime('%d.%m %H:%M')}\n━━━━━━━━━━━━━━━━━━━\n💰 Цена: <b>{price:.2f} ₽</b>\n\n"
    
    # Внутридневной сигнал
    if intra_signal:
        msg += f"🟢 ВНУТРИДНЕВНОЙ: {intra_signal}\n   🎯 {intra_data['target']:.2f} | 🛑 {intra_data['stop']:.2f}\n   RSI: {intra_data['rsi']} | Объём: {intra_data['volume_ratio']}x\n\n"
    else:
        msg += f"⚪ ВНУТРИДНЕВНОЙ: НЕТ\n\n"
    
    # Свинг-сигнал
    if swing_signal:
        msg += f"🟢 СВИНГ: {swing_signal}\n   🎯 {swing_data['target']:.2f} | 🛑 {swing_data['stop']:.2f}\n   MA20: {swing_data['ma20']} | MA50: {swing_data['ma50']}\n   RSI: {swing_data['rsi']} | ADX: {swing_data['adx']} | Объём: {swing_data['volume_ratio']}x\n\n"
    else:
        msg += f"⚪ СВИНГ: НЕТ (ADX<25 или флет)\n\n"
    
    # Текущая позиция
    if current_position['type']:
        pnl = (price - current_position['entry_price']) / current_position['entry_price'] * 100
        if current_position['type'] == 'short':
            pnl = -pnl
        msg += f"📌 ПОЗИЦИЯ: {current_position['type'].upper()}\n   P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n"
    
    # Выход
    if exit_needed:
        msg += f"\n🚨 ВЫХОД: {exit_reason}\n"
        # Закрываем позицию
        pnl_final = (price - current_position['entry_price']) / current_position['entry_price'] * 100
        if current_position['type'] == 'short':
            pnl_final = -pnl_final
        daily_pnl += pnl_final / 100
        save_trade(current_position['type'], current_position['entry_price'], price, pnl_final)
        current_position['type'] = None
    
    # Вход в новую позицию (только если нет открытой)
    elif not current_position['type']:
        # Приоритет: свинг сильнее, но если его нет — внутридневной
        if swing_signal:
            current_position['type'] = swing_signal.lower()
            current_position['entry_price'] = swing_data['price']
            current_position['entry_time'] = now
            current_position['signal_type'] = 'swing'
            msg += f"\n✅ ВХОД {swing_signal} (свинг)\n"
        elif intra_signal:
            # Проверяем, не поздно ли (до 18:45)
            if now.hour < 18 or (now.hour == 18 and now.minute < 45):
                current_position['type'] = intra_signal.lower()
                current_position['entry_price'] = intra_data['price']
                current_position['entry_time'] = now
                current_position['signal_type'] = 'intraday'
                msg += f"\n✅ ВХОД {intra_signal} (внутридневной)\n"
    
    msg += f"\n🤖 Следующий сигнал через 15 мин"
    
    # Отправляем
    try:
        await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
        last_signal_sent = {'signal': signal_key, 'price': price, 'time': now}
    except Exception as e:
        print(f"Ошибка отправки: {e}")

async def sber_signal_loop():
    await asyncio.sleep(5)
    await send_sber_signal()
    while True:
        await asyncio.sleep(15 * 60)
        await send_sber_signal()

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
async def get_all_trends():
    results = {}
    for ticker in ALL_TICKERS:
        df = await data_fetcher.fetch_candles_daily(ticker, 100)
        price = await data_fetcher.get_price(ticker)
        trend = calc_trend(df)
        results[ticker] = {**TICKERS[ticker], "price": price, "trend": trend}
    return results

# === НАСТРОЙКА ЛОГГИНГА ===
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# === КЛАВИАТУРА ===
keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 Фазы Луны")],
        [KeyboardButton(text="📈 Открыть позицию")],
        [KeyboardButton(text="📊 Историческая статистика")],
        [KeyboardButton(text="📈 График акции")],
    ],
    resize_keyboard=True
)

# === КОМАНДЫ ===
@dp.message_handler(commands=['start'])
async def start_cmd(m):
    await m.answer(
        "📊 **АНАЛИТИК**\n\n"
        "📊 17 акций\n\n"
        "🔹 **КОМАНДЫ:**\n"
        "   📈 Открыть позицию\n"
        "   📊 Историческая статистика\n"
        "   📈 График акции\n\n"
        "🌐 Дашборд: https://moon-bot-55tl.onrender.com/dashboard",
        reply_markup=keyboard, parse_mode='Markdown')

@dp.message_handler(lambda msg: msg.text == "🌙 Фазы Луны")
async def btn_lunar(m):
    ph, dt, nxt = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    txt = f"🌙 {ph.upper()}\n📅 {now.strftime('%d.%m.%Y')}\n"
    if nxt:
        txt += f"🌕 Полнолуние: {nxt.strftime('%d.%m.%Y %H:%M')}"
    await m.answer(txt)

@dp.message_handler(lambda msg: msg.text == "📊 Историческая статистика")
async def btn_stats(m):
    s = sorted(TICKERS.items(), key=lambda x: -x[1]['return_bull'])
    txt = "📊 **ТОП-10**\n"
    for i, (t, d) in enumerate(s[:10], 1):
        txt += f"{i}. {d['name']}: +{d['return_bull']:.2f}% | {d['return_bear']:.2f}%\n"
    await m.answer(txt, parse_mode='Markdown')

@dp.message_handler(lambda msg: msg.text == "📈 Открыть позицию")
async def btn_open(m):
    ph, _, nxt = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    if ph == "полнолуние":
        await m.answer("🌕 **ТОЧКА ВХОДА!**")
    else:
        days = (nxt - now).days if nxt else 0
        await m.answer(f"⏸ Сигнала нет\n⏳ Следующее полнолуние: {nxt.strftime('%d.%m.%Y') if nxt else '—'} (через {days} дн.)")

@dp.message_handler(lambda msg: msg.text == "📈 График акции")
async def btn_chart(m):
    await m.answer("Введите тикер: SBER, VTBR, GAZP...")

@dp.message_handler(lambda msg: msg.text.upper() in ALL_TICKERS)
async def chart(m):
    ticker = m.text.upper()
    msg = await m.answer(f"📈 График {TICKERS[ticker]['name']}...")
    df = await data_fetcher.fetch_candles_daily(ticker, 100)
    if df is None:
        await msg.edit_text("Нет данных")
        return
    plt.figure(figsize=(12,5))
    plt.plot(df['date'], df['close'], 'b-', label='Цена')
    if len(df) >= 20:
        plt.plot(df['date'], df['close'].rolling(20).mean(), 'g--', label='MA20')
    if len(df) >= 50:
        plt.plot(df['date'], df['close'].rolling(50).mean(), 'r--', label='MA50')
    plt.title(TICKERS[ticker]['name'])
    plt.grid()
    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    ind = calc_indicators(df)
    cap = f"{TICKERS[ticker]['name']}\nRSI: {ind['rsi']} ({ind['rsi_status']}) | {ind['macd_status']}" if ind else ""
    await msg.delete()
    await m.answer_photo(buf, caption=cap)

# === ЕЖЕДНЕВНАЯ СВОДКА ===
async def daily_job():
    if not CHANNEL_ID:
        return
    msk = pytz.timezone('Europe/Moscow')
    today = datetime.now(msk).strftime('%Y-%m-%d')
    if get_last_summary_date() == today:
        return
    ph, _, nxt = get_lunar_info()
    tr = await get_all_trends()
    long = sum(1 for d in tr.values() if d['trend'] == 'бычий')
    short = sum(1 for d in tr.values() if d['trend'] == 'медвежий')
    txt = f"🌙 **{datetime.now(msk).strftime('%d.%m.%Y')}**\n"
    if nxt:
        txt += f"🌕 Полнолуние {nxt.strftime('%d.%m.%Y')}\n"
    txt += f"🟢 LONG: {long}  🔴 SHORT: {short}"
    save_daily_summary(today, txt)
    try:
        await bot.send_message(CHANNEL_ID, txt, parse_mode='Markdown')
    except:
        pass

async def daily_loop():
    while True:
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        if now.hour == 10 and now.minute < 5:
            await daily_job()
        await asyncio.sleep(60)

async def moon_notify():
    last = {}
    while True:
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        _, _, nxt = get_lunar_info()
        if nxt:
            if (nxt - timedelta(days=1)).date() == now.date() and last.get('before') != nxt.date():
                last['before'] = nxt.date()
                await bot.send_message(MY_CHAT_ID, f"🌕 ЗАВТРА ПОЛНОЛУНИЕ — точка входа")
            if nxt.date() == now.date() and last.get('today') != nxt.date():
                last['today'] = nxt.date()
                await bot.send_message(MY_CHAT_ID, f"🌕 СЕГОДНЯ ПОЛНОЛУНИЕ — ТОЧКА ВХОДА")
        await asyncio.sleep(3600)

# === WEB ДАШБОРД ===
async def dashboard(req):
    tr = await get_all_trends()
    ph, _, nxt = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    long = sum(1 for d in tr.values() if d['trend'] == 'бычий')
    short = sum(1 for d in tr.values() if d['trend'] == 'медвежий')
    side = sum(1 for d in tr.values() if d['trend'] == 'боковик')
    rows = ""
    for t, d in tr.items():
        p = f"{d['price']:.2f}" if d['price'] else "—"
        cls = "bull" if d['trend'] == 'бычий' else "bear" if d['trend'] == 'медвежий' else "neutral"
        sym = "🟢" if d['trend'] == 'бычий' else "🔴" if d['trend'] == 'медвежий' else "⚪"
        rows += f"<tr><td>{d['name']}</td><td>{t}</td><td>{p}</td><td class='{cls}'>{sym} {d['trend']}</td><td class='bull'>+{d['return_bull']:.2f}%</td><td class='bear'>+{d['return_bear']:.2f}%</td></tr>"
    html = f"""
    <!DOCTYPE html>
    <html><head><title>Аналитик</title><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
        body{{background:#0f0f1a;color:#eee;font-family:system-ui;padding:20px;}}
        .card{{background:#1a1a2e;border-radius:20px;padding:20px;margin-bottom:20px;}}
        .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-bottom:20px;}}
        .stat{{background:#1a1a2e;border-radius:20px;padding:20px;text-align:center;}}
        .num{{font-size:2.5rem;font-weight:bold;color:#f0c040;}}
        table{{width:100%;border-collapse:collapse;background:#1a1a2e;border-radius:20px;overflow:hidden;}}
        th,td{{padding:12px;text-align:left;border-bottom:1px solid #2a2a3e;}}
        th{{background:#f0c04020;color:#f0c040;}}
        .bull{{color:#4ade80;}}.bear{{color:#f87171;}}.neutral{{color:#facc15;}}
        .footer{{text-align:center;color:#666;margin-top:20px;}}
    </style>
    </head>
    <body>
    <div class="card"><h1>📊 АНАЛИТИК</h1><div>{now.strftime('%d.%m.%Y %H:%M')} | {ph}</div><div>🌕 Полнолуние: {nxt.strftime('%d.%m.%Y') if nxt else '—'}</div></div>
    <div class="grid"><div class="stat"><div class="num">{long}</div><div>LONG</div></div><div class="stat"><div class="num">{short}</div><div>SHORT</div></div><div class="stat"><div class="num">{side}</div><div>БОКОВИК</div></div></div>
    <table><thead><tr><th>Актив</th><th>Тикер</th><th>Цена</th><th>Тренд</th><th>LONG</th><th>SHORT</th></tr></thead><tbody>{rows}</tbody></table>
    <div class="footer">Обновляется каждые 5 минут</div>
    </body></html>
    """
    return web.Response(text=html, content_type='text/html')

async def health(req):
    return web.Response(text="OK")

async def web_server():
    app = web.Application()
    app.router.add_get('/health', health)
    app.router.add_get('/dashboard', dashboard)
    app.router.add_get('/', dashboard)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 10000).start()
    print("🌐 Веб-сервер запущен")

# === ЗАПУСК ===
async def on_startup(dp):
    init_db()
    await web_server()
    asyncio.create_task(daily_loop())
    asyncio.create_task(moon_notify())
    asyncio.create_task(sber_signal_loop())
    try:
        await bot.send_message(MY_CHAT_ID, "🚀 Бот запущен\n📊 Аналитик | Сигналы по Сберу каждые 15 мин\n🌐 Дашборд: https://moon-bot-55tl.onrender.com/dashboard")
    except:
        pass

async def on_shutdown(dp):
    await data_fetcher.close()
    await bot.close()

if __name__ == "__main__":
    print("=" * 50)
    print("АНАЛИТИК | СБЕР СИГНАЛЫ КАЖДЫЕ 15 МИНУТ")
    print("=" * 50)
    from aiogram.utils import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
