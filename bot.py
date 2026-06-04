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

# === КОМИССИЯ БРОКЕРА ===
COMMISSION = 0.003  # 0.3%

# === ОПТИМИЗИРОВАННЫЕ ПАРАМЕТРЫ ===
STRATEGY = {
    'MA_FAST': 10,        # Быстрая MA (было 20)
    'MA_SLOW': 30,        # Медленная MA (было 50)
    'ADX_THRESHOLD': 20,  # Ниже порога — флет (было 25)
    'STOP_LOSS': 0.06,    # 6% (было 5%)
    'TAKE_PROFIT': 0.12,  # 12% (было 8%)
    'DAILY_LOSS_LIMIT': 0.03,
    'LUNAR_BLACKOUT_DAYS': 2  # Не торговать за 2 дня до/после полнолуния
}

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

# === СОСТОЯНИЕ ===
current_position = {'type': None, 'entry_price': None, 'entry_time': None, 'signal_type': None, 'is_manual': False}
last_signal_sent = {'signal': None, 'price': None, 'time': None}
daily_pnl = 0.0
last_reset_date = None

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, type TEXT, entry REAL, exit REAL, pnl REAL, commission REAL, is_manual INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_summary (date TEXT PRIMARY KEY, summary TEXT)''')

def save_trade(trade_type, entry, exit_price, pnl, commission, is_manual=False):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("INSERT INTO trades (date, type, entry, exit, pnl, commission, is_manual) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (datetime.now().isoformat(), trade_type, entry, exit_price, pnl, commission, 1 if is_manual else 0))

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

# === ЛУННЫЕ ДАННЫЕ С ФИЛЬТРОМ ===
LUNAR_PHASES = {
    "full_moons": [
        ("2026-01-03", "13:04"), ("2026-02-02", "01:10"), ("2026-03-03", "14:39"),
        ("2026-04-02", "05:13"), ("2026-05-01", "20:24"), ("2026-05-31", "11:46"),
        ("2026-06-30", "02:58"), ("2026-07-29", "17:37"), ("2026-08-28", "07:19"),
        ("2026-09-26", "19:50"), ("2026-10-26", "07:13"), ("2026-11-24", "17:55"),
        ("2026-12-24", "04:29"),
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

def is_lunar_blackout():
    """Запрет торговли за 2 дня до и 2 дня после полнолуния"""
    msk = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk).date()
    for date_str, _ in LUNAR_PHASES["full_moons"]:
        full_moon_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        days_diff = (full_moon_date - now).days
        if abs(days_diff) <= STRATEGY['LUNAR_BLACKOUT_DAYS']:
            return True, full_moon_date
    return False, None

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

# === УПРОЩЁННЫЙ РАСЧЁТ ИНДИКАТОРОВ (только MA + ADX) ===
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

def calc_trend(df):
    if df is None or len(df) < 30:
        return "недостаточно данных"
    ma10 = df['close'].rolling(STRATEGY['MA_FAST']).mean().iloc[-1]
    ma30 = df['close'].rolling(STRATEGY['MA_SLOW']).mean().iloc[-1]
    if np.isnan(ma10) or np.isnan(ma30):
        return "недостаточно данных"
    return "бычий" if ma10 > ma30 else "медвежий" if ma10 < ma30 else "боковик"

# === УПРОЩЁННЫЙ СИГНАЛ (только MA пересечения + ADX) ===
async def get_sber_signal(df, price):
    """Сигнал только на основе MA пересечений и ADX"""
    if df is None or len(df) < 50:
        return None, None
    
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    
    ma10 = df['close'].rolling(STRATEGY['MA_FAST']).mean()
    ma30 = df['close'].rolling(STRATEGY['MA_SLOW']).mean()
    
    last_ma10 = ma10.iloc[-1]
    last_ma30 = ma30.iloc[-1]
    prev_ma10 = ma10.iloc[-2] if len(ma10) > 1 else last_ma10
    prev_ma30 = ma30.iloc[-2] if len(ma30) > 1 else last_ma30
    
    adx = calculate_adx(df)
    
    # Золотое пересечение (бычий сигнал)
    golden_cross = (last_ma10 > last_ma30) and (prev_ma10 <= prev_ma30)
    # Мёртвое пересечение (медвежий сигнал)
    dead_cross = (last_ma10 < last_ma30) and (prev_ma10 >= prev_ma30)
    
    # Трендовый сигнал (уже в тренде)
    trend_long = (price > last_ma30) and (last_ma10 > last_ma30) and (adx > STRATEGY['ADX_THRESHOLD'])
    trend_short = (price < last_ma30) and (last_ma10 < last_ma30) and (adx > STRATEGY['ADX_THRESHOLD'])
    
    if golden_cross or trend_long:
        return "LONG", {
            'price': price,
            'target': price * (1 + STRATEGY['TAKE_PROFIT']),
            'stop': price * (1 - STRATEGY['STOP_LOSS']),
            'adx': round(adx, 1),
            'ma10': round(last_ma10, 2),
            'ma30': round(last_ma30, 2),
            'signal_type': "ЗОЛОТОЕ ПЕРЕСЕЧЕНИЕ" if golden_cross else "ТРЕНД"
        }
    if dead_cross or trend_short:
        return "SHORT", {
            'price': price,
            'target': price * (1 - STRATEGY['TAKE_PROFIT']),
            'stop': price * (1 + STRATEGY['STOP_LOSS']),
            'adx': round(adx, 1),
            'ma10': round(last_ma10, 2),
            'ma30': round(last_ma30, 2),
            'signal_type': "МЁРТВОЕ ПЕРЕСЕЧЕНИЕ" if dead_cross else "ТРЕНД"
        }
    
    return None, None

async def get_exit_signal(df, price, position_type):
    """Выход при обратном пересечении MA"""
    if df is None or len(df) < 20 or position_type is None:
        return False, None
    
    ma10 = df['close'].rolling(STRATEGY['MA_FAST']).mean()
    ma30 = df['close'].rolling(STRATEGY['MA_SLOW']).mean()
    
    last_ma10 = ma10.iloc[-1]
    last_ma30 = ma30.iloc[-1]
    prev_ma10 = ma10.iloc[-2] if len(ma10) > 1 else last_ma10
    prev_ma30 = ma30.iloc[-2] if len(ma30) > 1 else last_ma30
    
    if position_type == 'long':
        # Выход из лонга при мёртвом пересечении
        dead_cross = (last_ma10 < last_ma30) and (prev_ma10 >= prev_ma30)
        if dead_cross:
            return True, "MA10 ниже MA30 (мёртвое пересечение)"
    elif position_type == 'short':
        # Выход из шорта при золотом пересечении
        golden_cross = (last_ma10 > last_ma30) and (prev_ma10 <= prev_ma30)
        if golden_cross:
            return True, "MA10 выше MA30 (золотое пересечение)"
    
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
    
    await reset_daily_pnl()
    
    # Проверка лунного запрета
    lunar_blackout, full_moon_date = is_lunar_blackout()
    if lunar_blackout:
        # Не отправляем сигналы, но не спамим
        return
    
    df = await data_fetcher.fetch_candles_daily("SBER", 100)
    price = await data_fetcher.get_price("SBER")
    
    if df is None or price is None:
        return
    
    if daily_pnl < -STRATEGY['DAILY_LOSS_LIMIT']:
        if current_position['type']:
            msg = f"🚨 ДНЕВНОЙ ЛИМИТ ПРОСАДКИ ({daily_pnl*100:.1f}%)\nТорговля на сегодня остановлена"
            await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
            current_position['type'] = None
        return
    
    signal, data = await get_sber_signal(df, price)
    exit_needed, exit_reason = await get_exit_signal(df, price, current_position['type'])
    
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    signal_key = f"{signal}_{current_position['type']}"
    if signal_key == last_signal_sent.get('signal') and (now - last_signal_sent.get('time', datetime.min)).seconds < 300:
        return
    
    msg = f"📊 <b>СБЕР</b> {now.strftime('%d.%m %H:%M')}\n━━━━━━━━━━━━━━━━━━━\n💰 Цена: <b>{price:.2f} ₽</b>\n"
    
    if signal:
        msg += f"\n🟢 СИГНАЛ: {signal}\n   📊 {data['signal_type']}\n   🎯 {data['target']:.2f} | 🛑 {data['stop']:.2f}\n   ADX: {data['adx']} | MA10: {data['ma10']} | MA30: {data['ma30']}\n"
    else:
        adx = calculate_adx(df)
        msg += f"\n⚪ СИГНАЛ: НЕТ\n   ADX: {adx:.1f} (тренд {'есть' if adx > 20 else 'отсутствует'})\n"
    
    if current_position['type']:
        pnl = (price - current_position['entry_price']) / current_position['entry_price'] * 100
        if current_position['type'] == 'short':
            pnl = -pnl
        manual_mark = " (ручная)" if current_position.get('is_manual') else ""
        msg += f"\n📌 ПОЗИЦИЯ: {current_position['type'].upper()}{manual_mark}\n   P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n"
    
    if exit_needed:
        msg += f"\n🚨 ВЫХОД: {exit_reason}\n"
        pnl_final = (price - current_position['entry_price']) / current_position['entry_price'] * 100
        if current_position['type'] == 'short':
            pnl_final = -pnl_final
        commission_cost = COMMISSION * 2 * 100
        pnl_after_commission = pnl_final - commission_cost
        daily_pnl += pnl_after_commission / 100
        save_trade(current_position['type'], current_position['entry_price'], price, pnl_after_commission, commission_cost, current_position.get('is_manual', False))
        current_position['type'] = None
    
    elif not current_position['type'] and signal:
        current_position['type'] = signal.lower()
        current_position['entry_price'] = data['price']
        current_position['entry_time'] = now
        current_position['signal_type'] = data['signal_type']
        current_position['is_manual'] = False
        msg += f"\n✅ ВХОД {signal}\n"
    
    # Информация о лунном запрете
    if lunar_blackout and full_moon_date:
        days = (full_moon_date - datetime.now(pytz.timezone('Europe/Moscow')).date()).days
        msg += f"\n🌕 ЛУННЫЙ ЗАПРЕТ: {'за ' + str(abs(days)) + ' дн. до' if days > 0 else 'после'} полнолуния\n   Торговля приостановлена"
    
    msg += f"\n🤖 Следующий сигнал через 15 мин"
    
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

# === КОМАНДЫ ===
@dp.message_handler(commands=['status'])
async def status_cmd(m):
    price = await data_fetcher.get_price("SBER")
    df = await data_fetcher.fetch_candles_daily("SBER", 100)
    
    if price is None or df is None:
        await m.answer("⚠️ Нет данных")
        return
    
    ma10 = df['close'].rolling(STRATEGY['MA_FAST']).mean().iloc[-1]
    ma30 = df['close'].rolling(STRATEGY['MA_SLOW']).mean().iloc[-1]
    adx = calculate_adx(df)
    trend = "бычий 📈" if ma10 > ma30 else "медвежий 📉" if ma10 < ma30 else "боковик ⚪"
    lunar_blackout, full_moon_date = is_lunar_blackout()
    
    msg = f"📊 <b>СБЕР - СТАТУС</b>\n━━━━━━━━━━━━━━━━━━━\n💰 Цена: <b>{price:.2f} ₽</b>\n📈 Тренд: {trend}\n📊 MA10: {ma10:.2f} | MA30: {ma30:.2f}\n📈 ADX: {adx:.1f} ({'тренд есть' if adx > 20 else 'флет'})\n\n"
    
    if current_position['type']:
        pnl = (price - current_position['entry_price']) / current_position['entry_price'] * 100
        if current_position['type'] == 'short':
            pnl = -pnl
        commission_cost = COMMISSION * 2 * 100
        pnl_with_comm = pnl - commission_cost
        manual_mark = " (ручная)" if current_position.get('is_manual') else ""
        msg += f"📌 ПОЗИЦИЯ: {current_position['type'].upper()}{manual_mark}\n   💰 Вход: {current_position['entry_price']:.2f} ₽\n   📊 P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n   💸 С комиссией: {'+' if pnl_with_comm >= 0 else ''}{pnl_with_comm:.2f}%\n"
        if current_position['type'] == 'long':
            stop = current_position['entry_price'] * (1 - STRATEGY['STOP_LOSS'])
            take = current_position['entry_price'] * (1 + STRATEGY['TAKE_PROFIT'])
            msg += f"   🛑 Стоп: {stop:.2f} | 🎯 Тейк: {take:.2f}\n"
        else:
            stop = current_position['entry_price'] * (1 + STRATEGY['STOP_LOSS'])
            take = current_position['entry_price'] * (1 - STRATEGY['TAKE_PROFIT'])
            msg += f"   🛑 Стоп: {stop:.2f} | 🎯 Тейк: {take:.2f}\n"
    else:
        msg += "📌 ПОЗИЦИЯ: НЕТ\n"
    
    if lunar_blackout and full_moon_date:
        days = (full_moon_date - datetime.now(pytz.timezone('Europe/Moscow')).date()).days
        msg += f"\n🌕 ЛУННЫЙ ЗАПРЕТ: {'за ' + str(abs(days)) + ' дн. до' if days > 0 else 'после'} полнолуния\n   Новые сигналы не принимаются\n"
    
    msg += f"\n📅 Дневной P&L: {'+' if daily_pnl*100 >= 0 else ''}{daily_pnl*100:.2f}% (лимит -{STRATEGY['DAILY_LOSS_LIMIT']*100:.0f}%)"
    
    await m.answer(msg, parse_mode='HTML')

@dp.message_handler(commands=['open'])
async def open_cmd(m):
    global current_position, daily_pnl
    
    parts = m.text.split()
    if len(parts) != 3 or parts[1].upper() not in ['LONG', 'SHORT']:
        await m.answer("📝 /open LONG 310.50\nили\n📝 /open SHORT 310.50")
        return
    
    if current_position['type'] is not None:
        await m.answer(f"⚠️ Уже есть открытая позиция {current_position['type'].upper()}. Сначала закройте её /close")
        return
    
    lunar_blackout, full_moon_date = is_lunar_blackout()
    if lunar_blackout:
        await m.answer(f"🌕 ЛУННЫЙ ЗАПРЕТ: торговля приостановлена на {STRATEGY['LUNAR_BLACKOUT_DAYS']} дн. до/после полнолуния")
        return
    
    direction = parts[1].upper()
    try:
        entry_price = float(parts[2])
    except:
        await m.answer("❌ Неверная цена")
        return
    
    current_price = await data_fetcher.get_price("SBER")
    if current_price is None:
        await m.answer("⚠️ Не могу получить текущую цену")
        return
    
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    current_position['type'] = direction.lower()
    current_position['entry_price'] = entry_price
    current_position['entry_time'] = now
    current_position['signal_type'] = 'manual'
    current_position['is_manual'] = True
    
    msg = f"✅ <b>РУЧНОЕ ОТКРЫТИЕ ПОЗИЦИИ</b>\n\n📌 {direction}\n💰 Вход: {entry_price:.2f} ₽\n📅 Время: {now.strftime('%H:%M:%S')}\n\n"
    if direction == 'LONG':
        stop = entry_price * (1 - STRATEGY['STOP_LOSS'])
        take = entry_price * (1 + STRATEGY['TAKE_PROFIT'])
        msg += f"🛑 Стоп: {stop:.2f} | 🎯 Тейк: {take:.2f}"
    else:
        stop = entry_price * (1 + STRATEGY['STOP_LOSS'])
        take = entry_price * (1 - STRATEGY['TAKE_PROFIT'])
        msg += f"🛑 Стоп: {stop:.2f} | 🎯 Тейк: {take:.2f}"
    
    await m.answer(msg, parse_mode='HTML')

@dp.message_handler(commands=['close'])
async def close_cmd(m):
    global current_position, daily_pnl
    
    if current_position['type'] is None:
        await m.answer("⚠️ Нет открытой позиции")
        return
    
    price = await data_fetcher.get_price("SBER")
    if price is None:
        await m.answer("⚠️ Не могу получить текущую цену")
        return
    
    pnl = (price - current_position['entry_price']) / current_position['entry_price'] * 100
    if current_position['type'] == 'short':
        pnl = -pnl
    
    commission_cost = COMMISSION * 2 * 100
    pnl_after_commission = pnl - commission_cost
    
    msg = f"✅ <b>РУЧНОЕ ЗАКРЫТИЕ ПОЗИЦИИ</b>\n\n📌 {current_position['type'].upper()}\n💰 Вход: {current_position['entry_price']:.2f} ₽\n💰 Выход: {price:.2f} ₽\n📊 P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n💸 С комиссией: {'+' if pnl_after_commission >= 0 else ''}{pnl_after_commission:.2f}%\n"
    
    daily_pnl += pnl_after_commission / 100
    save_trade(current_position['type'], current_position['entry_price'], price, pnl_after_commission, commission_cost, current_position.get('is_manual', False))
    
    current_position['type'] = None
    
    await m.answer(msg, parse_mode='HTML')

@dp.message_handler(commands=['balance'])
async def balance_cmd(m):
    stats = get_stats()
    price = await data_fetcher.get_price("SBER")
    
    msg = f"📊 <b>СТАТИСТИКА ПО СДЕЛКАМ</b>\n━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💰 Текущая цена: {price:.2f} ₽\n\n" if price else ""
    msg += f"📈 <b>ОБЩАЯ СТАТИСТИКА</b>\n"
    msg += f"   Всего сделок: {stats['total_trades']}\n"
    msg += f"   Прибыльных: {stats['winning_trades']}\n"
    msg += f"   Убыточных: {stats['losing_trades']}\n"
    msg += f"   Win Rate: {stats['win_rate']:.1f}%\n"
    msg += f"   Общий P&L: {'+' if stats['total_pnl'] >= 0 else ''}{stats['total_pnl']:.2f}%\n"
    msg += f"   Средний P&L: {'+' if stats['avg_pnl'] >= 0 else ''}{stats['avg_pnl']:.2f}%\n\n"
    
    msg += f"📅 <b>СЕГОДНЯ</b>\n"
    msg += f"   P&L: {'+' if daily_pnl*100 >= 0 else ''}{daily_pnl*100:.2f}%\n"
    msg += f"   Лимит дня: -{STRATEGY['DAILY_LOSS_LIMIT']*100:.0f}%\n"
    
    if current_position['type']:
        pnl_current = (price - current_position['entry_price']) / current_position['entry_price'] * 100
        if current_position['type'] == 'short':
            pnl_current = -pnl_current
        commission_cost = COMMISSION * 2 * 100
        msg += f"\n📌 <b>ТЕКУЩАЯ ПОЗИЦИЯ</b>\n"
        msg += f"   {current_position['type'].upper()} | Вход: {current_position['entry_price']:.2f}\n"
        msg += f"   Текущий P&L: {'+' if pnl_current >= 0 else ''}{pnl_current:.2f}%\n"
        msg += f"   С комиссией: {'+' if pnl_current - commission_cost >= 0 else ''}{pnl_current - commission_cost:.2f}%\n"
    
    await m.answer(msg, parse_mode='HTML')

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

@dp.message_handler(commands=['start'])
async def start_cmd(m):
    await m.answer(
        "📊 **АНАЛИТИК**\n\n"
        "🔹 **СТРАТЕГИЯ:** MA10/MA30 + ADX\n"
        "🔹 **ЛУННЫЙ ФИЛЬТР:** запрет за 2 дня до/после полнолуния\n"
        "🔹 **СТОП/ТЕЙК:** 6% / 12%\n\n"
        "🔹 **КОМАНДЫ:**\n"
        "   /status — текущее состояние\n"
        "   /open LONG 310 — открыть позицию\n"
        "   /close — закрыть позицию\n"
        "   /balance — статистика\n\n"
        "🌐 Дашборд: https://moon-bot-55tl.onrender.com/dashboard",
        reply_markup=keyboard, parse_mode='Markdown')

@dp.message_handler(lambda msg: msg.text == "🌙 Фазы Луны")
async def btn_lunar(m):
    ph, dt, nxt = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    lunar_blackout, full_moon_date = is_lunar_blackout()
    txt = f"🌙 {ph.upper()}\n📅 {now.strftime('%d.%m.%Y')}\n"
    if nxt:
        txt += f"🌕 Полнолуние: {nxt.strftime('%d.%m.%Y %H:%M')}\n"
    if lunar_blackout and full_moon_date:
        days = (full_moon_date - now.date()).days
        txt += f"\n⚠️ ЛУННЫЙ ЗАПРЕТ: {'за ' + str(abs(days)) + ' дн. до' if days > 0 else 'после'} полнолуния\n   Новые сигналы не принимаются"
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
    await m.answer("📝 Используйте команду /open LONG 310.50\nили /open SHORT 310.50")

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
    if len(df) >= STRATEGY['MA_FAST']:
        plt.plot(df['date'], df['close'].rolling(STRATEGY['MA_FAST']).mean(), 'g--', label=f'MA{STRATEGY["MA_FAST"]}')
    if len(df) >= STRATEGY['MA_SLOW']:
        plt.plot(df['date'], df['close'].rolling(STRATEGY['MA_SLOW']).mean(), 'r--', label=f'MA{STRATEGY["MA_SLOW"]}')
    plt.title(TICKERS[ticker]['name'])
    plt.grid()
    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    await msg.delete()
    await m.answer_photo(buf)

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
    lunar_blackout, full_moon_date = is_lunar_blackout()
    txt = f"🌙 **{datetime.now(msk).strftime('%d.%m.%Y')}**\n"
    if nxt:
        txt += f"🌕 Полнолуние {nxt.strftime('%d.%m.%Y')}\n"
    txt += f"🟢 LONG: {long}  🔴 SHORT: {short}\n"
    if lunar_blackout:
        txt += f"⚠️ ЛУННЫЙ ЗАПРЕТ: торги приостановлены\n"
    txt += f"💡 /status /balance"
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
            if (nxt - timedelta(days=STRATEGY['LUNAR_BLACKOUT_DAYS'])).date() == now.date() and last.get('before') != nxt.date():
                last['before'] = nxt.date()
                await bot.send_message(MY_CHAT_ID, f"🌕 ЗА {STRATEGY['LUNAR_BLACKOUT_DAYS']} ДНЯ ДО ПОЛНОЛУНИЯ\nТорговля приостанавливается")
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
        await bot.send_message(MY_CHAT_ID, "🚀 Бот запущен\n📊 Аналитик | Стратегия MA10/MA30\n🌕 Лунный фильтр: запрет за 2 дня до/после полнолуния\n\n🔹 Команды:\n/status — текущее состояние\n/open LONG 310 — открыть позицию\n/close — закрыть позицию\n/balance — статистика\n\n🌐 Дашборд: https://moon-bot-55tl.onrender.com/dashboard")
    except:
        pass

async def on_shutdown(dp):
    await data_fetcher.close()
    await bot.close()

if __name__ == "__main__":
    print("=" * 50)
    print("АНАЛИТИК | УПРОЩЁННАЯ СТРАТЕГИЯ MA10/MA30")
    print("ЛУННЫЙ ФИЛЬТР | СТОП 6% | ТЕЙК 12%")
    print("КОМАНДЫ: /status, /open, /close, /balance")
    print("=" * 50)
    from aiogram.utils import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
