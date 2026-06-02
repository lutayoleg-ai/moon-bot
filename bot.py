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

warnings.filterwarnings('ignore')

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = 414210743
CHANNEL_ID = os.environ.get("CHANNEL_ID")

if not BOT_TOKEN:
    raise ValueError("❌ Токен не найден!")

# === КЭШ ===
data_cache = {}
cache_ttl = 300

def get_cache_key(prefix, *args):
    data = f"{prefix}_{'_'.join(str(a) for a in args)}"
    return hashlib.md5(data.encode()).hexdigest()

def get_from_cache(key):
    if key in data_cache:
        data, timestamp = data_cache[key]
        if (datetime.now() - timestamp).seconds < cache_ttl:
            return data
        else:
            del data_cache[key]
    return None

def set_to_cache(key, data):
    data_cache[key] = (data, datetime.now())

def clear_cache():
    global data_cache
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
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS watchlist (user_id INTEGER, ticker TEXT, PRIMARY KEY (user_id, ticker))''')
    c.execute('''CREATE TABLE IF NOT EXISTS adaptive_weights (ticker TEXT, weight REAL DEFAULT 1.0, correct_count INTEGER DEFAULT 0, total_count INTEGER DEFAULT 0, last_updated TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_summary (date TEXT, summary TEXT, PRIMARY KEY (date))''')
    conn.commit()
    conn.close()
    for ticker in ALL_TICKERS:
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO adaptive_weights (ticker, weight) VALUES (?, 1.0)", (ticker,))
        conn.commit()
        conn.close()

def get_adaptive_weight(ticker):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT weight, correct_count, total_count FROM adaptive_weights WHERE ticker = ?", (ticker,))
    row = c.fetchone()
    conn.close()
    return {'weight': row[0], 'correct': row[1], 'total': row[2]} if row else {'weight': 1.0, 'correct': 0, 'total': 0}

def get_watchlist(user_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT ticker FROM watchlist WHERE user_id = ?", (user_id,))
    result = [row[0] for row in c.fetchall()]
    conn.close()
    return result

def add_to_watchlist(user_id, ticker):
    if ticker not in ALL_TICKERS:
        return False
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO watchlist (user_id, ticker) VALUES (?, ?)", (user_id, ticker))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False

def remove_from_watchlist(user_id, ticker):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    conn.commit()
    conn.close()
    return True

def clear_watchlist(user_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM watchlist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def save_daily_summary(date, summary):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO daily_summary (date, summary) VALUES (?, ?)", (date, summary))
    conn.commit()
    conn.close()

def get_last_summary_date():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT date FROM daily_summary ORDER BY date DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
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
    msk_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk_tz)
    next_full = next_new = None
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        dt = msk_tz.localize(dt)
        if dt > now:
            next_full = dt
            break
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        dt = msk_tz.localize(dt)
        if dt > now:
            next_new = dt
            break
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        dt = msk_tz.localize(dt)
        if (now - dt).days <= 1 and (now - dt).days >= 0:
            return "полнолуние", dt, next_full, next_new
        if (dt - now).days == 1:
            return "полнолуние_завтра", dt, next_full, next_new
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        dt = msk_tz.localize(dt)
        if abs((now - dt).days) <= 1:
            return "новолуние", dt, next_full, next_new
    new_moons = [datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M") for d, t in LUNAR_PHASES["new_moons"]]
    new_moons = [msk_tz.localize(dt) for dt in new_moons]
    last_new = max([d for d in new_moons if d <= now], default=None)
    if last_new:
        days = (now - last_new).days
        phase = "растущая" if days < 14 else "убывающая"
        return phase, last_new, next_full, next_new
    return "неизвестно", None, next_full, next_new

# === НАСТРОЙКА ЛОГИРОВАНИЯ ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# === КЛАВИАТУРА ===
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

# === СБОР ДАННЫХ С MOEX ===
class DataFetcher:
    def __init__(self):
        self.session = None

    async def get_session(self):
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=50, ssl=ssl.create_default_context(cafile=certifi.where()))
            self.session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=15),
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        return self.session

    async def get_price(self, ticker):
        cache_key = get_cache_key('price', ticker)
        cached = get_from_cache(cache_key)
        if cached is not None:
            return cached
        
        try:
            s = await self.get_session()
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}.json"
            async with s.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    marketdata = data.get('marketdata', {}).get('data', [])
                    if marketdata:
                        cols = data.get('marketdata', {}).get('columns', [])
                        for i, col in enumerate(cols):
                            if col.lower() in ['last', 'currentprice', 'close']:
                                if len(marketdata[0]) > i and marketdata[0][i]:
                                    try:
                                        p = float(marketdata[0][i])
                                        if p > 0:
                                            set_to_cache(cache_key, p)
                                            return p
                                    except:
                                        pass
        except:
            pass
        return None

    async def fetch_candles(self, ticker, days=100):
        cache_key = get_cache_key('candles', ticker, days)
        cached = get_from_cache(cache_key)
        if cached is not None and isinstance(cached, pd.DataFrame) and len(cached) > 0:
            return cached
        
        try:
            s = await self.get_session()
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}/candles.json"
            params = {'from': start_date.strftime('%Y-%m-%d'), 'till': end_date.strftime('%Y-%m-%d'), 'interval': 24}
            async with s.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles_data = data.get('candles', {})
                    candles = candles_data.get('data', [])
                    columns = candles_data.get('columns', [])
                    if candles and len(candles) >= 3:
                        col_date = None
                        col_close = None
                        for i, col in enumerate(columns):
                            if col.lower() in ['begin', 'date']:
                                col_date = i
                            elif col.lower() in ['close', 'value']:
                                col_close = i
                        if col_date is not None and col_close is not None:
                            df_data = []
                            for candle in candles:
                                if len(candle) > max(col_date, col_close):
                                    try:
                                        date_val = candle[col_date]
                                        close_val = float(candle[col_close])
                                        if date_val and close_val > 0:
                                            df_data.append({'date': pd.to_datetime(date_val), 'close': close_val})
                                    except:
                                        pass
                            if len(df_data) >= 5:
                                df = pd.DataFrame(df_data)
                                df = df.sort_values('date').reset_index(drop=True)
                                set_to_cache(cache_key, df)
                                return df
        except:
            pass
        return None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

data_fetcher = DataFetcher()

def calc_trend(df):
    if df is None or len(df) < 50:
        return "недостаточно данных"
    close = df['close'].values
    ma18 = pd.Series(close).rolling(18).mean().values[-1]
    ma50 = pd.Series(close).rolling(50).mean().values[-1]
    if np.isnan(ma18) or np.isnan(ma50):
        return "недостаточно данных"
    spread = abs(ma18 - ma50) / ma50 * 100
    if spread < 0.7:
        return "боковик"
    return "бычий" if ma18 > ma50 else "медвежий"

def calc_indicators(df):
    if df is None or len(df) < 30:
        return None
    close = df['close']
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
    macd = ta.trend.MACD(close)
    macd_line = macd.macd().iloc[-1]
    macd_signal = macd.macd_signal().iloc[-1]
    rsi_status = "перекупленность" if rsi > 70 else "перепроданность" if rsi < 30 else "нейтрально"
    macd_status = "бычий сигнал" if macd_line > macd_signal else "медвежий сигнал"
    return {'rsi': round(rsi, 1), 'rsi_status': rsi_status, 'macd_status': macd_status}

async def get_all_trends(force_refresh=False):
    if force_refresh:
        clear_cache()
    results = {}
    for ticker in ALL_TICKERS:
        try:
            df = await data_fetcher.fetch_candles(ticker, 100)
            price = await data_fetcher.get_price(ticker)
            trend = calc_trend(df)
            indicators = calc_indicators(df) if df is not None else None
            results[ticker] = {**TICKERS[ticker], "price": price, "trend": trend, "indicators": indicators}
        except:
            results[ticker] = {**TICKERS[ticker], "price": None, "trend": "ошибка", "indicators": None}
    return results

# === ПРОГНОЗ ===
async def generate_forecast(ticker, days_ahead=7):
    df = await data_fetcher.fetch_candles(ticker, 100)
    if df is None or len(df) < 30:
        return None
    from sklearn.linear_model import LinearRegression
    df['date_num'] = (df['date'] - df['date'].min()).dt.days
    X = df['date_num'].values.reshape(-1, 1)
    y = df['close'].values
    model = LinearRegression()
    model.fit(X, y)
    last_date = df['date'].max()
    future_dates = [last_date + timedelta(days=i) for i in range(1, days_ahead + 1)]
    future_date_nums = [(d - df['date'].min()).days for d in future_dates]
    future_prices = model.predict(np.array(future_date_nums).reshape(-1, 1))
    rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi().iloc[-1]
    macd = ta.trend.MACD(df['close'])
    macd_line = macd.macd().iloc[-1]
    macd_signal = macd.macd_signal().iloc[-1]
    ma18 = df['close'].rolling(18).mean().iloc[-1]
    ma50 = df['close'].rolling(50).mean().iloc[-1]
    if rsi > 70:
        rsi_signal = "перекупленность ⚠️ (ожидается коррекция)"
    elif rsi < 30:
        rsi_signal = "перепроданность 📈 (ожидается рост)"
    else:
        rsi_signal = "нейтрально"
    macd_signal_text = "бычий 📈" if macd_line > macd_signal else "медвежий 📉"
    trend_direction = "восходящий" if ma18 > ma50 else "нисходящий"
    current_price = df['close'].iloc[-1]
    forecast_pct = (future_prices[-1] - current_price) / current_price * 100
    return {
        'ticker': ticker, 'name': TICKERS[ticker]['name'],
        'current_price': current_price, 'forecast_prices': future_prices,
        'forecast_dates': future_dates, 'forecast_pct': forecast_pct,
        'rsi': round(rsi, 1), 'rsi_signal': rsi_signal, 'macd_signal': macd_signal_text,
        'trend_direction': trend_direction, 'ma18': round(ma18, 2), 'ma50': round(ma50, 2)
    }

# === РИСК ПОРТФЕЛЯ ===
async def calculate_portfolio_risk(days=60):
    all_returns = []
    for ticker in ALL_TICKERS:
        df = await data_fetcher.fetch_candles(ticker, days+10)
        if df is not None and len(df) >= days:
            returns = df['close'].pct_change().dropna()
            if len(returns) >= days - 5:
                all_returns.append(returns)
    if not all_returns:
        return None
    portfolio_returns = pd.DataFrame(all_returns).T.mean(axis=1).dropna()
    cumulative = (1 + portfolio_returns).cumprod()
    cummax = cumulative.cummax()
    drawdown = (cumulative - cummax) / cummax * 100
    max_drawdown = drawdown.min()
    risk_free_rate = 0.16
    excess_returns = portfolio_returns - risk_free_rate / 252
    sharpe = np.sqrt(252) * excess_returns.mean() / excess_returns.std() if excess_returns.std() > 0 else 0
    volatility = portfolio_returns.std() * np.sqrt(252) * 100
    return {
        'max_drawdown': max_drawdown, 'sharpe': sharpe, 'volatility': volatility,
        'total_return': (cumulative.iloc[-1] - 1) * 100, 'days': len(portfolio_returns),
        'positive_days': (portfolio_returns > 0).sum(), 'negative_days': (portfolio_returns < 0).sum()
    }

# === ДОХОДНОСТЬ ЗА ПЕРИОД ===
async def get_returns_for_period(days):
    results = []
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    for ticker, data in TICKERS.items():
        df = await data_fetcher.fetch_candles(ticker, days+20)
        if df is not None and len(df) >= 10:
            df_start = df[df['date'] >= start_date]
            if len(df_start) > 0:
                start_price = df_start['close'].iloc[0]
                end_price = df['close'].iloc[-1]
                return_pct = (end_price - start_price) / start_price * 100
                results.append({'ticker': ticker, 'name': data['name'], 'return': return_pct})
    return sorted(results, key=lambda x: -x['return'])

# === ВОЛАТИЛЬНОСТЬ ===
async def get_volatility(ticker, days=30):
    df = await data_fetcher.fetch_candles(ticker, days+10)
    if df is None or len(df) < days:
        return None
    returns = df['close'].pct_change().dropna()
    if len(returns) < days - 5:
        return None
    daily_vol = returns.std()
    annual_vol = daily_vol * np.sqrt(252)
    cummax = df['close'].cummax()
    drawdown = (df['close'] - cummax) / cummax * 100
    max_drawdown = drawdown.min()
    return {
        'daily_vol': daily_vol * 100, 'annual_vol': annual_vol * 100,
        'max_drawdown': max_drawdown, 'avg_return': returns.mean() * 100,
        'days': len(returns)
    }

# === КОРРЕЛЯЦИЯ ===
async def get_correlation(ticker1, ticker2, days=60):
    df1 = await data_fetcher.fetch_candles(ticker1, days+10)
    df2 = await data_fetcher.fetch_candles(ticker2, days+10)
    if df1 is None or df2 is None:
        return None
    df1 = df1.set_index('date')['close']
    df2 = df2.set_index('date')['close']
    combined = pd.DataFrame({'ticker1': df1, 'ticker2': df2}).dropna()
    if len(combined) < 30:
        return None
    returns1 = combined['ticker1'].pct_change().dropna()
    returns2 = combined['ticker2'].pct_change().dropna()
    correlation = returns1.corr(returns2)
    return {'correlation': correlation, 'days': len(combined), 'price1': combined['ticker1'].iloc[-1], 'price2': combined['ticker2'].iloc[-1]}

# === КОМАНДЫ ===
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        f"🌙 **ПРОФ АНАЛИТИК** | ЭФФЕКТ ДМИТРИЕВА\n\n"
        f"📊 17 акций с подтверждённым эффектом\n\n"
        f"🔹 **ОСНОВНЫЕ КОМАНДЫ:**\n"
        f"   📈 Открыть позицию — анализ с RSI/MACD\n"
        f"   📊 Историческая статистика — успешность\n"
        f"   📋 /all — сводная таблица\n"
        f"   📈 График акции — цена + RSI\n"
        f"   ⭐ Watchlist — персональный список\n\n"
        f"🔹 **НОВЫЕ КОМАНДЫ:**\n"
        f"   🔮 /forecast TICKER — прогноз на 7 дней\n"
        f"   📊 /risk — оценка риска портфеля\n"
        f"   📈 /compare T1 T2 — сравнение активов\n"
        f"   🏆 /best [7d|30d|90d] — топ активов\n"
        f"   📊 /volatility TICKER — волатильность\n"
        f"   📈 /correlation T1 T2 — корреляция\n"
        f"   🔄 /refresh — обновить данные\n\n"
        f"🌐 Web-дашборд: https://moon-bot-55tl.onrender.com/dashboard\n\n"
        f"📖 По методике: полнолуние → точка входа\n"
        f"💡 Бот учится на ошибках!",
        reply_markup=keyboard, parse_mode='Markdown')

@dp.message_handler(commands=['refresh'])
async def cmd_refresh(message: types.Message):
    msg = await message.answer("🔄 Очищаю кэш и обновляю данные...")
    clear_cache()
    await get_all_trends(force_refresh=True)
    phase, phase_date, next_full, _ = get_lunar_info()
    text = f"✅ **Данные обновлены!**\n\n🗑️ Кэш очищен\n🌙 Текущая фаза: {phase}\n"
    if next_full:
        text += f"🌕 Следующее полнолуние: {next_full.strftime('%d.%m.%Y')}\n"
    text += f"\n💡 Используйте /forecast TICKER для прогноза"
    await msg.delete()
    await message.answer(text, parse_mode='Markdown')

@dp.message_handler(commands=['forecast'])
async def cmd_forecast(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("🔮 Использование: /forecast TICKER\nПример: /forecast SBER")
        return
    ticker = parts[1].upper()
    if ticker not in TICKERS:
        await message.answer(f"❌ Тикер {ticker} не найден")
        return
    msg = await message.answer(f"🔮 Генерирую прогноз для {TICKERS[ticker]['name']}...")
    try:
        forecast = await generate_forecast(ticker, 7)
        if forecast is None:
            await msg.edit_text(f"⚠️ Недостаточно данных для прогноза")
            return
        plt.figure(figsize=(12, 6))
        df = await data_fetcher.fetch_candles(ticker, 60)
        if df is not None:
            plt.plot(df['date'], df['close'], 'b-', linewidth=2, label='История')
        plt.plot(forecast['forecast_dates'], forecast['forecast_prices'], 'r--', linewidth=2, marker='o', label='Прогноз 7 дней')
        plt.title(f"Прогноз {forecast['name']} ({ticker})")
        plt.xlabel("Дата")
        plt.ylabel("Цена, ₽")
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.xticks(rotation=45)
        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()
        text = f"🔮 **ПРОГНОЗ {forecast['name']} ({ticker})**\n\n"
        text += f"💰 Текущая цена: {forecast['current_price']:.2f}₽\n"
        text += f"📈 Прогноз через 7 дней: {forecast['forecast_prices'][-1]:.2f}₽ ({forecast['forecast_pct']:+.1f}%)\n\n"
        text += f"📊 **Индикаторы:**\n"
        text += f"   • RSI: {forecast['rsi']} ({forecast['rsi_signal']})\n"
        text += f"   • MACD: {forecast['macd_signal']}\n"
        text += f"   • Тренд: {forecast['trend_direction']}\n"
        text += f"   • MA18: {forecast['ma18']}₽ | MA50: {forecast['ma50']}₽"
        await msg.delete()
        await message.answer_photo(photo=buf, caption=text, parse_mode='Markdown')
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

@dp.message_handler(commands=['risk'])
async def cmd_risk(message: types.Message):
    msg = await message.answer("📊 Рассчитываю риск портфеля...")
    risk = await calculate_portfolio_risk()
    if risk:
        sharpe_comment = "Отличный ✅" if risk['sharpe'] > 1 else "Хороший 📈" if risk['sharpe'] > 0.5 else "Умеренный ⚪" if risk['sharpe'] > 0 else "Низкий ⚠️"
        text = f"📊 **ОЦЕНКА РИСКА**\n\n📉 Просадка: {risk['max_drawdown']:.2f}%\n📈 Sharpe: {risk['sharpe']:.2f} ({sharpe_comment})\n⚡ Волатильность: {risk['volatility']:.2f}%\n💰 Доходность: {risk['total_return']:.2f}%\n📅 Период: {risk['days']} дн.\n🟢 {risk['positive_days']} | 🔴 {risk['negative_days']}"
        await msg.delete()
        await message.answer(text, parse_mode='Markdown')
    else:
        await msg.edit_text("⚠️ Недостаточно данных")

@dp.message_handler(commands=['compare'])
async def cmd_compare(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("📈 Использование: /compare TICKER1 TICKER2\nПример: /compare SBER VTBR")
        return
    ticker1, ticker2 = parts[1].upper(), parts[2].upper()
    if ticker1 not in TICKERS or ticker2 not in TICKERS:
        await message.answer("❌ Тикер не найден")
        return
    msg = await message.answer("📈 Загружаю график...")
    df1 = await data_fetcher.fetch_candles(ticker1, 60)
    df2 = await data_fetcher.fetch_candles(ticker2, 60)
    if df1 is None or df2 is None:
        await msg.edit_text("⚠️ Недостаточно данных")
        return
    norm1 = df1['close'] / df1['close'].iloc[0] * 100
    norm2 = df2['close'] / df2['close'].iloc[0] * 100
    plt.figure(figsize=(12, 6))
    plt.plot(df1['date'], norm1, 'b-', linewidth=2, label=f"{TICKERS[ticker1]['name']}")
    plt.plot(df2['date'], norm2, 'r-', linewidth=2, label=f"{TICKERS[ticker2]['name']}")
    plt.axhline(y=100, color='gray', linestyle='--')
    plt.title("Сравнение активов")
    plt.xlabel("Дата")
    plt.ylabel("Нормированная цена (100)")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    ret1 = norm1.iloc[-1] - 100
    ret2 = norm2.iloc[-1] - 100
    caption = f"📈 {TICKERS[ticker1]['name']}: {'🟢' if ret1>0 else '🔴'} {ret1:+.2f}%\n📈 {TICKERS[ticker2]['name']}: {'🟢' if ret2>0 else '🔴'} {ret2:+.2f}%"
    await msg.delete()
    await message.answer_photo(photo=buf, caption=caption)

@dp.message_handler(commands=['best'])
async def cmd_best(message: types.Message):
    parts = message.text.split()
    period = 30
    if len(parts) == 2:
        p = parts[1].lower()
        if p == '7d': period = 7
        elif p == '30d': period = 30
        elif p == '90d': period = 90
        else:
            await message.answer("Использование: /best [7d|30d|90d]")
            return
    msg = await message.answer(f"🏆 Топ активов за {period} дней...")
    returns = await get_returns_for_period(period)
    if not returns:
        await msg.edit_text("⚠️ Недостаточно данных")
        return
    text = f"🏆 **ТОП-3 за {period} дней**\n\n🟢 **ЛУЧШИЕ:**\n"
    for i, r in enumerate(returns[:3], 1):
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
        text += f"{emoji} {r['name']}: `{r['return']:+.2f}%`\n"
    text += f"\n🔴 **ХУДШИЕ:**\n"
    for r in returns[-3:]:
        text += f"   {r['name']}: `{r['return']:+.2f}%`\n"
    avg_ret = np.mean([r['return'] for r in returns])
    text += f"\n📊 Средняя: `{avg_ret:+.2f}%`"
    await msg.delete()
    await message.answer(text, parse_mode='Markdown')

@dp.message_handler(commands=['volatility'])
async def cmd_volatility(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("📊 Использование: /volatility TICKER\nПример: /volatility SBER")
        return
    ticker = parts[1].upper()
    if ticker not in TICKERS:
        await message.answer("❌ Тикер не найден")
        return
    msg = await message.answer(f"📊 Волатильность {TICKERS[ticker]['name']}...")
    vol = await get_volatility(ticker, 30)
    if vol:
        text = f"📊 **Волатильность {TICKERS[ticker]['name']}**\n\n📈 Дневная: {vol['daily_vol']:.2f}%\n📈 Годовая: {vol['annual_vol']:.2f}%\n📉 Просадка: {vol['max_drawdown']:.2f}%\n💰 Доходность: {vol['avg_return']:.3f}%/день"
        await msg.delete()
        await message.answer(text, parse_mode='Markdown')
    else:
        await msg.edit_text("⚠️ Недостаточно данных")

@dp.message_handler(commands=['correlation'])
async def cmd_correlation(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("📈 Использование: /correlation T1 T2\nПример: /correlation SBER VTBR")
        return
    t1, t2 = parts[1].upper(), parts[2].upper()
    if t1 not in TICKERS or t2 not in TICKERS:
        await message.answer("❌ Тикер не найден")
        return
    msg = await message.answer("📈 Расчёт корреляции...")
    corr = await get_correlation(t1, t2, 60)
    if corr:
        if corr['correlation'] > 0.7: interp = "сильная положительная ✅"
        elif corr['correlation'] > 0.3: interp = "умеренная положительная 📈"
        elif corr['correlation'] > -0.3: interp = "слабая ⚪"
        elif corr['correlation'] > -0.7: interp = "умеренная отрицательная 📉"
        else: interp = "сильная отрицательная 🔄"
        text = f"📈 **Корреляция**\n\n{TICKERS[t1]['name']} vs {TICKERS[t2]['name']}\n\n🎯 Коэффициент: {corr['correlation']:.3f}\n📖 {interp}\n📅 Период: {corr['days']} дн."
        await msg.delete()
        await message.answer(text, parse_mode='Markdown')
    else:
        await msg.edit_text("⚠️ Недостаточно данных")

@dp.message_handler(commands=['all'])
async def cmd_all(message: types.Message):
    msg = await message.answer("📋 Собираю данные...")
    trends = await get_all_trends()
    text = f"📋 **ВСЕ АКТИВЫ**\n\n"
    long_list = [(t, d) for t, d in trends.items() if d['trend'] == "бычий"]
    short_list = [(t, d) for t, d in trends.items() if d['trend'] == "медвежий"]
    side_list = [(t, d) for t, d in trends.items() if d['trend'] == "боковик"]
    text += f"🟢 **LONG ({len(long_list)}):**\n"
    for t, d in long_list[:5]:
        text += f"   ✅ {d['name']}: +{d['return_bull']:.2f}%\n"
    if not long_list: text += "   ⚠️ Нет\n"
    text += f"\n🔴 **SHORT ({len(short_list)}):**\n"
    for t, d in short_list[:5]:
        text += f"   ❌ {d['name']}: +{d['return_bear']:.2f}%\n"
    if not short_list: text += "   ⚠️ Нет\n"
    text += f"\n⚪ **БОКОВИК ({len(side_list)}):**\n"
    for t, d in side_list[:5]:
        text += f"   ⚪ {d['name']}\n"
    await msg.delete()
    await message.answer(text, parse_mode='Markdown')

@dp.message_handler(commands=['export'])
async def cmd_export(message: types.Message):
    msg = await message.answer("📎 Формирую Excel...")
    trends = await get_all_trends()
    data = []
    for ticker, info in trends.items():
        adaptive = get_adaptive_weight(ticker)
        data.append({
            'Тикер': ticker, 'Название': info['name'], 'Цена': info['price'],
            'Тренд': info['trend'], 'LONG %': info['return_bull'], 'LONG успех %': info['success_bull'],
            'SHORT %': info['return_bear'], 'SHORT успех %': info['success_bear'], 'Вес': adaptive['weight']
        })
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Активы', index=False)
    output.seek(0)
    await msg.delete()
    await message.answer_document(types.InputFile(output, filename=f'moon_bot_report_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx'), caption="📎 Статистика")

@dp.message_handler(commands=['watchlist'])
async def cmd_watchlist(message: types.Message):
    wl = get_watchlist(message.from_user.id)
    if not wl:
        await message.answer("⭐ Watchlist пуст\n/add TICKER — добавить\n/remove TICKER — удалить\n/clear_watchlist — очистить")
        return
    text = f"⭐ **Watchlist**\n"
    for t in wl:
        text += f"• {TICKERS[t]['name']} ({t})\n"
    await message.answer(text, parse_mode='Markdown')

@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /add TICKER\nПример: /add SBER")
        return
    ticker = parts[1].upper()
    if ticker not in TICKERS:
        await message.answer("❌ Тикер не найден")
        return
    if add_to_watchlist(message.from_user.id, ticker):
        await message.answer(f"✅ {TICKERS[ticker]['name']} добавлен")
    else:
        await message.answer(f"⚠️ Уже в списке")

@dp.message_handler(commands=['remove'])
async def cmd_remove(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /remove TICKER")
        return
    ticker = parts[1].upper()
    remove_from_watchlist(message.from_user.id, ticker)
    await message.answer(f"🗑️ {ticker} удалён")

@dp.message_handler(commands=['clear_watchlist'])
async def cmd_clear_watchlist(message: types.Message):
    clear_watchlist(message.from_user.id)
    await message.answer("🗑️ Watchlist очищен")

@dp.message_handler(lambda message: message.text == "🌙 Фазы Луны")
async def lunar_phases_cmd(message: types.Message):
    phase, phase_date, next_full, next_new = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    text = f"🌙 **ЛУННЫЙ КАЛЕНДАРЬ**\n\n📅 Сегодня: {now.strftime('%d.%m.%Y')}\n🌙 Фаза: {phase.upper()}\n"
    if phase_date:
        text += f"📆 Дата фазы: {phase_date.strftime('%d.%m.%Y %H:%M')}\n"
    text += f"\n🎯 **События:**\n"
    if next_full: text += f"🌕 Полнолуние: {next_full.strftime('%d.%m.%Y %H:%M')}\n"
    if next_new: text += f"🌑 Новолуние: {next_new.strftime('%d.%m.%Y %H:%M')}"
    await message.answer(text, parse_mode='Markdown')

@dp.message_handler(lambda message: message.text == "📊 Историческая статистика")
async def stats_cmd(message: types.Message):
    text = f"📊 **ИСТОРИЧЕСКАЯ СТАТИСТИКА**\n\n"
    sorted_by_return = sorted(TICKERS.items(), key=lambda x: -x[1]['return_bull'])
    for i, (ticker, data) in enumerate(sorted_by_return[:10], 1):
        text += f"{i}. {data['name']}: +{data['return_bull']:.2f}% (успех {data['success_bull']:.0f}%)\n"
    await message.answer(text, parse_mode='Markdown')

@dp.message_handler(lambda message: message.text == "📈 Открыть позицию")
async def open_position_cmd(message: types.Message):
    phase, phase_date, next_full, _ = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    text = f"🎯 **РЕКОМЕНДАЦИИ**\n\n"
    if phase == "полнолуние":
        text += f"🌕 СЕГОДНЯ ПОЛНОЛУНИЕ — ТОЧКА ВХОДА!\n"
        trends = await get_all_trends()
        for ticker, data in trends.items():
            if data['trend'] == "бычий":
                text += f"✅ {data['name']}: ПОКУПКА\n"
            elif data['trend'] == "медвежий":
                text += f"❌ {data['name']}: ПРОДАЖА\n"
    else:
        text += f"⏸ Активный сигнал отсутствует\n"
        if next_full:
            days = (next_full - now).days
            text += f"⏳ Следующая точка входа: {next_full.strftime('%d.%m.%Y')} (через {days} дн.)\n"
    text += f"\n⚠️ СТОП-ЛОСС ОБЯЗАТЕЛЕН!"
    await message.answer(text, parse_mode='Markdown')

@dp.message_handler(lambda message: message.text == "📋 Все активы (/all)")
async def all_button_handler(message: types.Message):
    await cmd_all(message)

@dp.message_handler(lambda message: message.text == "⭐ Watchlist")
async def watchlist_button_handler(message: types.Message):
    await cmd_watchlist(message)

@dp.message_handler(lambda message: message.text == "📎 Экспорт в Excel")
async def export_button_handler(message: types.Message):
    await cmd_export(message)

@dp.message_handler(lambda message: message.text == "📊 Оценка риска (/risk)")
async def risk_button_handler(message: types.Message):
    await cmd_risk(message)

@dp.message_handler(lambda message: message.text == "📈 Сравнить активы (/compare)")
async def compare_button_handler(message: types.Message):
    await message.answer("📈 /compare SBER VTBR")

@dp.message_handler(lambda message: message.text == "🏆 Топ активов (/best)")
async def best_button_handler(message: types.Message):
    await message.answer("🏆 /best 30d (7d, 30d, 90d)")

@dp.message_handler(lambda message: message.text == "🔄 Обновить данные (/refresh)")
async def refresh_button_handler(message: types.Message):
    await cmd_refresh(message)

@dp.message_handler(lambda message: message.text == "🔮 Прогноз (/forecast)")
async def forecast_button_handler(message: types.Message):
    await message.answer("🔮 /forecast SBER")

@dp.message_handler(lambda message: message.text == "📈 График акции")
async def ask_ticker_for_chart(message: types.Message):
    await message.answer("📊 Введите тикер (SBER, VTBR, GAZP...)")

@dp.message_handler(lambda message: message.text.upper() in ALL_TICKERS)
async def send_chart(message: types.Message):
    ticker = message.text.upper()
    msg = await message.answer(f"📈 График {TICKERS[ticker]['name']}...")
    df = await data_fetcher.fetch_candles(ticker, 100)
    if df is None:
        await msg.edit_text("⚠️ Нет данных")
        return
    plt.figure(figsize=(12, 6))
    plt.plot(df['date'], df['close'], 'b-', linewidth=2, label='Цена')
    if len(df) >= 18:
        ma18 = df['close'].rolling(18).mean()
        plt.plot(df['date'], ma18, 'g--', linewidth=1.5, label='MA18')
    if len(df) >= 50:
        ma50 = df['close'].rolling(50).mean()
        plt.plot(df['date'], ma50, 'r--', linewidth=1.5, label='MA50')
    plt.title(f"{TICKERS[ticker]['name']} ({ticker})")
    plt.xlabel("Дата")
    plt.ylabel("Цена, ₽")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    indicators = calc_indicators(df)
    caption = f"📈 {TICKERS[ticker]['name']} ({ticker})"
    if indicators:
        caption += f"\nRSI: {indicators['rsi']} ({indicators['rsi_status']}) | MACD: {indicators['macd_status']}"
    await msg.delete()
    await message.answer_photo(photo=buf, caption=caption)

# === ЕЖЕДНЕВНАЯ СВОДКА ===
async def send_daily_summary():
    if not CHANNEL_ID:
        return
    msk_tz = pytz.timezone('Europe/Moscow')
    today = datetime.now(msk_tz).strftime('%Y-%m-%d')
    if get_last_summary_date() == today:
        return
    phase, phase_date, next_full, _ = get_lunar_info()
    trends = await get_all_trends()
    text = f"🌙 **ЕЖЕДНЕВНАЯ СВОДКА**\n📅 {datetime.now(msk_tz).strftime('%d.%m.%Y')}\n\n"
    if next_full:
        days_left = (next_full - datetime.now(msk_tz)).days
        text += f"🌕 Полнолуние через {days_left} дн. ({next_full.strftime('%d.%m.%Y')})\n\n"
    long_list = [d for d in trends.values() if d['trend'] == "бычий"]
    short_list = [d for d in trends.values() if d['trend'] == "медвежий"]
    text += f"🟢 LONG: {len(long_list)} | 🔴 SHORT: {len(short_list)}\n\n"
    text += f"💡 /forecast, /risk, /best"
    save_daily_summary(today, text)
    try:
        await bot.send_message(CHANNEL_ID, text, parse_mode='Markdown')
    except:
        pass

async def daily_summary_task():
    while True:
        try:
            msk_tz = pytz.timezone('Europe/Moscow')
            now = datetime.now(msk_tz)
            if now.hour == 10 and now.minute < 5:
                await send_daily_summary()
            await asyncio.sleep(60)
        except:
            await asyncio.sleep(60)

# === УВЕДОМЛЕНИЯ ===
async def check_full_moon_notification():
    msk_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk_tz)
    if not hasattr(check_full_moon_notification, 'last_notify'):
        check_full_moon_notification.last_notify = {}
    phase, phase_date, next_full, _ = get_lunar_info()
    if next_full:
        one_day_before = next_full - timedelta(days=1)
        if one_day_before.date() == now.date():
            key = f"before_{next_full.date()}"
            if check_full_moon_notification.last_notify.get(key) != now.date():
                check_full_moon_notification.last_notify[key] = now.date()
                await bot.send_message(MY_CHAT_ID, f"🌕 **НАПОМИНАНИЕ!**\n\nЗавтра ПОЛНОЛУНИЕ ({next_full.strftime('%d.%m.%Y')}) — точка входа!")
        if next_full.date() == now.date():
            key = f"day_{next_full.date()}"
            if check_full_moon_notification.last_notify.get(key) != now.date():
                check_full_moon_notification.last_notify[key] = now.date()
                await bot.send_message(MY_CHAT_ID, f"🌕 **СЕГОДНЯ ПОЛНОЛУНИЕ!**\n\nТОЧКА ВХОДА!")

async def periodic_notification():
    while True:
        try:
            await check_full_moon_notification()
        except:
            pass
        await asyncio.sleep(3600)

# === WEB ДАШБОРД ===
async def handle_dashboard(request):
    trends = await get_all_trends()
    phase, phase_date, next_full, next_new = get_lunar_info()
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Проф Аналитик</title><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        *{{margin:0;padding:0;box-sizing:border-box;}}
        body{{font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);color:#eee;padding:20px;min-height:100vh;}}
        h1{{text-align:center;margin-bottom:10px;color:#f0c040;}}
        .container{{max-width:1400px;margin:0 auto;}}
        .lunar-card, .stat-card{{background:rgba(255,255,255,0.1);border-radius:15px;padding:20px;margin-bottom:20px;backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.2);}}
        .stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:20px;margin-bottom:20px;}}
        .stat-value{{font-size:2.5em;font-weight:bold;color:#f0c040;}}
        table{{width:100%;border-collapse:collapse;background:rgba(255,255,255,0.1);border-radius:15px;overflow:hidden;}}
        th,td{{padding:12px;text-align:left;border-bottom:1px solid rgba(255,255,255,0.2);}}
        th{{background:rgba(240,192,64,0.3);color:#f0c040;}}
        .bull{{color:#4ade80;}}.bear{{color:#f87171;}}.neutral{{color:#facc15;}}
        .refresh-btn{{background:#f0c040;color:#1a1a2e;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:bold;margin-bottom:20px;}}
        .footer{{text-align:center;margin-top:30px;padding:20px;color:#666;font-size:0.8em;}}
    </style>
    </head>
    <body>
    <div class="container">
        <h1>🌙 ПРОФ АНАЛИТИК</h1>
        <div style="text-align:center;margin-bottom:20px;">Эффект Дмитриева — анализ 17 акций</div>
        <button class="refresh-btn" onclick="location.reload()">🔄 Обновить</button>
        <div class="lunar-card">
            <h3>🌙 Лунный календарь</h3>
            <p>📅 {now.strftime('%d.%m.%Y %H:%M')}</p>
            <p>🌙 Фаза: <strong>{phase}</strong></p>
            <p>🌕 Полнолуние: {next_full.strftime('%d.%m.%Y %H:%M') if next_full else '—'}</p>
            <p>🌑 Новолуние: {next_new.strftime('%d.%m.%Y %H:%M') if next_new else '—'}</p>
        </div>
        <div class="stats-grid">
            <div class="stat-card"><h3>📈 LONG</h3><div class="stat-value">{len([t for t,d in trends.items() if d['trend'] == 'бычий'])}</div><div>активов</div></div>
            <div class="stat-card"><h3>📉 SHORT</h3><div class="stat-value">{len([t for t,d in trends.items() if d['trend'] == 'медвежий'])}</div><div>активов</div></div>
            <div class="stat-card"><h3>⚪ БОКОВИК</h3><div class="stat-value">{len([t for t,d in trends.items() if d['trend'] == 'боковик'])}</div><div>активов</div></div>
        </div>
        <div style="overflow-x:auto;">
        <table>
            <thead><tr><th>Актив</th><th>Тикер</th><th>Цена</th><th>Тренд</th><th>LONG доход</th><th>SHORT доход</th></tr></thead>
            <tbody>"""
    for ticker, data in trends.items():
        price_str = f"{data['price']:.2f}" if data['price'] else "—"
        trend_class = "bull" if data['trend'] == "бычий" else "bear" if data['trend'] == "медвежий" else "neutral"
        trend_symbol = "🟢" if data['trend'] == "бычий" else "🔴" if data['trend'] == "медвежий" else "⚪"
        html += f"<tr><td>{data['name']}</td><td>{ticker}</td><td>{price_str}₽</td><td class='{trend_class}'>{trend_symbol} {data['trend']}</td><td class='bull'>+{data['return_bull']:.2f}%</td><td class='bear'>+{data['return_bear']:.2f}%</td></tr>"
    html += f"""
            </tbody>
        </table>
        </div>
        <div class="footer">📊 Данные обновляются каждые 5 минут | {now.strftime('%d.%m.%Y %H:%M:%S')}</div>
    </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def handle_health(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/health', handle_health)
    app.router.add_get('/dashboard', handle_dashboard)
    app.router.add_get('/', handle_dashboard)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    print("🌐 Веб-сервер запущен")

async def on_startup(dp):
    init_db()
    await start_web_server()
    asyncio.create_task(periodic_notification())
    asyncio.create_task(daily_summary_task())
    try:
        await bot.send_message(MY_CHAT_ID, "🚀 **Бот запущен!**\n\n🔮 /forecast TICKER — прогноз\n📊 /risk — риск портфеля\n📈 /compare — сравнение\n🏆 /best — топ активов\n🌐 Дашборд: https://moon-bot-55tl.onrender.com/dashboard", parse_mode='Markdown')
    except:
        pass

async def on_shutdown(dp):
    await data_fetcher.close()
    await bot.close()

if __name__ == "__main__":
    print("=" * 50)
    print("ПРОФ АНАЛИТИК | ЭФФЕКТ ДМИТРИЕВА")
    print("=" * 50)
    from aiogram.utils import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
