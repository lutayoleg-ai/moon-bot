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
cache_ttl = 300

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

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS daily_summary (date TEXT PRIMARY KEY, summary TEXT)''')

def save_daily_summary(date, summary):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO daily_summary (date, summary) VALUES (?, ?)", (date, summary))

def get_last_summary_date():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("SELECT date FROM daily_summary ORDER BY date DESC LIMIT 1")
        row = c.fetchone()
    return row[0] if row else None

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
    next_full = next_new = None
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
    return "неизвестно", None, next_full

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# === КЛАВИАТУРА (только нужные кнопки) ===
keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 Фазы Луны")],
        [KeyboardButton(text="📈 Открыть позицию")],
        [KeyboardButton(text="📊 Историческая статистика")],
        [KeyboardButton(text="📈 График акции")],
    ],
    resize_keyboard=True
)

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

    async def fetch_candles(self, ticker, days=100):
        key = f"candles_{ticker}_{days}"
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
                                        d = pd.to_datetime(row[idx_date])
                                        v = float(row[idx_close])
                                        if 1 < v < 20000:
                                            records.append({'date': d, 'close': v})
                                    except:
                                        pass
                            if len(records) >= 5:
                                df = pd.DataFrame(records).sort_values('date').reset_index(drop=True)
                                set_to_cache(key, df)
                                return df
        except:
            pass
        return None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

data_fetcher = DataFetcher()

def calc_trend(df):
    if df is None or len(df) < 30:
        return "недостаточно данных"
    ma18 = df['close'].rolling(18).mean().iloc[-1]
    ma50 = df['close'].rolling(50).mean().iloc[-1] if len(df) >= 50 else ma18
    if np.isnan(ma18) or np.isnan(ma50):
        return "недостаточно данных"
    spread = abs(ma18 - ma50) / ma50 * 100
    return "боковик" if spread < 0.7 else ("бычий" if ma18 > ma50 else "медвежий")

def calc_indicators(df):
    if df is None or len(df) < 30:
        return None
    rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi().iloc[-1]
    macd = ta.trend.MACD(df['close'])
    macd_line, macd_signal = macd.macd().iloc[-1], macd.macd_signal().iloc[-1]
    return {
        'rsi': round(rsi, 1),
        'rsi_status': "перекупленность" if rsi > 70 else "перепроданность" if rsi < 30 else "нейтрально",
        'macd_status': "бычий" if macd_line > macd_signal else "медвежий"
    }

async def get_all_trends():
    results = {}
    for ticker in ALL_TICKERS:
        df = await data_fetcher.fetch_candles(ticker, 100)
        price = await data_fetcher.get_price(ticker)
        trend = calc_trend(df)
        results[ticker] = {**TICKERS[ticker], "price": price, "trend": trend}
    return results

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

# === КНОПКИ ===
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
    df = await data_fetcher.fetch_candles(ticker, 100)
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

# === СБЕР СИГНАЛЫ КАЖДЫЕ 15 МИНУТ ===
SBER_CONFIG = {
    'STOP_LOSS': 0.08,
    'TAKE_PROFIT': 0.15,
    'EXIT_TIME': "18:45"
}

current_position = {'type': None, 'entry_price': None, 'entry_time': None}

async def get_sber_signal(df, price):
    if df is None or len(df) < 50:
        return None, None
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    df['MA20'] = df['close'].rolling(20).mean()
    df['MA50'] = df['close'].rolling(50).mean()
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1] if loss.iloc[-1] != 0 else 50
    volume_ratio = 1.0
    if 'volume' in df.columns and len(df) > 20:
        vol_avg = df['volume'].rolling(20).mean().iloc[-1]
        volume_ratio = df['volume'].iloc[-1] / vol_avg if vol_avg > 0 else 1.0
    ma_cross_up = (last['MA20'] > last['MA50']) and (prev['MA20'] <= prev['MA50'])
    ma_cross_down = (last['MA20'] < last['MA50']) and (prev['MA20'] >= prev['MA50'])
    long_cond = ((price > last['MA50'] and last['MA20'] > last['MA50']) or ma_cross_up) and volume_ratio > 1.2 and 30 < rsi < 70
    short_cond = ((price < last['MA50'] and last['MA20'] < last['MA50']) or ma_cross_down) and volume_ratio > 1.2 and 30 < rsi < 70
    if long_cond:
        return "LONG", {'price': price, 'target': price * 1.15, 'stop': price * 0.92, 'rsi': round(rsi, 1)}
    if short_cond:
        return "SHORT", {'price': price, 'target': price * 0.85, 'stop': price * 1.08, 'rsi': round(rsi, 1)}
    return None, None

async def get_exit_signal(df, price, position_type):
    if df is None or len(df) < 20 or position_type is None:
        return False, None
    last = df.iloc[-1]
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1] if loss.iloc[-1] != 0 else 50
    df['MA20'] = df['close'].rolling(20).mean()
    df['MA50'] = df['close'].rolling(50).mean()
    if position_type == 'long':
        if rsi > 75 or last['MA20'] < last['MA50']:
            reasons = []
            if rsi > 75: reasons.append(f"RSI={rsi:.1f}")
            if last['MA20'] < last['MA50']: reasons.append("MA20 ниже MA50")
            return True, ", ".join(reasons)
    elif position_type == 'short':
        if rsi < 25 or last['MA20'] > last['MA50']:
            reasons = []
            if rsi < 25: reasons.append(f"RSI={rsi:.1f}")
            if last['MA20'] > last['MA50']: reasons.append("MA20 выше MA50")
            return True, ", ".join(reasons)
    return False, None

async def check_intraday_close():
    msk = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk)
    exit_time = datetime.strptime("18:45", "%H:%M").time()
    return now.time() >= exit_time

async def send_sber_signal():
    global current_position
    if not CHANNEL_ID:
        return
    df = await data_fetcher.fetch_candles("SBER", 100)
    price = await data_fetcher.get_price("SBER")
    if df is None or price is None:
        return
    signal, data = await get_sber_signal(df, price)
    exit_needed, exit_reason = await get_exit_signal(df, price, current_position['type'])
    close_intraday = await check_intraday_close()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    msg = f"📊 <b>СБЕР</b> {now.strftime('%d.%m %H:%M')}\n━━━━━━━━━━━━━━━━━━━\n💰 Цена: <b>{price:.2f} ₽</b>\n\n"
    if close_intraday and current_position.get('type'):
        msg += f"⏰ ЗАКРЫТИЕ ПОЗИЦИИ (18:45)\n"
        if current_position['type'] == 'long':
            pnl = (price - current_position['entry_price']) / current_position['entry_price'] * 100
            msg += f"💰 Результат: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n"
        current_position['type'] = None
    elif signal:
        msg += f"🟢 СИГНАЛ: {signal}\n   🎯 {data['target']:.2f} | 🛑 {data['stop']:.2f}\n   RSI: {data['rsi']}\n\n"
    else:
        msg += f"⚪ СИГНАЛ: НЕТ\n\n"
    if current_position['type']:
        pnl = (price - current_position['entry_price']) / current_position['entry_price'] * 100
        if current_position['type'] == 'short':
            pnl = -pnl
        msg += f"📌 ПОЗИЦИЯ: {current_position['type'].upper()}\n   P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n"
    if exit_needed:
        msg += f"\n🚨 ВЫХОД: {exit_reason}\n"
        current_position['type'] = None
    msg += f"\n🤖 Следующий сигнал через 15 мин"
    try:
        await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
    except Exception as e:
        print(f"Ошибка: {e}")
    if not current_position['type'] and signal and not close_intraday:
        current_position['type'] = signal.lower()
        current_position['entry_price'] = data['price']
        current_position['entry_time'] = now

async def sber_signal_loop():
    await asyncio.sleep(5)
    await send_sber_signal()
    while True:
        await asyncio.sleep(15 * 60)
        await send_sber_signal()

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
