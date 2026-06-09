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

warnings.filterwarnings('ignore')

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
    'DAILY_LOSS_LIMIT': 0.03
}

COMMISSION = 0.003

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
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if abs((now - dt).days) <= 1:
            return "новолуние", dt, next_full
    new_moons = [msk.localize(datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")) for d, t in LUNAR_PHASES["new_moons"]]
    last_new = max([d for d in new_moons if d <= now], default=None)
    if last_new:
        days = (now - last_new).days
        return ("растущая" if days < 14 else "убывающая"), last_new, next_full
    return "обычный день", None, next_full

def get_days_until_full_moon():
    msk = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk)
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if dt > now:
            return (dt - now).days
    return None

# === КЭШ ===
data_cache = {}
cache_ttl = 60

def get_from_cache(key):
    if key in data_cache:
        data, ts = data_cache[key]
        if (datetime.now() - ts).seconds < cache_ttl:
            return data
        del data_cache[key]
    return None

def set_to_cache(key, data):
    data_cache[key] = (data, datetime.now())

# === СОСТОЯНИЕ ПОЗИЦИЙ ===
positions = {}
last_signal_sent = {}
daily_pnl = 0.0
last_reset_date = None
lunar_notified_days = set()

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, ticker TEXT, type TEXT, entry REAL, exit REAL, pnl REAL, commission REAL, is_manual INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_summary (date TEXT PRIMARY KEY, summary TEXT)''')

def save_trade(ticker, trade_type, entry, exit_price, pnl, commission, is_manual=False):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("INSERT INTO trades (date, ticker, type, entry, exit, pnl, commission, is_manual) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (datetime.now().isoformat(), ticker, trade_type, entry, exit_price, pnl, commission, 1 if is_manual else 0))

def get_stats():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*), SUM(pnl), AVG(pnl), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) FROM trades")
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
                                set_to_cache(key, df)
                                return df
        except Exception as e:
            print(f"Ошибка: {e}")
        return None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

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

# === АНАЛИЗ СИГНАЛА ===
async def get_signal_for_ticker(ticker):
    df = await data_fetcher.fetch_candles_daily(ticker, 100)
    price = await data_fetcher.get_price(ticker)
    
    if df is None or price is None:
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
        return "LONG", {
            'ticker': ticker,
            'name': TICKERS[ticker]['name'],
            'price': price,
            'trend': trend,
            'adx': round(adx, 1),
            'target': price * (1 + STRATEGY['TAKE_PROFIT']),
            'stop': price * (1 - STRATEGY['STOP_LOSS']),
            'signal_type': "ЗОЛОТОЕ ПЕРЕСЕЧЕНИЕ" if golden_cross else "ТРЕНД",
            'ma10': last_ma10,
            'ma30': last_ma30
        }, None
    
    if trend == "bearish" and (adx > STRATEGY['ADX_THRESHOLD'] or dead_cross):
        return "SHORT", {
            'ticker': ticker,
            'name': TICKERS[ticker]['name'],
            'price': price,
            'trend': trend,
            'adx': round(adx, 1),
            'target': price * (1 - STRATEGY['TAKE_PROFIT']),
            'stop': price * (1 + STRATEGY['STOP_LOSS']),
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

# === ДЛЯ СБЕРА (подробный мониторинг) ===
async def get_sber_signal_detailed():
    signal, data, explanation = await get_signal_for_ticker("SBER")
    if data is None:
        return None, None, "Нет данных"
    if signal:
        return signal, data, None
    else:
        reasons = []
        if data.get('adx', 0) < STRATEGY['ADX_THRESHOLD']:
            reasons.append(f"⚠️ ADX = {data['adx']:.1f} (нужно > {STRATEGY['ADX_THRESHOLD']}) — рынок во флете")
        if data.get('trend') == 'bearish':
            reasons.append(f"📉 Тренд медвежий (MA10 ниже MA30) — для LONG нужен бычий тренд")
        elif data.get('trend') == 'bullish':
            reasons.append(f"📈 Тренд бычий (MA10 выше MA30) — для SHORT нужен медвежий тренд")
        if not reasons:
            reasons.append("Условия для входа не выполнены")
        return None, data, "\n".join(reasons)

# === ОТПРАВКА СИГНАЛОВ ПО ВСЕМ АКТИВАМ (со сворачивающимся списком) ===
async def check_and_send_all_signals():
    global last_signal_sent
    
    signals = []
    for ticker in ALL_TICKERS:
        if ticker == "SBER":
            continue
        if ticker in positions and positions[ticker].get('type') is not None:
            continue
        signal, data, _ = await get_signal_for_ticker(ticker)
        if signal and data:
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
    
    msg = f"🔔🔔🔔 <b>НАЙДЕНЫ СИГНАЛЫ</b> 🔔🔔🔔\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"⏰ {now.strftime('%H:%M')} | Найдено {len(signals)} сигналов\n\n"
    
    msg += f"📊 <b>САМЫЕ СИЛЬНЫЕ (ТОП-3):</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    top_count = min(3, len(signals))
    for i in range(top_count):
        s = signals[i]
        data = s['data']
        emoji = "🟢" if s['signal'] == 'LONG' else "🔴"
        direction = "LONG" if s['signal'] == 'LONG' else "SHORT"
        msg += f"{emoji} <b>{data['name']} ({s['ticker']})</b> | {direction} | ADX {data['adx']}\n"
        msg += f"   💡 /open {s['ticker']} {s['signal']} {data['price']:.2f}\n\n"
    
    if len(signals) > top_count:
        msg += f"<details>\n<summary>📋 Остальные {len(signals) - top_count} сигналов (нажмите, чтобы раскрыть)</summary>\n\n"
        for i in range(top_count, len(signals)):
            s = signals[i]
            data = s['data']
            emoji = "🟢" if s['signal'] == 'LONG' else "🔴"
            direction = "LONG" if s['signal'] == 'LONG' else "SHORT"
            msg += f"{emoji} <b>{data['name']} ({s['ticker']})</b> | {direction} | ADX {data['adx']}\n"
            msg += f"   💡 /open {s['ticker']} {s['signal']} {data['price']:.2f}\n\n"
        msg += f"</details>\n"
    
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🤖 Сигналы сгенерированы в {now.strftime('%H:%M')}"
    
    try:
        await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
        for s in signals:
            last_signal_sent[s['ticker']] = f"{s['signal']}_{int(s['data']['price'])}"
    except Exception as e:
        print(f"Ошибка отправки: {e}")

# === ОТПРАВКА СИГНАЛА ПО СБЕРУ (КАЖДЫЙ ЧАС) ===
async def send_sber_hourly():
    global positions, daily_pnl
    
    if not CHANNEL_ID:
        return
    
    await reset_daily_pnl()
    
    signal, data, explanation = await get_sber_signal_detailed()
    if data is None:
        return
    
    price = data['price']
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    
    trend_ru = "БЫЧИЙ 🟢" if data.get('trend') == 'bullish' else "МЕДВЕЖИЙ 🔴" if data.get('trend') == 'bearish' else "НЕЙТРАЛЬНО ⚪"
    
    exit_needed = False
    exit_reason = None
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
        msg = f"""
🔔🔔🔔 <b>СБЕР — СИГНАЛ К {signal} !!!</b> 🔔🔔🔔

━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 Цена: <b>{price:.2f} ₽</b>
📈 Тренд: {trend_ru}
📊 ADX: {data['adx']}
📊 MA10: {data['ma10']:.2f} | MA30: {data['ma30']:.2f}

🎯 <b>ПЛАН СДЕЛКИ:</b>
   Вход: {price:.2f} ₽
   🛑 Стоп: {data['stop']:.2f} (-{STRATEGY['STOP_LOSS']*100:.0f}%)
   🎯 Тейк: {data['target']:.2f} (+{STRATEGY['TAKE_PROFIT']*100:.0f}%)

📊 Тип сигнала: {data['signal_type']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━

🤖 Сигнал сгенерирован в {now.strftime('%H:%M')}
"""
    else:
        msg = f"""
📊 <b>СБЕР - МОНИТОРИНГ</b> {now.strftime('%H:%M')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 Цена: <b>{price:.2f} ₽</b>
📈 Тренд: {trend_ru}
📊 ADX: {data['adx']:.1f}
📊 MA10: {data['ma10']:.2f} | MA30: {data['ma30']:.2f}

❌ <b>СИГНАЛА НЕТ</b>

📋 <b>ПРИЧИНА:</b>
{explanation if explanation else 'Условия для входа не выполнены'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Следующая проверка через час
"""
    
    if sber_position:
        pnl = (price - sber_entry) / sber_entry * 100 if sber_position == 'long' else (sber_entry - price) / sber_entry * 100
        msg += f"\n\n📌 ПОЗИЦИЯ ПО СБЕРУ: {sber_position.upper()}\n   P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%"
    
    if exit_needed:
        msg += f"\n\n🚨 <b>ВЫХОД ИЗ ПОЗИЦИИ ПО СБЕРУ</b>\n{exit_reason}"
        pnl_final = (price - sber_entry) / sber_entry * 100 if sber_position == 'long' else (sber_entry - price) / sber_entry * 100
        commission_cost = COMMISSION * 2 * 100
        pnl_after = pnl_final - commission_cost
        daily_pnl += pnl_after / 100
        save_trade("SBER", sber_position, sber_entry, price, pnl_after, commission_cost, positions["SBER"].get('is_manual', False))
        positions["SBER"] = {'type': None, 'entry_price': None, 'entry_time': None, 'is_manual': False}
    elif signal and not sber_position:
        positions["SBER"] = {
            'type': signal.lower(),
            'entry_price': price,
            'entry_time': now,
            'is_manual': False
        }
        msg += f"\n\n✅ <b>ВХОД {signal}</b> по сигналу бота"
    
    try:
        await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
    except Exception as e:
        print(f"Ошибка отправки: {e}")

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
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
    text = "📋 <b>ДОСТУПНЫЕ ТИКЕРЫ</b> (17 активов)\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, (ticker, info) in enumerate(TICKERS.items(), 1):
        text += f"{i}. <b>{info['name']}</b> ({ticker})\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "💡 <b>Как использовать:</b>\n"
    text += "   /open SBER LONG 310.50 — открыть LONG\n"
    text += "   /open GAZP SHORT 180.20 — открыть SHORT\n"
    text += "   /close — закрыть позицию\n"
    text += "   /status — состояние Сбера\n"
    text += "   /balance — общая статистика"
    return text

# === ЛУННАЯ СТРАТЕГИЯ ===
async def lunar_notify():
    global lunar_notified_days
    while True:
        days_until = get_days_until_full_moon()
        if days_until is not None and days_until <= 3 and days_until not in lunar_notified_days:
            lunar_notified_days.add(days_until)
            if days_until == 3:
                await bot.send_message(MY_CHAT_ID, f"🌕 ЧЕРЕЗ 3 ДНЯ ПОЛНОЛУНИЕ\nГотовьтесь к точке входа")
            elif days_until == 2:
                await bot.send_message(MY_CHAT_ID, f"🌕 ЧЕРЕЗ 2 ДНЯ ПОЛНОЛУНИЕ")
            elif days_until == 1:
                await bot.send_message(MY_CHAT_ID, f"🌕 ЗАВТРА ПОЛНОЛУНИЕ — ТОЧКА ВХОДА")
        await asyncio.sleep(3600)

async def daily_lunar_summary():
    if not CHANNEL_ID:
        return
    msk = pytz.timezone('Europe/Moscow')
    today = datetime.now(msk).strftime('%Y-%m-%d')
    if get_last_summary_date() == today:
        return
    ph, _, nxt = get_lunar_info()
    trends = {}
    for ticker in ALL_TICKERS:
        df = await data_fetcher.fetch_candles_daily(ticker, 100)
        trend = calc_trend_for_ticker(df)
        trends[ticker] = trend
    long_cnt = sum(1 for t in trends.values() if t == 'бычий')
    short_cnt = sum(1 for t in trends.values() if t == 'медвежий')
    txt = f"🌙 **{datetime.now(msk).strftime('%d.%m.%Y')}**\n"
    if nxt:
        txt += f"🌕 Полнолуние {nxt.strftime('%d.%m.%Y')}\n"
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
        await asyncio.sleep(60)

# === ЦИКЛЫ СИГНАЛОВ ===
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
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 Фазы Луны"), KeyboardButton(text="📈 Открыть позицию")],
        [KeyboardButton(text="📊 Историческая статистика"), KeyboardButton(text="📈 График акции")],
        [KeyboardButton(text="📋 Тикеры")],
    ],
    resize_keyboard=True
)

# === КОМАНДЫ ===
@dp.message_handler(commands=['start'])
async def start_cmd(m):
    await m.answer(
        "📊 <b>АНАЛИТИК</b>\n\n"
        "🔹 <b>СБЕР (сигналы каждый час с 10:00 до 22:00)</b>\n"
        "   Стратегия: MA10/MA30 + ADX | Стоп 6% | Тейк 12%\n\n"
        "🔹 <b>ОСТАЛЬНЫЕ 16 АКТИВОВ</b>\n"
        "   Проверяются каждый час, ТОП-3 видны сразу, остальные под спойлером\n\n"
        "🔹 <b>ЛУННАЯ СТРАТЕГИЯ</b>\n"
        "   Ежедневная сводка в 10:00 | Уведомление за 3 дня до полнолуния\n\n"
        "🔹 <b>КОМАНДЫ:</b>\n"
        "   /status — состояние по Сберу\n"
        "   /open SBER LONG 310 — открыть сделку\n"
        "   /close — закрыть позицию\n"
        "   /balance — статистика\n"
        "   /tickers — список всех тикеров\n\n"
        "🌐 Дашборд: https://moon-bot-55tl.onrender.com/dashboard",
        reply_markup=keyboard, parse_mode='HTML')

@dp.message_handler(commands=['tickers'])
async def tickers_cmd(m):
    await m.answer(get_tickers_list_text(), parse_mode='HTML')

@dp.message_handler(commands=['status'])
async def status_cmd(m):
    price = await data_fetcher.get_price("SBER")
    df = await data_fetcher.fetch_candles_daily("SBER", 100)
    if price is None or df is None:
        await m.answer("⚠️ Нет данных")
        return
    trend = get_trend(df)
    adx = calculate_adx(df)
    ma10 = df['close'].rolling(10).mean().iloc[-1]
    ma30 = df['close'].rolling(30).mean().iloc[-1]
    trend_ru = "БЫЧИЙ 🟢" if trend == "bullish" else "МЕДВЕЖИЙ 🔴" if trend == "bearish" else "БОКОВИК ⚪"
    msg = f"📊 <b>СБЕР - СТАТУС</b>\n━━━━━━━━━━━━━━━━━━━\n💰 Цена: <b>{price:.2f} ₽</b>\n"
    msg += f"📈 Тренд: {trend_ru}\n📊 MA10: {ma10:.2f} | MA30: {ma30:.2f}\n📈 ADX: {adx:.1f}\n"
    sber_pos = positions.get("SBER", {}).get('type')
    sber_entry = positions.get("SBER", {}).get('entry_price') if sber_pos else None
    if sber_pos and sber_entry:
        pnl = (price - sber_entry) / sber_entry * 100 if sber_pos == 'long' else (sber_entry - price) / sber_entry * 100
        commission_cost = COMMISSION * 2 * 100
        msg += f"\n📌 ПОЗИЦИЯ: {sber_pos.upper()}\n   Вход: {sber_entry:.2f} ₽\n   P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n   С комиссией: {'+' if pnl - commission_cost >= 0 else ''}{pnl - commission_cost:.2f}%\n"
    else:
        msg += f"\n📌 ПОЗИЦИЯ: НЕТ\n"
    msg += f"\n📅 Дневной P&L: {'+' if daily_pnl*100 >= 0 else ''}{daily_pnl*100:.2f}%"
    await m.answer(msg, parse_mode='HTML')

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
    if not price:
        await m.answer("⚠️ Нет цены")
        return
    if active_pos['type'] == 'long':
        pnl = (price - active_pos['entry_price']) / active_pos['entry_price'] * 100
    else:
        pnl = (active_pos['entry_price'] - price) / active_pos['entry_price'] * 100
    commission_cost = COMMISSION * 2 * 100
    pnl_after = pnl - commission_cost
    daily_pnl += pnl_after / 100
    save_trade(active_ticker, active_pos['type'], active_pos['entry_price'], price, pnl_after, commission_cost, active_pos.get('is_manual', False))
    msg = f"✅ Закрыто {active_pos['type'].upper()} по {TICKERS[active_ticker]['name']} ({active_ticker})\n💰 Вход: {active_pos['entry_price']:.2f}\n💰 Выход: {price:.2f}\n📊 P&L: {pnl:+.2f}%\n💸 С комиссией: {pnl_after:+.2f}%"
    positions[active_ticker] = {'type': None, 'entry_price': None, 'entry_time': None, 'is_manual': False}
    await m.answer(msg)

@dp.message_handler(commands=['balance'])
async def balance_cmd(m):
    stats = get_stats()
    price = await data_fetcher.get_price("SBER")
    msg = f"📊 <b>СТАТИСТИКА ПО СДЕЛКАМ</b>\n━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💰 Цена Сбера: {price:.2f} ₽\n\n" if price else ""
    msg += f"📈 <b>ОБЩАЯ</b>\n   Всего сделок: {stats['total_trades']}\n"
    msg += f"   Прибыльных: {stats['winning_trades']}\n   Убыточных: {stats['losing_trades']}\n"
    msg += f"   Win Rate: {stats['win_rate']:.1f}%\n   Общий P&L: {stats['total_pnl']:+.2f}%\n"
    msg += f"   Средний P&L: {stats['avg_pnl']:+.2f}%\n\n📅 <b>СЕГОДНЯ</b>\n   P&L: {daily_pnl*100:+.2f}%"
    await m.answer(msg, parse_mode='HTML')

# === КНОПКИ ===
@dp.message_handler(lambda msg: msg.text == "🌙 Фазы Луны")
async def btn_lunar(m):
    ph, dt, nxt = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    days = get_days_until_full_moon()
    txt = f"🌙 {ph.upper()}\n📅 {now.strftime('%d.%m.%Y')}"
    if nxt:
        txt += f"\n🌕 Полнолуние: {nxt.strftime('%d.%m.%Y %H:%M')}"
    if days is not None:
        txt += f"\n⏳ До полнолуния: {days} дн."
    await m.answer(txt)

@dp.message_handler(lambda msg: msg.text == "📈 Открыть позицию")
async def btn_open_position(m):
    ph, _, _ = get_lunar_info()
    if ph == "полнолуние":
        await m.answer("🌕 **ТОЧКА ВХОДА!**\n📝 Используйте /open SBER LONG ЦЕНА\nили /open GAZP SHORT ЦЕНА")
    else:
        days = get_days_until_full_moon()
        await m.answer(f"⏸ Лунного сигнала нет\n⏳ Полнолуние через {days} дн.\n📝 Для ручного входа: /open SBER LONG 310")

@dp.message_handler(lambda msg: msg.text == "📊 Историческая статистика")
async def btn_stats(m):
    s = sorted(TICKERS.items(), key=lambda x: -x[1]['return_bull'])
    txt = "📊 **ТОП-10 по доходности LONG**\n"
    for i, (t, d) in enumerate(s[:10], 1):
        txt += f"{i}. {d['name']} ({t}): +{d['return_bull']:.2f}%\n"
    await m.answer(txt, parse_mode='Markdown')

@dp.message_handler(lambda msg: msg.text == "📈 График акции")
async def btn_chart(m):
    await m.answer("Введите тикер из списка:\n" + ", ".join(ALL_TICKERS))

@dp.message_handler(lambda msg: msg.text == "📋 Тикеры")
async def btn_tickers(m):
    await m.answer(get_tickers_list_text(), parse_mode='HTML')

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
    if len(df) >= 18:
        plt.plot(df['date'], df['close'].rolling(18).mean(), 'g--', label='MA18')
    if len(df) >= 50:
        plt.plot(df['date'], df['close'].rolling(50).mean(), 'r--', label='MA50')
    plt.title(TICKERS[ticker]['name'])
    plt.grid()
    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    await msg.delete()
    await m.answer_photo(buf)

# === ВЕБ-ДАШБОРД ===
async def dashboard(req):
    tr = await get_all_trends()
    ph, _, nxt = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    long_count = sum(1 for d in tr.values() if d['trend'] == 'бычий')
    short_count = sum(1 for d in tr.values() if d['trend'] == 'медвежий')
    side_count = sum(1 for d in tr.values() if d['trend'] == 'боковик')
    total_count = long_count + short_count + side_count
    short_percent = round((short_count / total_count) * 100, 1) if total_count else 0
    long_percent = round((long_count / total_count) * 100, 1) if total_count else 0
    sentiment_color = "#f87171" if short_count > long_count else "#4ade80" if long_count > short_count else "#facc15"
    
    rows = ""
    tickers_names = []
    long_returns = []
    short_returns = []
    for ticker, data in tr.items():
        price = f"{data['price']:.2f}" if data['price'] else "—"
        if data['trend'] == 'бычий':
            trend_class = "trend-bull"
            trend_text = "🟢 БЫЧИЙ"
        elif data['trend'] == 'медвежий':
            trend_class = "trend-bear"
            trend_text = "🔴 МЕДВЕЖИЙ"
        else:
            trend_class = "trend-neutral"
            trend_text = "⚪ БОКОВИК"
        rows += f"<tr><td style='font-weight:500;'>{data['name']}</td><td><b>{ticker}</b></td><td><b>{price}</b> ₽</td><td class='{trend_class}'>{trend_text}</td><td class='bull'>+{data['return_bull']:.2f}%</td><td class='bear'>+{data['return_bear']:.2f}%</td></tr>"
        tickers_names.append(data['name'])
        long_returns.append(data['return_bull'])
        short_returns.append(data['return_bear'])
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>АНАЛИТИК | ПРОФАНАЛИТИК</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #0a0c15; font-family: 'Inter', system-ui, sans-serif; padding: 24px; color: #e2e8f0; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border-radius: 28px; padding: 28px 32px; margin-bottom: 32px; border: 1px solid #334155; }}
        .header h1 {{ font-size: 2rem; font-weight: 700; background: linear-gradient(135deg, #f0f9ff, #bae6fd); -webkit-background-clip: text; background-clip: text; color: transparent; }}
        .lunar-info {{ display: flex; gap: 20px; flex-wrap: wrap; margin-top: 16px; color: #94a3b8; }}
        .lunar-badge {{ background: #1e293b; padding: 6px 14px; border-radius: 40px; font-size: 0.85rem; border-left: 3px solid #facc15; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 20px; margin-bottom: 32px; }}
        .stat-card {{ background: #111827; border-radius: 24px; padding: 20px; text-align: center; border: 1px solid #2d3a4e; transition: 0.2s; }}
        .stat-card:hover {{ transform: translateY(-3px); border-color: #4f5b73; }}
        .stat-value {{ font-size: 2.5rem; font-weight: 800; }}
        .stat-label {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 1px; color: #94a3b8; margin-top: 10px; }}
        .bull {{ color: #4ade80; }} .bear {{ color: #f87171; }} .neutral {{ color: #facc15; }}
        .charts-row {{ display: flex; flex-wrap: wrap; gap: 24px; margin-bottom: 32px; }}
        .chart-box {{ flex: 1; min-width: 280px; background: #0f172a; border-radius: 24px; padding: 20px; border: 1px solid #2d3a4e; }}
        .chart-box h3 {{ font-size: 1.1rem; margin-bottom: 16px; color: #cbd5e1; }}
        canvas {{ max-height: 260px; width: 100%; }}
        .table-wrapper {{ overflow-x: auto; border-radius: 24px; background: #0f172a; border: 1px solid #2d3a4e; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
        th {{ background: #1e293b; padding: 14px 10px; text-align: left; color: #cbd5e6; border-bottom: 1px solid #334155; }}
        td {{ padding: 12px 10px; border-bottom: 1px solid #1e293b; }}
        tr:hover td {{ background-color: rgba(30, 41, 59, 0.5); }}
        .trend-bull {{ color: #4ade80; font-weight: 600; }} .trend-bear {{ color: #f87171; font-weight: 600; }} .trend-neutral {{ color: #facc15; font-weight: 600; }}
        .footer-note {{ margin-top: 28px; text-align: center; font-size: 0.75rem; color: #5b6e8c; border-top: 1px solid #1e293b; padding-top: 20px; }}
        @media (max-width: 700px) {{ body {{ padding: 16px; }} .stat-value {{ font-size: 1.8rem; }} th, td {{ font-size: 0.7rem; padding: 6px 4px; }} }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📊 АНАЛИТИК</h1>
        <div class="lunar-info">
            <span>🗓️ {now.strftime('%d.%m.%Y %H:%M')}</span>
            <span class="lunar-badge">🌙 {ph.upper()}</span>
            <span class="lunar-badge">🌕 Полнолуние: {nxt.strftime('%d.%m.%Y') if nxt else '—'}</span>
        </div>
    </div>
    <div class="stats-grid">
        <div class="stat-card"><div class="stat-value bull">{long_count}</div><div class="stat-label">🟢 LONG</div></div>
        <div class="stat-card"><div class="stat-value bear">{short_count}</div><div class="stat-label">🔴 SHORT</div></div>
        <div class="stat-card"><div class="stat-value neutral">{side_count}</div><div class="stat-label">⚪ БОКОВИК</div></div>
        <div class="stat-card"><div class="stat-value" style="color: {sentiment_color};">{short_percent}%</div><div class="stat-label">📉 ПРЕОБЛАДАНИЕ SHORT</div></div>
        <div class="stat-card"><div class="stat-value" style="color: #60a5fa;">{long_percent}%</div><div class="stat-label">📈 % БЫЧЬИХ</div></div>
        <div class="stat-card"><div class="stat-value" style="color: #c084fc;">{total_count}</div><div class="stat-label">🏷️ ВСЕГО АКТИВОВ</div></div>
    </div>
    <div class="charts-row">
        <div class="chart-box"><h3>📊 РАСПРЕДЕЛЕНИЕ ТРЕНДОВ</h3><canvas id="trendPieChart"></canvas></div>
        <div class="chart-box"><h3>📊 ПОТЕНЦИАЛЬНАЯ ДОХОДНОСТЬ (ТОП-10)</h3><canvas id="returnBarChart"></canvas></div>
    </div>
    <div class="table-wrapper">
        <table>
            <thead><tr><th>Актив</th><th>Тикер</th><th>💰 Цена</th><th>📈 Тренд</th><th>🚀 LONG %</th><th>📉 SHORT %</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </div>
    <div class="footer-note">🤖 Сбер: сигналы каждый час | Остальные: ТОП-3 видны сразу, остальные под спойлером</div>
</div>
<script>
    new Chart(document.getElementById('trendPieChart'), {{
        type: 'doughnut',
        data: {{ labels: ['LONG', 'SHORT', 'БОКОВИК'], datasets: [{{ data: [{long_count}, {short_count}, {side_count}], backgroundColor: ['#4ade80', '#f87171', '#facc15'], borderWidth: 0 }}] }},
        options: {{ responsive: true, maintainAspectRatio: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#cbd5e6' }} }} }} }}
    }});
    new Chart(document.getElementById('returnBarChart'), {{
        type: 'bar',
        data: {{ labels: {tickers_names[:10]}, datasets: [
            {{ label: 'LONG потенциал (%)', data: {long_returns[:10]}, backgroundColor: '#4ade8066', borderColor: '#4ade80', borderWidth: 1 }},
            {{ label: 'SHORT потенциал (%)', data: {short_returns[:10]}, backgroundColor: '#f8717166', borderColor: '#f87171', borderWidth: 1 }}
        ] }},
        options: {{ responsive: true, maintainAspectRatio: true, plugins: {{ legend: {{ position: 'top', labels: {{ color: '#cbd5e6' }} }} }}, scales: {{ y: {{ grid: {{ color: '#2d3a4e' }}, ticks: {{ color: '#cbd5e6' }} }}, x: {{ ticks: {{ color: '#cbd5e6', rotation: 35, autoSkip: true }} }} }} }}
    }});
</script>
</body>
</html>
    """
    return web.Response(text=html, content_type='text/html')

# === ЗАПУСК ===
async def on_startup(dp):
    init_db()
    await web_server()
    asyncio.create_task(daily_loop())
    asyncio.create_task(lunar_notify())
    asyncio.create_task(sber_hourly_loop())
    asyncio.create_task(all_signals_check_loop())
    try:
        await bot.send_message(MY_CHAT_ID, "🚀 Бот запущен\n\n🔹 СБЕР: сигналы КАЖДЫЙ ЧАС с 10:00 до 22:00\n🔹 ОСТАЛЬНЫЕ 16: ТОП-3 видны сразу, остальные под спойлером\n🔹 ЛУНА: сводка в 10:00, уведомления за 3 дня\n\n📋 /tickers — список всех активов\n/open SBER LONG 310 — открыть сделку\n/close — закрыть\n/status — состояние Сбера\n/balance — статистика")
    except:
        pass

async def on_shutdown(dp):
    await data_fetcher.close()
    await bot.close()

async def web_server():
    app = web.Application()
    app.router.add_get('/health', lambda req: web.Response(text="OK"))
    app.router.add_get('/dashboard', dashboard)
    app.router.add_get('/', dashboard)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 10000).start()
    print("🌐 Веб-сервер запущен")

if __name__ == "__main__":
    print("=" * 50)
    print("АНАЛИТИК | СИГНАЛЫ ПО ВСЕМ 17 АКТИВАМ")
    print("Сбер: каждый час | Остальные: ТОП-3 видно, остальные под спойлером")
    print("Стоп 6% | Тейк 12% | ADX > 20")
    print("=" * 50)
    from aiogram.utils import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
