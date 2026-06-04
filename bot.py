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
import hashlib
from sklearn.linear_model import LinearRegression

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

def get_cache_key(prefix, *args):
    return hashlib.md5(f"{prefix}_{'_'.join(str(a) for a in args)}".encode()).hexdigest()

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

# === 17 АКТИВОВ ===
TICKERS = {
    "VTBR": {"name": "ВТБ", "p_value": 0.0009, "success_bull": 88.9, "return_bull": 5.31, "success_bear": 81.3, "return_bear": 5.35},
    "OZON": {"name": "OZON", "p_value": 0.0023, "success_bull": 88.9, "return_bull": 3.92, "success_bear": 83.3, "return_bear": 4.65},
    "SBER": {"name": "Сбер", "p_value": 0.0017, "success_bull": 83.3, "return_bull": 3.62, "success_bear": 87.5, "return_bear": 4.52},
    "MGNT": {"name": "Магнит", "p_value": 0.006, "success_bull": 87.5, "return_bull": 4.62, "success_bear": 77.8, "return_bear": 3.51},
    "GMKN": {"name": "Норникель", "p_value": 0.009, "success_bull": 88.9, "return_bull": 4.60, "success_bear": 70.0, "return_bear": 3.55},
    "NLMK": {"name": "НЛМК", "p_value": 0.017, "success_bull": 85.7, "return_bull": 4.84, "success_bear": 75.0, "return_bear": 3.91},
    "MTLR": {"name": "Мечел", "p_value": 0.034, "success_bull": 83.3, "return_bull": 5.41, "success_bear": 77.8, "return_bear": 4.55},
    "CBOM": {"name": "МКБ", "p_value": 0.017, "success_bull": 85.7, "return_bull": 4.46, "success_bear": 75.0, "return_bear": 3.65},
    "ROSN": {"name": "Роснефть", "p_value": 0.012, "success_bull": 85.7, "return_bull": 4.18, "success_bear": 77.8, "return_bear": 3.04},
    "ALRS": {"name": "Алроса", "p_value": 0.017, "success_bull": 85.7, "return_bull": 4.73, "success_bear": 75.0, "return_bear": 3.91},
    "WUSH": {"name": "Whoosh", "p_value": 0.034, "success_bull": 83.3, "return_bull": 4.86, "success_bear": 77.8, "return_bear": 3.93},
    "LKOH": {"name": "Лукойл", "p_value": 0.0046, "success_bull": 80.0, "return_bull": 2.98, "success_bear": 78.6, "return_bear": 3.43},
    "GAZP": {"name": "Газпром", "p_value": 0.0105, "success_bull": 87.5, "return_bull": 4.40, "success_bear": 76.9, "return_bear": 3.35},
    "AFLT": {"name": "Аэрофлот", "p_value": 0.012, "success_bull": 87.5, "return_bull": 4.33, "success_bear": 80.0, "return_bear": 4.58},
    "YDEX": {"name": "Яндекс", "p_value": 0.013, "success_bull": 80.0, "return_bull": 2.31, "success_bear": 81.8, "return_bear": 3.52},
    "TATN": {"name": "Татнефть", "p_value": 0.021, "success_bull": 87.5, "return_bull": 3.26, "success_bear": 72.7, "return_bear": 2.79},
    "ASTR": {"name": "Астра", "p_value": 0.045, "success_bull": 83.3, "return_bull": 3.77, "success_bear": 69.2, "return_bear": 3.12},
}
ALL_TICKERS = list(TICKERS.keys())

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS watchlist (user_id INTEGER, ticker TEXT, PRIMARY KEY (user_id, ticker))''')
        c.execute('''CREATE TABLE IF NOT EXISTS adaptive_weights (ticker TEXT PRIMARY KEY, weight REAL DEFAULT 1.0, correct_count INTEGER DEFAULT 0, total_count INTEGER DEFAULT 0, last_updated TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_summary (date TEXT PRIMARY KEY, summary TEXT)''')
        for ticker in ALL_TICKERS:
            c.execute("INSERT OR IGNORE INTO adaptive_weights (ticker) VALUES (?)", (ticker,))

def get_adaptive_weight(ticker):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("SELECT weight, correct_count, total_count FROM adaptive_weights WHERE ticker = ?", (ticker,))
        row = c.fetchone()
    return {'weight': row[0], 'correct': row[1], 'total': row[2]} if row else {'weight': 1.0, 'correct': 0, 'total': 0}

def get_watchlist(user_id):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("SELECT ticker FROM watchlist WHERE user_id = ?", (user_id,))
        return [row[0] for row in c.fetchall()]

def add_to_watchlist(user_id, ticker):
    if ticker not in ALL_TICKERS:
        return False
    try:
        with sqlite3.connect('bot_data.db') as conn:
            c = conn.cursor()
            c.execute("INSERT INTO watchlist (user_id, ticker) VALUES (?, ?)", (user_id, ticker))
        return True
    except:
        return False

def remove_from_watchlist(user_id, ticker):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker))

def clear_watchlist(user_id):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute("DELETE FROM watchlist WHERE user_id = ?", (user_id,))

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
            return "полнолуние", dt, next_full, next_new
        if (dt - now).days == 1:
            return "полнолуние_завтра", dt, next_full, next_new
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = msk.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if abs((now - dt).days) <= 1:
            return "новолуние", dt, next_full, next_new
    new_moons = [msk.localize(datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")) for d, t in LUNAR_PHASES["new_moons"]]
    last_new = max([d for d in new_moons if d <= now], default=None)
    if last_new:
        days = (now - last_new).days
        return ("растущая" if days < 14 else "убывающая"), last_new, next_full, next_new
    return "неизвестно", None, next_full, next_new

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 Фазы Луны")],
        [KeyboardButton(text="📈 Открыть позицию")],
        [KeyboardButton(text="📊 Историческая статистика")],
        [KeyboardButton(text="📋 Все активы (/all)")],
        [KeyboardButton(text="📈 График акции")],
        [KeyboardButton(text="⭐ Watchlist")],
        [KeyboardButton(text="📎 Экспорт в Excel")],
        [KeyboardButton(text="📊 Оценка риска (/risk)")],
        [KeyboardButton(text="📈 Сравнить активы (/compare)")],
        [KeyboardButton(text="🏆 Топ активов (/best)")],
        [KeyboardButton(text="🔄 Обновить данные (/refresh)")],
        [KeyboardButton(text="🔮 Прогноз (/forecast)")]
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
        key = get_cache_key('price', ticker)
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
        key = get_cache_key('candles', ticker, days)
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
        'macd_status': "бычий сигнал" if macd_line > macd_signal else "медвежий сигнал"
    }

async def get_all_trends(force=False):
    if force:
        clear_cache()
    results = {}
    for ticker in ALL_TICKERS:
        df = await data_fetcher.fetch_candles(ticker, 100)
        price = await data_fetcher.get_price(ticker)
        trend = calc_trend(df)
        ind = calc_indicators(df) if df is not None else None
        results[ticker] = {**TICKERS[ticker], "price": price, "trend": trend, "indicators": ind}
    return results

async def generate_forecast(ticker, ahead=7):
    df = await data_fetcher.fetch_candles(ticker, 100)
    if df is None or len(df) < 30:
        return None
    df['num'] = (df['date'] - df['date'].min()).dt.days
    X = df['num'].values.reshape(-1, 1)
    y = df['close'].values
    model = LinearRegression()
    model.fit(X, y)
    last = df['date'].max()
    future_dates = [last + timedelta(days=i) for i in range(1, ahead+1)]
    future_nums = [(d - df['date'].min()).days for d in future_dates]
    future_prices = model.predict(np.array(future_nums).reshape(-1, 1))
    rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi().iloc[-1]
    macd = ta.trend.MACD(df['close'])
    macd_line, macd_signal = macd.macd().iloc[-1], macd.macd_signal().iloc[-1]
    ma18 = df['close'].rolling(18).mean().iloc[-1]
    ma50 = df['close'].rolling(50).mean().iloc[-1]
    current = df['close'].iloc[-1]
    return {
        'name': TICKERS[ticker]['name'], 'current': current,
        'future_prices': future_prices, 'future_dates': future_dates,
        'pct': (future_prices[-1] - current) / current * 100,
        'rsi': round(rsi, 1),
        'rsi_sig': "перекупленность" if rsi > 70 else "перепроданность" if rsi < 30 else "нейтрально",
        'macd_sig': "бычий" if macd_line > macd_signal else "медвежий",
        'trend': "восходящий" if ma18 > ma50 else "нисходящий",
        'ma18': round(ma18, 2), 'ma50': round(ma50, 2)
    }

async def portfolio_risk(days=60):
    returns = []
    for t in ALL_TICKERS:
        df = await data_fetcher.fetch_candles(t, days+10)
        if df is not None and len(df) >= days:
            ret = df['close'].pct_change().dropna()
            if len(ret) >= days-5:
                returns.append(ret)
    if not returns:
        return None
    pr = pd.DataFrame(returns).T.mean(axis=1).dropna()
    cum = (1 + pr).cumprod()
    dd = (cum / cum.cummax() - 1) * 100
    sharpe = np.sqrt(252) * (pr.mean() - 0.16/252) / pr.std() if pr.std() > 0 else 0
    return {
        'dd': dd.min(), 'sharpe': sharpe,
        'vol': pr.std() * np.sqrt(252) * 100,
        'ret': (cum.iloc[-1] - 1) * 100,
        'days': len(pr)
    }

async def period_returns(days):
    end = datetime.now()
    start = end - timedelta(days=days)
    res = []
    for t, d in TICKERS.items():
        df = await data_fetcher.fetch_candles(t, days+20)
        if df is not None and len(df) >= 10:
            sub = df[df['date'] >= start]
            if len(sub):
                pct = (sub['close'].iloc[-1] - sub['close'].iloc[0]) / sub['close'].iloc[0] * 100
                res.append({'ticker': t, 'name': d['name'], 'return': pct})
    return sorted(res, key=lambda x: -x['return'])

async def get_volatility(ticker, days=30):
    df = await data_fetcher.fetch_candles(ticker, days+10)
    if df is None or len(df) < days:
        return None
    ret = df['close'].pct_change().dropna()
    if len(ret) < days-5:
        return None
    dv = ret.std()
    return {
        'daily': dv * 100,
        'annual': dv * np.sqrt(252) * 100,
        'dd': ((df['close'] / df['close'].cummax() - 1) * 100).min(),
        'avg': ret.mean() * 100,
        'days': len(ret)
    }

async def get_corr(t1, t2, days=60):
    df1 = await data_fetcher.fetch_candles(t1, days+10)
    df2 = await data_fetcher.fetch_candles(t2, days+10)
    if df1 is None or df2 is None:
        return None
    m = pd.merge(df1[['date', 'close']].rename(columns={'close': 'c1'}),
                 df2[['date', 'close']].rename(columns={'close': 'c2'}), on='date').dropna()
    if len(m) < 30:
        return None
    r1 = m['c1'].pct_change().dropna()
    r2 = m['c2'].pct_change().dropna()
    return {'corr': r1.corr(r2), 'days': len(m), 'p1': m['c1'].iloc[-1], 'p2': m['c2'].iloc[-1]}

# === КОМАНДЫ ===
@dp.message_handler(commands=['start'])
async def start_cmd(m):
    await m.answer(
        "🌙 **ПРОФ АНАЛИТИК** | ЭФФЕКТ ДМИТРИЕВА\n\n"
        "📊 17 акций\n\n"
        "🔹 **КОМАНДЫ:**\n"
        "   📈 Открыть позицию\n"
        "   📊 Историческая статистика\n"
        "   📋 /all\n"
        "   📈 График акции\n"
        "   ⭐ Watchlist\n"
        "   🔮 /forecast TICKER\n"
        "   📊 /risk\n"
        "   📈 /compare T1 T2\n"
        "   🏆 /best [7d|30d|90d]\n"
        "   📊 /volatility TICKER\n"
        "   📈 /correlation T1 T2\n"
        "   🔄 /refresh\n\n"
        "🌐 Дашборд: https://moon-bot-55tl.onrender.com/dashboard",
        reply_markup=keyboard, parse_mode='Markdown')

@dp.message_handler(commands=['refresh'])
async def refresh_cmd(m):
    msg = await m.answer("🔄 Очистка кэша...")
    clear_cache()
    await get_all_trends(force=True)
    phase, _, nxt, _ = get_lunar_info()
    txt = f"✅ Обновлено\n🌙 {phase}\n"
    if nxt:
        txt += f"🌕 Полнолуние: {nxt.strftime('%d.%m.%Y')}"
    await msg.delete()
    await m.answer(txt, parse_mode='Markdown')

@dp.message_handler(commands=['forecast'])
async def forecast_cmd(m):
    parts = m.text.split()
    if len(parts) != 2 or parts[1].upper() not in TICKERS:
        await m.answer("🔮 /forecast SBER")
        return
    ticker = parts[1].upper()
    msg = await m.answer(f"🔮 Прогноз {TICKERS[ticker]['name']}...")
    f = await generate_forecast(ticker)
    if f is None:
        await msg.edit_text("⚠️ Мало данных")
        return
    plt.figure(figsize=(12,5))
    df = await data_fetcher.fetch_candles(ticker, 60)
    if df is not None:
        plt.plot(df['date'], df['close'], 'b-', label='История')
    plt.plot(f['future_dates'], f['future_prices'], 'r--o', label='Прогноз 7д')
    plt.title(f"{f['name']} ({ticker})")
    plt.legend()
    plt.grid()
    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    cap = (f"💰 {f['current']:.2f}₽ → {f['future_prices'][-1]:.2f}₽ ({f['pct']:+.1f}%)\n"
           f"📊 RSI: {f['rsi']} ({f['rsi_sig']}) | MACD: {f['macd_sig']}\n"
           f"📈 Тренд: {f['trend']} | MA18: {f['ma18']} | MA50: {f['ma50']}")
    await msg.delete()
    await m.answer_photo(buf, caption=cap)

@dp.message_handler(commands=['risk'])
async def risk_cmd(m):
    msg = await m.answer("📊 Расчёт риска...")
    r = await portfolio_risk()
    if r:
        s = "✅" if r['sharpe'] > 1 else "📈" if r['sharpe'] > 0.5 else "⚠️"
        txt = (f"📉 Просадка: {r['dd']:.1f}%\n"
               f"📈 Sharpe: {r['sharpe']:.2f} {s}\n"
               f"⚡ Волатильность: {r['vol']:.1f}%\n"
               f"💰 Доходность: {r['ret']:.1f}%\n"
               f"📅 {r['days']} дн.")
        await msg.delete()
        await m.answer(txt)
    else:
        await msg.edit_text("⚠️ Нет данных")

@dp.message_handler(commands=['compare'])
async def compare_cmd(m):
    parts = m.text.split()
    if len(parts) != 3:
        await m.answer("📈 /compare SBER VTBR")
        return
    t1, t2 = parts[1].upper(), parts[2].upper()
    if t1 not in TICKERS or t2 not in TICKERS:
        await m.answer("❌ Тикер не найден")
        return
    msg = await m.answer("📈 Загрузка...")
    df1 = await data_fetcher.fetch_candles(t1, 60)
    df2 = await data_fetcher.fetch_candles(t2, 60)
    if df1 is None or df2 is None:
        await msg.edit_text("⚠️ Нет данных")
        return
    n1 = df1['close'] / df1['close'].iloc[0] * 100
    n2 = df2['close'] / df2['close'].iloc[0] * 100
    plt.figure(figsize=(12,5))
    plt.plot(df1['date'], n1, label=TICKERS[t1]['name'])
    plt.plot(df2['date'], n2, label=TICKERS[t2]['name'])
    plt.axhline(100, color='gray', ls='--')
    plt.legend()
    plt.grid()
    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    r1, r2 = n1.iloc[-1]-100, n2.iloc[-1]-100
    cap = f"{TICKERS[t1]['name']}: {'+' if r1>0 else ''}{r1:.1f}%\n{TICKERS[t2]['name']}: {'+' if r2>0 else ''}{r2:.1f}%"
    await msg.delete()
    await m.answer_photo(buf, caption=cap)

@dp.message_handler(commands=['best'])
async def best_cmd(m):
    parts = m.text.split()
    days = 30
    if len(parts) == 2:
        if parts[1] == '7d': days = 7
        elif parts[1] == '30d': days = 30
        elif parts[1] == '90d': days = 90
        else:
            await m.answer("/best 7d | 30d | 90d")
            return
    msg = await m.answer(f"🏆 За {days} дней...")
    rr = await period_returns(days)
    if not rr:
        await msg.edit_text("⚠️ Нет данных")
        return
    txt = f"🏆 **ТОП-3 за {days} дн.**\n\n🟢 ЛУЧШИЕ:\n"
    for i, r in enumerate(rr[:3]):
        e = "🥇" if i==0 else "🥈" if i==1 else "🥉"
        txt += f"{e} {r['name']}: `{r['return']:+.1f}%`\n"
    txt += f"\n🔴 ХУДШИЕ:\n"
    for r in rr[-3:]:
        txt += f"   {r['name']}: `{r['return']:+.1f}%`\n"
    await msg.delete()
    await m.answer(txt, parse_mode='Markdown')

@dp.message_handler(commands=['volatility'])
async def vol_cmd(m):
    parts = m.text.split()
    if len(parts) != 2 or parts[1].upper() not in TICKERS:
        await m.answer("📊 /volatility SBER")
        return
    ticker = parts[1].upper()
    msg = await m.answer(f"📊 Волатильность {TICKERS[ticker]['name']}...")
    v = await get_volatility(ticker, 30)
    if v:
        txt = (f"📈 Дневная: {v['daily']:.2f}%\n"
               f"📈 Годовая: {v['annual']:.2f}%\n"
               f"📉 Просадка: {v['dd']:.1f}%\n"
               f"💰 Средняя: {v['avg']:.2f}%/день")
        await msg.delete()
        await m.answer(txt)
    else:
        await msg.edit_text("⚠️ Нет данных")

@dp.message_handler(commands=['correlation'])
async def corr_cmd(m):
    parts = m.text.split()
    if len(parts) != 3:
        await m.answer("📈 /correlation SBER VTBR")
        return
    t1, t2 = parts[1].upper(), parts[2].upper()
    if t1 not in TICKERS or t2 not in TICKERS:
        await m.answer("❌ Тикер не найден")
        return
    msg = await m.answer("📈 Расчёт...")
    c = await get_corr(t1, t2)
    if c:
        if c['corr'] > 0.7: interp = "сильная положительная ✅"
        elif c['corr'] > 0.3: interp = "умеренная положительная 📈"
        elif c['corr'] > -0.3: interp = "слабая ⚪"
        elif c['corr'] > -0.7: interp = "умеренная отрицательная 📉"
        else: interp = "сильная отрицательная 🔄"
        txt = f"📈 {TICKERS[t1]['name']} vs {TICKERS[t2]['name']}\n\n🎯 {c['corr']:.3f}\n📖 {interp}"
        await msg.delete()
        await m.answer(txt)
    else:
        await msg.edit_text("⚠️ Нет данных")

@dp.message_handler(commands=['all'])
async def all_cmd(m):
    msg = await m.answer("📋 Загрузка...")
    tr = await get_all_trends()
    long = [(t, d) for t, d in tr.items() if d['trend'] == 'бычий']
    short = [(t, d) for t, d in tr.items() if d['trend'] == 'медвежий']
    side = [(t, d) for t, d in tr.items() if d['trend'] == 'боковик']
    txt = f"📋 **ВСЕ АКТИВЫ**\n\n🟢 LONG ({len(long)}):\n"
    for t, d in long[:5]:
        txt += f"   ✅ {d['name']}: +{d['return_bull']:.2f}%\n"
    if not long: txt += "   —\n"
    txt += f"\n🔴 SHORT ({len(short)}):\n"
    for t, d in short[:5]:
        txt += f"   ❌ {d['name']}: +{d['return_bear']:.2f}%\n"
    if not short: txt += "   —\n"
    txt += f"\n⚪ БОКОВИК ({len(side)}):\n"
    for t, d in side[:5]:
        txt += f"   ⚪ {d['name']}\n"
    await msg.delete()
    await m.answer(txt, parse_mode='Markdown')

@dp.message_handler(commands=['export'])
async def export_cmd(m):
    msg = await m.answer("📎 Формирую Excel...")
    tr = await get_all_trends()
    rows = []
    for t, d in tr.items():
        rows.append({
            'Тикер': t, 'Название': d['name'], 'Цена': d['price'],
            'Тренд': d['trend'], 'LONG %': d['return_bull'], 'LONG успех': d['success_bull'],
            'SHORT %': d['return_bear'], 'SHORT успех': d['success_bear']
        })
    df = pd.DataFrame(rows)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        df.to_excel(w, index=False)
    buf.seek(0)
    await msg.delete()
    await m.answer_document(types.InputFile(buf, filename=f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"))

@dp.message_handler(commands=['watchlist', 'add', 'remove', 'clear_watchlist'])
async def watchlist_dispatch(m):
    cmd = m.get_command()
    if cmd == '/watchlist':
        wl = get_watchlist(m.from_user.id)
        if wl:
            txt = "⭐ **Watchlist**\n" + "\n".join(f"• {TICKERS[t]['name']} ({t})" for t in wl)
            await m.answer(txt, parse_mode='Markdown')
        else:
            await m.answer("⭐ Пусто. /add SBER")
    elif cmd == '/add':
        parts = m.text.split()
        if len(parts) != 2 or parts[1].upper() not in TICKERS:
            await m.answer("/add SBER")
            return
        t = parts[1].upper()
        if add_to_watchlist(m.from_user.id, t):
            await m.answer(f"✅ {TICKERS[t]['name']} добавлен")
        else:
            await m.answer(f"⚠️ Уже есть")
    elif cmd == '/remove':
        parts = m.text.split()
        if len(parts) != 2:
            await m.answer("/remove SBER")
            return
        remove_from_watchlist(m.from_user.id, parts[1].upper())
        await m.answer("🗑️ Удалён")
    elif cmd == '/clear_watchlist':
        clear_watchlist(m.from_user.id)
        await m.answer("🗑️ Очищено")

# === КНОПКИ ===
@dp.message_handler(lambda msg: msg.text == "🌙 Фазы Луны")
async def btn_lunar(m):
    ph, dt, nxt, _ = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    txt = f"🌙 {ph.upper()}\n📅 {now.strftime('%d.%m.%Y')}\n"
    if nxt:
        txt += f"🌕 Полнолуние: {nxt.strftime('%d.%m.%Y %H:%M')}"
    await m.answer(txt)

@dp.message_handler(lambda msg: msg.text == "📊 Историческая статистика")
async def btn_stats(m):
    s = sorted(TICKERS.items(), key=lambda x: -x[1]['return_bull'])
    txt = "📊 **ТОП-10**\n"
    for i, (_, d) in enumerate(s[:10], 1):
        txt += f"{i}. {d['name']}: +{d['return_bull']:.2f}% ({d['success_bull']:.0f}%)\n"
    await m.answer(txt, parse_mode='Markdown')

@dp.message_handler(lambda msg: msg.text == "📈 Открыть позицию")
async def btn_open(m):
    ph, _, nxt, _ = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    if ph == "полнолуние":
        await m.answer("🌕 **ТОЧКА ВХОДА!**")
    else:
        days = (nxt - now).days if nxt else 0
        await m.answer(f"⏸ Сигнала нет\n⏳ Следующее полнолуние: {nxt.strftime('%d.%m.%Y') if nxt else '—'} (через {days} дн.)")

@dp.message_handler(lambda msg: msg.text == "📋 Все активы (/all)")
async def btn_all(m):
    await all_cmd(m)

@dp.message_handler(lambda msg: msg.text == "⭐ Watchlist")
async def btn_wl(m):
    await watchlist_dispatch(m)

@dp.message_handler(lambda msg: msg.text == "📎 Экспорт в Excel")
async def btn_exp(m):
    await export_cmd(m)

@dp.message_handler(lambda msg: msg.text == "📊 Оценка риска (/risk)")
async def btn_risk(m):
    await risk_cmd(m)

@dp.message_handler(lambda msg: msg.text == "📈 Сравнить активы (/compare)")
async def btn_cmp(m):
    await m.answer("/compare SBER VTBR")

@dp.message_handler(lambda msg: msg.text == "🏆 Топ активов (/best)")
async def btn_best(m):
    await m.answer("/best 30d")

@dp.message_handler(lambda msg: msg.text == "🔄 Обновить данные (/refresh)")
async def btn_ref(m):
    await refresh_cmd(m)

@dp.message_handler(lambda msg: msg.text == "🔮 Прогноз (/forecast)")
async def btn_fc(m):
    await m.answer("/forecast SBER")

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
    plt.figure()
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
    ph, _, nxt, _ = get_lunar_info()
    tr = await get_all_trends()
    long = sum(1 for d in tr.values() if d['trend'] == 'бычий')
    short = sum(1 for d in tr.values() if d['trend'] == 'медвежий')
    txt = f"🌙 **{datetime.now(msk).strftime('%d.%m.%Y')}**\n"
    if nxt:
        txt += f"🌕 Полнолуние {nxt.strftime('%d.%m.%Y')}\n"
    txt += f"🟢 LONG: {long}  🔴 SHORT: {short}\n💡 /forecast /risk /best"
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
        _, _, nxt, _ = get_lunar_info()
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
    'MA_FAST': 20,
    'MA_SLOW': 50,
    'RSI_OVERBOUGHT': 75,
    'RSI_OVERSOLD': 30,
    'VOLUME_RATIO_LONG': 1.5,
    'VOLUME_RATIO_SHORT': 1.5,
    'VOLUME_RATIO_SWING': 1.2,
    'STOP_LOSS_INTRADAY': 0.008,
    'TAKE_PROFIT_INTRADAY': 0.015,
    'STOP_LOSS_SWING': 0.04,
    'TAKE_PROFIT_SWING': 0.06,
    'EXIT_TIME': "18:45"
}

current_position = {'type': None, 'entry_price': None, 'entry_time': None, 'signal_type': None}

async def get_sber_intraday_signal(df, price):
    if df is None or len(df) < 20:
        return None, None
    last = df.iloc[-1]
    df['MA20_fast'] = df['close'].rolling(10).mean()
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(10).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(10).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1] if loss.iloc[-1] != 0 else 50
    ema_fast = df['close'].ewm(span=5).mean()
    ema_slow = df['close'].ewm(span=13).mean()
    macd = (ema_fast - ema_slow).iloc[-1]
    volume_ratio = 1.0
    if 'volume' in df.columns and len(df) > 10:
        vol_avg = df['volume'].rolling(10).mean().iloc[-1]
        volume_ratio = df['volume'].iloc[-1] / vol_avg if vol_avg > 0 else 1.0
    long_cond = (price > last['MA20_fast'] and volume_ratio > 1.5 and 40 < rsi < 60 and macd > 0)
    short_cond = (price < last['MA20_fast'] and volume_ratio > 1.5 and 40 < rsi < 60 and macd < 0)
    if long_cond:
        return "LONG", {'price': price, 'target': price * 1.015, 'stop': price * 0.992, 'rsi': round(rsi, 1), 'volume_ratio': round(volume_ratio, 1)}
    if short_cond:
        return "SHORT", {'price': price, 'target': price * 0.985, 'stop': price * 1.008, 'rsi': round(rsi, 1), 'volume_ratio': round(volume_ratio, 1)}
    return None, None

async def get_sber_swing_signal(df, price):
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
        return "LONG", {'price': price, 'target': price * 1.06, 'stop': price * 0.96, 'rsi': round(rsi, 1), 'ma20': round(last['MA20'], 2), 'ma50': round(last['MA50'], 2), 'volume_ratio': round(volume_ratio, 1)}
    if short_cond:
        return "SHORT", {'price': price, 'target': price * 0.94, 'stop': price * 1.04, 'rsi': round(rsi, 1), 'ma20': round(last['MA20'], 2), 'ma50': round(last['MA50'], 2), 'volume_ratio': round(volume_ratio, 1)}
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
    ema_fast = df['close'].ewm(span=8).mean()
    ema_slow = df['close'].ewm(span=17).mean()
    macd = (ema_fast - ema_slow).iloc[-1]
    df['MA20'] = df['close'].rolling(20).mean()
    df['MA50'] = df['close'].rolling(50).mean()
    if position_type == 'long':
        if rsi > 75 or macd < 0 or last['MA20'] < last['MA50']:
            reasons = []
            if rsi > 75: reasons.append(f"RSI={rsi:.1f} перекупленность")
            if macd < 0: reasons.append("MACD разворот вниз")
            if last['MA20'] < last['MA50']: reasons.append("Мёртвое пересечение MA")
            return True, ", ".join(reasons)
    elif position_type == 'short':
        if rsi < 25 or macd > 0 or last['MA20'] > last['MA50']:
            reasons = []
            if rsi < 25: reasons.append(f"RSI={rsi:.1f} перепроданность")
            if macd > 0: reasons.append("MACD разворот вверх")
            if last['MA20'] > last['MA50']: reasons.append("Золотое пересечение MA")
            return True, ", ".join(reasons)
    return False, None

async def check_intraday_close():
    msk = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk)
    exit_time = datetime.strptime("18:45", "%H:%M").time()
    return now.time() >= exit_time

async def get_sber_data_full():
    ticker = "SBER"
    df_daily = await data_fetcher.fetch_candles(ticker, 100)
    price = await data_fetcher.get_price(ticker)
    return df_daily, df_daily, price

async def send_sber_signal():
    global current_position
    if not CHANNEL_ID:
        return
    df_daily, df_hourly, price = await get_sber_data_full()
    if df_daily is None or price is None:
        return
    intra_signal, intra_data = await get_sber_intraday_signal(df_hourly, price)
    swing_signal, swing_data = await get_sber_swing_signal(df_daily, price)
    exit_needed, exit_reason = await get_exit_signal(df_daily, price, current_position['type'])
    close_intraday = await check_intraday_close()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    msg = f"📊 <b>СБЕР СИГНАЛ</b> {now.strftime('%d.%m %H:%M')}\n━━━━━━━━━━━━━━━━━━━\n💰 Цена: <b>{price:.2f} ₽</b>\n\n"
    if close_intraday and current_position.get('signal_type') == 'intraday':
        msg += f"⏰ ЗАКРЫТИЕ ПОЗИЦИИ (18:45)\n"
        if current_position['type'] == 'long':
            pnl = (price - current_position['entry_price']) / current_position['entry_price'] * 100
            msg += f"💰 Результат: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n"
        current_position['type'] = None
        current_position['signal_type'] = None
    elif intra_signal:
        msg += f"🟢 ВНУТРИДНЕВНОЙ: {intra_signal}\n   🎯 {intra_data['target']:.2f} | 🛑 {intra_data['stop']:.2f}\n   RSI: {intra_data['rsi']} | Объём: {intra_data['volume_ratio']}x\n\n"
    else:
        msg += f"⚪ ВНУТРИДНЕВНОЙ: НЕТ\n\n"
    if swing_signal:
        msg += f"🟢 СВИНГ: {swing_signal}\n   🎯 {swing_data['target']:.2f} | 🛑 {swing_data['stop']:.2f}\n   MA20: {swing_data['ma20']} | MA50: {swing_data['ma50']}\n   RSI: {swing_data['rsi']} | Объём: {swing_data['volume_ratio']}x\n\n"
    else:
        msg += f"⚪ СВИНГ: НЕТ\n\n"
    if current_position['type']:
        pnl = (price - current_position['entry_price']) / current_position['entry_price'] * 100
        if current_position['type'] == 'short':
            pnl = -pnl
        msg += f"📌 ПОЗИЦИЯ: {current_position['type'].upper()} | {current_position['signal_type']}\n   P&L: {'+' if pnl >= 0 else ''}{pnl:.2f}%\n"
    if exit_needed:
        msg += f"\n🚨 ВЫХОД ИЗ {current_position['type'].upper()}: {exit_reason}\n"
        current_position['type'] = None
        current_position['signal_type'] = None
    msg += f"\n🤖 Следующий сигнал через 15 мин"
    try:
        await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
    except Exception as e:
        print(f"Ошибка: {e}")
    if not current_position['type']:
        if intra_signal and not close_intraday:
            current_position['type'] = intra_signal.lower()
            current_position['entry_price'] = intra_data['price']
            current_position['entry_time'] = now
            current_position['signal_type'] = 'intraday'
        elif swing_signal:
            current_position['type'] = swing_signal.lower()
            current_position['entry_price'] = swing_data['price']
            current_position['entry_time'] = now
            current_position['signal_type'] = 'swing'

async def sber_signal_loop():
    await asyncio.sleep(5)
    await send_sber_signal()
    while True:
        await asyncio.sleep(15 * 60)
        await send_sber_signal()

# === WEB ДАШБОРД ===
async def dashboard(req):
    tr = await get_all_trends()
    ph, _, nxt, _ = get_lunar_info()
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
    <html><head><title>Проф Аналитик</title><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
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
    <div class="card"><h1>🌙 ПРОФ АНАЛИТИК</h1><div>{now.strftime('%d.%m.%Y %H:%M')} | {ph}</div><div>🌕 Полнолуние: {nxt.strftime('%d.%m.%Y') if nxt else '—'}</div></div>
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
        await bot.send_message(MY_CHAT_ID, "🚀 Бот запущен\n/forecast, /risk, /best, /compare\n🌐 Дашборд: https://moon-bot-55tl.onrender.com/dashboard")
    except:
        pass

async def on_shutdown(dp):
    await data_fetcher.close()
    await bot.close()

if __name__ == "__main__":
    print("=" * 50)
    print("ПРОФ АНАЛИТИК | СБЕР СИГНАЛЫ КАЖДЫЕ 15 МИНУТ")
    print("=" * 50)
    from aiogram.utils import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
