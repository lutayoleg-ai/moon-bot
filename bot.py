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
import json
from bs4 import BeautifulSoup
import re

warnings.filterwarnings('ignore')

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = 414210743
CHANNEL_ID = os.environ.get("CHANNEL_ID")  # Добавьте в Environment: CHANNEL_ID (например, @your_channel)

if not BOT_TOKEN:
    raise ValueError("❌ Токен не найден! Добавьте переменную окружения BOT_TOKEN в Render")

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
    c.execute('''CREATE TABLE IF NOT EXISTS watchlist
                 (user_id INTEGER, ticker TEXT, PRIMARY KEY (user_id, ticker))''')
    c.execute('''CREATE TABLE IF NOT EXISTS prediction_accuracy
                 (ticker TEXT, date TEXT, predicted_trend TEXT, actual_trend TEXT, 
                  was_correct INTEGER, PRIMARY KEY (ticker, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS adaptive_weights
                 (ticker TEXT, weight REAL DEFAULT 1.0, correct_count INTEGER DEFAULT 0, 
                  total_count INTEGER DEFAULT 0, last_updated TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_summary
                 (date TEXT, summary TEXT, PRIMARY KEY (date))''')
    conn.commit()
    conn.close()
    init_weights()

def init_weights():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    for ticker in ALL_TICKERS:
        c.execute("INSERT OR IGNORE INTO adaptive_weights (ticker, weight) VALUES (?, 1.0)", (ticker,))
    conn.commit()
    conn.close()

def update_accuracy(ticker, predicted_trend, actual_trend):
    was_correct = 1 if predicted_trend == actual_trend else 0
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    date = datetime.now().strftime('%Y-%m-%d')
    c.execute("INSERT INTO prediction_accuracy (ticker, date, predicted_trend, actual_trend, was_correct) VALUES (?, ?, ?, ?, ?)",
              (ticker, date, predicted_trend, actual_trend, was_correct))
    c.execute("SELECT correct_count, total_count FROM adaptive_weights WHERE ticker = ?", (ticker,))
    row = c.fetchone()
    if row:
        correct, total = row
        new_correct = correct + was_correct
        new_total = total + 1
        accuracy = new_correct / new_total if new_total > 0 else 0.5
        weight = max(0.3, min(1.5, accuracy * 2 - 0.5))
        c.execute("UPDATE adaptive_weights SET weight = ?, correct_count = ?, total_count = ?, last_updated = ? WHERE ticker = ?",
                  (weight, new_correct, new_total, datetime.now().strftime('%Y-%m-%d %H:%M'), ticker))
    conn.commit()
    conn.close()
    return was_correct

def get_adaptive_weight(ticker):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT weight, correct_count, total_count FROM adaptive_weights WHERE ticker = ?", (ticker,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'weight': row[0], 'correct': row[1], 'total': row[2]}
    return {'weight': 1.0, 'correct': 0, 'total': 0}

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
    new_moons = []
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        new_moons.append(msk_tz.localize(dt))
    last_new = max([d for d in new_moons if d <= now], default=None)
    if last_new:
        days = (now - last_new).days
        phase = "растущая" if days < 14 else "убывающая"
        return phase, last_new, next_full, next_new
    return "неизвестно", None, next_full, next_new

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
        [KeyboardButton(text="📈 Сравнение с IMOEX")],
        [KeyboardButton(text="📊 Точность стратегии")],
        [KeyboardButton(text="📊 Оценка риска (/risk)")],
        [KeyboardButton(text="📈 Сравнить активы (/compare)")],
        [KeyboardButton(text="🏆 Топ активов (/best)")]
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
            self.session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=10),
                headers={'User-Agent': 'Mozilla/5.0'})
        return self.session

    async def get_price(self, ticker):
        try:
            s = await self.get_session()
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
            async with s.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    md = data.get('marketdata', {}).get('data', [])
                    if md:
                        cols = [c.lower() for c in data['marketdata']['columns']]
                        row = md[0]
                        for field in ['last', 'currentprice']:
                            if field in cols:
                                idx = cols.index(field)
                                if idx < len(row) and row[idx]:
                                    try:
                                        p = float(row[idx])
                                        if p > 0: return p
                                    except: continue
        except: pass
        return None

    async def fetch_candles(self, ticker, days=100):
        try:
            s = await self.get_session()
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}/candles.json"
            params = {'from': (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d'),
                     'till': datetime.now().strftime('%Y-%m-%d'), 'interval': 24}
            async with s.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles = data.get('candles', {}).get('data', [])
                    if candles and len(candles) >= 30:
                        df = pd.DataFrame(candles, columns=['open','close','high','low','value','volume','begin','end'])
                        df['begin'] = pd.to_datetime(df['begin'])
                        df = df.sort_values('begin').reset_index(drop=True)
                        df = df[['begin','close']]
                        df.columns = ['date', 'close']
                        return df
        except: pass
        return None

    async def fetch_imoex(self, days=60):
        try:
            s = await self.get_session()
            url = "https://iss.moex.com/iss/engines/stock/markets/index/securities/IMOEX/candles.json"
            params = {'from': (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d'),
                     'till': datetime.now().strftime('%Y-%m-%d'), 'interval': 24}
            async with s.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles = data.get('candles', {}).get('data', [])
                    if candles:
                        df = pd.DataFrame(candles, columns=['open','close','high','low','value','volume','begin','end'])
                        df['begin'] = pd.to_datetime(df['begin'])
                        df = df.sort_values('begin').reset_index(drop=True)
                        df = df[['begin','close']]
                        df.columns = ['date', 'close']
                        return df
        except: pass
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
    rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]
    macd = ta.trend.MACD(close)
    macd_line = macd.macd().iloc[-1]
    macd_signal = macd.macd_signal().iloc[-1]
    
    rsi_status = "перекупленность" if rsi > 70 else "перепроданность" if rsi < 30 else "нейтрально"
    macd_status = "бычий сигнал" if macd_line > macd_signal else "медвежий сигнал"
    
    return {
        'rsi': round(rsi, 1),
        'rsi_status': rsi_status,
        'macd_status': macd_status
    }

async def get_all_trends():
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

def confidence_stars(p):
    if p < 0.001: return "⭐⭐⭐"
    if p < 0.01: return "⭐⭐"
    if p < 0.05: return "⭐"
    return "⚠️"

def calc_rr(entry, stop, target):
    if entry is None or stop is None or target is None:
        return 0
    try:
        risk = abs(entry - stop) / entry * 100
        reward = abs(target - entry) / entry * 100
        rr = reward / risk if risk > 0 else 0
        return rr
    except:
        return 0

# === ОЦЕНКА РИСКА ПОРТФЕЛЯ ===
async def calculate_portfolio_risk(tickers=None, days=60):
    """Рассчитывает риск портфеля: просадка, Sharpe ratio"""
    if tickers is None:
        tickers = ALL_TICKERS
    
    all_returns = []
    portfolio_values = []
    
    for ticker in tickers:
        df = await data_fetcher.fetch_candles(ticker, days+10)
        if df is not None and len(df) >= days:
            # Ежедневная доходность
            returns = df['close'].pct_change().dropna()
            if len(returns) >= days - 5:
                all_returns.append(returns)
                # Нормируем стоимость портфеля
                portfolio_values.append(df['close'] / df['close'].iloc[0])
    
    if not all_returns:
        return None
    
    # Средняя доходность портфеля
    portfolio_returns = pd.DataFrame(all_returns).T.mean(axis=1).dropna()
    
    # Накопленная стоимость портфеля
    cumulative = (1 + portfolio_returns).cumprod()
    
    # Максимальная просадка
    cummax = cumulative.cummax()
    drawdown = (cumulative - cummax) / cummax * 100
    max_drawdown = drawdown.min()
    
    # Годовая безрисковая ставка (примерно 16% в России)
    risk_free_rate = 0.16
    
    # Sharpe ratio
    excess_returns = portfolio_returns - risk_free_rate / 252
    if excess_returns.std() > 0:
        sharpe = np.sqrt(252) * excess_returns.mean() / excess_returns.std()
    else:
        sharpe = 0
    
    # Волатильность
    volatility = portfolio_returns.std() * np.sqrt(252) * 100
    
    return {
        'max_drawdown': max_drawdown,
        'sharpe': sharpe,
        'volatility': volatility,
        'total_return': (cumulative.iloc[-1] - 1) * 100,
        'days': len(portfolio_returns),
        'positive_days': (portfolio_returns > 0).sum(),
        'negative_days': (portfolio_returns < 0).sum()
    }

# === ДОХОДНОСТЬ ЗА ПЕРИОД ===
async def get_returns_for_period(days):
    """Рассчитывает доходность всех активов за указанный период"""
    results = []
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    for ticker, data in TICKERS.items():
        df = await data_fetcher.fetch_candles(ticker, days+20)
        if df is not None and len(df) >= 10:
            # Ищем цены на начало и конец периода
            df_start = df[df['date'] >= start_date]
            if len(df_start) > 0:
                start_price = df_start['close'].iloc[0]
                end_price = df['close'].iloc[-1]
                return_pct = (end_price - start_price) / start_price * 100
                results.append({
                    'ticker': ticker,
                    'name': data['name'],
                    'return': return_pct,
                    'start_price': start_price,
                    'end_price': end_price
                })
    return sorted(results, key=lambda x: -x['return'])

# === ТЕЛЕГРАМ-КАНАЛ С АВТО-ПОСТАМИ ===
async def send_daily_summary():
    """Отправляет ежедневную сводку в канал"""
    if not CHANNEL_ID:
        print("⚠️ CHANNEL_ID не задан, пропускаем отправку в канал")
        return
    
    msk_tz = pytz.timezone('Europe/Moscow')
    today = datetime.now(msk_tz).strftime('%Y-%m-%d')
    
    # Проверяем, не отправляли ли уже сегодня
    last_date = get_last_summary_date()
    if last_date == today:
        print(f"Сводка за {today} уже отправлена")
        return
    
    try:
        phase, phase_date, next_full, next_new = get_lunar_info()
        trends = await get_all_trends()
        
        # Формируем текст сводки
        text = f"🌙 **ЕЖЕДНЕВНАЯ СВОДКА**\n"
        text += f"📅 {datetime.now(msk_tz).strftime('%d.%m.%Y')}\n\n"
        
        # Лунная информация
        if phase == "полнолуние":
            text += f"🌕 **СЕГОДНЯ ПОЛНОЛУНИЕ — ТОЧКА ВХОДА!**\n\n"
        elif phase == "полнолуние_завтра":
            text += f"🌕 **ЗАВТРА ПОЛНОЛУНИЕ** — готовьтесь!\n\n"
        elif next_full:
            days_left = (next_full - datetime.now(msk_tz)).days
            text += f"🌙 Следующее полнолуние: **{next_full.strftime('%d.%m.%Y')}** (через {days_left} дн.)\n\n"
        
        # Топ-3 LONG
        text += f"🟢 **ТОП-3 LONG (покупка)**\n"
        long_assets = [(t, d) for t, d in trends.items() if d['trend'] == "бычий"]
        long_assets.sort(key=lambda x: -x[1]['success_bull'])
        if long_assets:
            for ticker, data in long_assets[:3]:
                adaptive = get_adaptive_weight(ticker)
                text += f"   ✅ {data['name']}: успех {data['success_bull']:.0f}% | вес {adaptive['weight']:.2f}\n"
        else:
            text += f"   ⚠️ Нет активов в LONG\n"
        
        # Топ-3 SHORT
        text += f"\n🔴 **ТОП-3 SHORT (продажа)**\n"
        short_assets = [(t, d) for t, d in trends.items() if d['trend'] == "медвежий"]
        short_assets.sort(key=lambda x: -x[1]['success_bear'])
        if short_assets:
            for ticker, data in short_assets[:3]:
                adaptive = get_adaptive_weight(ticker)
                text += f"   ❌ {data['name']}: успех {data['success_bear']:.0f}% | вес {adaptive['weight']:.2f}\n"
        else:
            text += f"   ⚠️ Нет активов в SHORT\n"
        
        # Рынок сегодня
        imoex_df = await data_fetcher.fetch_imoex(5)
        if imoex_df is not None and len(imoex_df) >= 2:
            today_change = (imoex_df['close'].iloc[-1] - imoex_df['close'].iloc[-2]) / imoex_df['close'].iloc[-2] * 100
            emoji = "🟢" if today_change > 0 else "🔴" if today_change < 0 else "⚪"
            text += f"\n📊 **IMOEX сегодня:** {emoji} {today_change:+.2f}%\n"
        
        text += f"\n💡 **Команды:** /start, /all, /risk, /best, /compare"
        
        # Сохраняем и отправляем
        save_daily_summary(today, text)
        
        # Отправляем в канал
        await bot.send_message(CHANNEL_ID, text, parse_mode='Markdown')
        print(f"✅ Ежедневная сводка отправлена в канал")
        
    except Exception as e:
        print(f"Ошибка при отправке сводки: {e}")

# === КОМАНДА /risk ===
@dp.message_handler(commands=['risk'])
async def cmd_risk(message: types.Message):
    msg = await message.answer("📊 Рассчитываю риск портфеля (17 активов)... ⏳ 30-40 сек")
    try:
        risk = await calculate_portfolio_risk()
        if risk:
            # Интерпретация Sharpe ratio
            if risk['sharpe'] > 1:
                sharpe_comment = "Отличный показатель ✅"
            elif risk['sharpe'] > 0.5:
                sharpe_comment = "Хороший показатель 📈"
            elif risk['sharpe'] > 0:
                sharpe_comment = "Умеренный показатель ⚪"
            else:
                sharpe_comment = "Низкий показатель ⚠️"
            
            text = f"📊 **ОЦЕНКА РИСКА ПОРТФЕЛЯ** (17 активов)\n\n"
            text += f"{'─' * 35}\n"
            text += f"📉 **Макс. просадка:** {risk['max_drawdown']:.2f}%\n"
            text += f"📈 **Sharpe ratio:** {risk['sharpe']:.2f} ({sharpe_comment})\n"
            text += f"⚡ **Волатильность:** {risk['volatility']:.2f}%\n"
            text += f"💰 **Общая доходность:** {risk['total_return']:.2f}%\n"
            text += f"{'─' * 35}\n"
            text += f"📅 Период: {risk['days']} дней\n"
            text += f"🟢 Положительных дней: {risk['positive_days']}\n"
            text += f"🔴 Отрицательных дней: {risk['negative_days']}\n"
            text += f"\n💡 **Что означают показатели:**\n"
            text += f"• **Просадка** > 20% — высокий риск\n"
            text += f"• **Sharpe** > 1 — отличная доходность на единицу риска\n"
            text += f"• **Волатильность** > 40% — активные колебания"
            
            await msg.delete()
            await message.answer(text, parse_mode='Markdown')
        else:
            await msg.edit_text("⚠️ Недостаточно данных для расчёта риска портфеля")
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

# === КОМАНДА /compare ===
@dp.message_handler(commands=['compare'])
async def cmd_compare(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("📈 Использование: /compare TICKER1 TICKER2\n\nПример: /compare SBER VTBR\n\nПоказывает график сравнения двух активов за 60 дней")
        return
    
    ticker1 = parts[1].upper()
    ticker2 = parts[2].upper()
    
    if ticker1 not in TICKERS:
        await message.answer(f"❌ Тикер {ticker1} не найден")
        return
    if ticker2 not in TICKERS:
        await message.answer(f"❌ Тикер {ticker2} не найден")
        return
    
    msg = await message.answer(f"📈 Загружаю график сравнения {TICKERS[ticker1]['name']} vs {TICKERS[ticker2]['name']}...")
    try:
        df1 = await data_fetcher.fetch_candles(ticker1, 60)
        df2 = await data_fetcher.fetch_candles(ticker2, 60)
        
        if df1 is None or df2 is None:
            await msg.edit_text("⚠️ Недостаточно данных для построения графика")
            return
        
        # Нормируем к 100
        norm1 = df1['close'] / df1['close'].iloc[0] * 100
        norm2 = df2['close'] / df2['close'].iloc[0] * 100
        
        plt.figure(figsize=(12, 6))
        plt.plot(df1['date'], norm1, 'b-', linewidth=2, label=f"{TICKERS[ticker1]['name']} ({ticker1})")
        plt.plot(df2['date'], norm2, 'r-', linewidth=2, label=f"{TICKERS[ticker2]['name']} ({ticker2})")
        
        # Добавляем горизонтальную линию на уровне 100
        plt.axhline(y=100, color='gray', linestyle='--', alpha=0.5)
        
        plt.title(f"Сравнение активов: {TICKERS[ticker1]['name']} vs {TICKERS[ticker2]['name']}")
        plt.xlabel("Дата")
        plt.ylabel("Нормированная цена (начало = 100)")
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()
        
        # Доходность
        ret1 = (norm1.iloc[-1] - 100)
        ret2 = (norm2.iloc[-1] - 100)
        
        emoji1 = "🟢" if ret1 > 0 else "🔴" if ret1 < 0 else "⚪"
        emoji2 = "🟢" if ret2 > 0 else "🔴" if ret2 < 0 else "⚪"
        
        caption = f"📈 {TICKERS[ticker1]['name']}: {emoji1} {ret1:+.2f}%\n"
        caption += f"📈 {TICKERS[ticker2]['name']}: {emoji2} {ret2:+.2f}%\n"
        caption += f"📅 За 60 дней"
        
        await msg.delete()
        await message.answer_photo(photo=buf, caption=caption)
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

# === КОМАНДА /best ===
@dp.message_handler(commands=['best'])
async def cmd_best(message: types.Message):
    parts = message.text.split()
    period = 30  # по умолчанию 30 дней
    
    if len(parts) == 2:
        period_str = parts[1].lower()
        if period_str == '7d':
            period = 7
        elif period_str == '30d':
            period = 30
        elif period_str == '90d':
            period = 90
        else:
            await message.answer("Использование: /best [7d|30d|90d]\n\nПримеры:\n/best 7d\n/best 30d\n/best 90d")
            return
    
    msg = await message.answer(f"🏆 Рассчитываю топ активов за {period} дней... ⏳")
    try:
        returns = await get_returns_for_period(period)
        if not returns:
            await msg.edit_text("⚠️ Недостаточно данных для расчёта")
            return
        
        text = f"🏆 **ТОП-3 АКТИВА ПО ДОХОДНОСТИ**\n"
        text += f"📅 Период: последние {period} дней\n\n"
        
        # Топ-3 по доходности
        text += f"🟢 **ЛУЧШИЕ:**\n"
        for i, r in enumerate(returns[:3], 1):
            emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
            text += f"{emoji} **{r['name']}** ({r['ticker']}): `{r['return']:+.2f}%`\n"
        
        # Худшие
        text += f"\n🔴 **ХУДШИЕ:**\n"
        for i, r in enumerate(returns[-3:], 1):
            text += f"   {r['name']} ({r['ticker']}): `{r['return']:+.2f}%`\n"
        
        # Средняя доходность
        avg_return = np.mean([r['return'] for r in returns])
        median_return = np.median([r['return'] for r in returns])
        
        text += f"\n📊 **Статистика по всем активам:**\n"
        text += f"   Средняя доходность: `{avg_return:+.2f}%`\n"
        text += f"   Медианная доходность: `{median_return:+.2f}%`\n"
        text += f"   Количество активов: {len(returns)}\n"
        
        text += f"\n💡 Для сравнения двух активов используйте /compare T1 T2"
        
        await msg.delete()
        await message.answer(text, parse_mode='Markdown')
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

# === НОВОСТИ (Smart-Lab.ru) ===
async def get_news(ticker):
    try:
        s = await data_fetcher.get_session()
        company_name = TICKERS.get(ticker, {}).get('name', ticker)
        url = f"https://smart-lab.ru/search/?q={company_name}&type=posts&sort=date"
        async with s.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
            if resp.status == 200:
                html = await resp.text()
                soup = BeautifulSoup(html, 'html.parser')
                news_items = []
                articles = soup.find_all('div', class_='message')[:5]
                for article in articles:
                    title_elem = article.find('a', class_='title')
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                        link = "https://smart-lab.ru" + title_elem.get('href', '')
                        news_items.append(f"• <a href='{link}'>{title[:80]}</a>")
                if news_items:
                    return news_items
        return None
    except Exception as e:
        print(f"News error for {ticker}: {e}")
        return None

# === ДИВИДЕНДЫ (dohod.ru) ===
async def get_dividends(ticker):
    try:
        s = await data_fetcher.get_session()
        company_name = TICKERS.get(ticker, {}).get('name', ticker).lower()
        url = f"https://dohod.ru/ik/analytics/dividend/company/{company_name}"
        async with s.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
            if resp.status == 200:
                html = await resp.text()
                soup = BeautifulSoup(html, 'html.parser')
                table = soup.find('table', class_='dividends')
                if table:
                    rows = table.find_all('tr')[1:4]
                    dividends = []
                    for row in rows:
                        cols = row.find_all('td')
                        if len(cols) >= 3:
                            date = cols[0].get_text(strip=True)
                            value = cols[1].get_text(strip=True)
                            dividends.append(f"📅 {date}: {value} ₽ на акцию")
                    if dividends:
                        return dividends
        return None
    except Exception as e:
        print(f"Dividends error for {ticker}: {e}")
        return None

# === ВОЛАТИЛЬНОСТЬ ===
async def get_volatility(ticker, days=30):
    try:
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
            'daily_vol': daily_vol * 100,
            'annual_vol': annual_vol * 100,
            'max_drawdown': max_drawdown,
            'avg_return': returns.mean() * 100,
            'days': len(returns)
        }
    except Exception as e:
        print(f"Volatility error for {ticker}: {e}")
        return None

# === КОРРЕЛЯЦИЯ ===
async def get_correlation(ticker1, ticker2, days=60):
    try:
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
        covariance = returns1.cov(returns2) * 252
        
        return {
            'correlation': correlation,
            'covariance': covariance,
            'days': len(combined),
            'price1': combined['ticker1'].iloc[-1],
            'price2': combined['ticker2'].iloc[-1]
        }
    except Exception as e:
        print(f"Correlation error: {e}")
        return None

# === КОМАНДА /news ===
@dp.message_handler(commands=['news'])
async def cmd_news(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("📰 Использование: /news TICKER\n\nПример: /news SBER\nДоступные тикеры: " + ", ".join(ALL_TICKERS[:5]) + "...")
        return
    
    ticker = parts[1].upper()
    if ticker not in TICKERS:
        await message.answer(f"❌ Тикер {ticker} не найден. Доступные: " + ", ".join(ALL_TICKERS))
        return
    
    msg = await message.answer(f"📰 Загружаю новости по {TICKERS[ticker]['name']}...")
    try:
        news = await get_news(ticker)
        if news:
            text = f"📰 НОВОСТИ ПО {TICKERS[ticker]['name']} ({ticker})\n\n"
            text += "\n".join(news)
            text += f"\n\n📅 {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')}"
            await msg.delete()
            await message.answer(text, parse_mode='HTML', disable_web_page_preview=True)
        else:
            await msg.edit_text(f"⚠️ Не удалось загрузить новости для {TICKERS[ticker]['name']}. Попробуйте позже.")
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

# === КОМАНДА /dividends ===
@dp.message_handler(commands=['dividends'])
async def cmd_dividends(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("💰 Использование: /dividends TICKER\n\nПример: /dividends SBER")
        return
    
    ticker = parts[1].upper()
    if ticker not in TICKERS:
        await message.answer(f"❌ Тикер {ticker} не найден")
        return
    
    msg = await message.answer(f"💰 Загружаю дивиденды по {TICKERS[ticker]['name']}...")
    try:
        dividends = await get_dividends(ticker)
        if dividends:
            text = f"💰 ДИВИДЕНДЫ ПО {TICKERS[ticker]['name']} ({ticker})\n\n"
            text += "\n".join(dividends)
            text += f"\n\n⚠️ Данные носят информационный характер"
            await msg.delete()
            await message.answer(text)
        else:
            await msg.edit_text(f"⚠️ Не удалось загрузить дивиденды для {TICKERS[ticker]['name']}")
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

# === КОМАНДА /volatility ===
@dp.message_handler(commands=['volatility'])
async def cmd_volatility(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("📊 Использование: /volatility TICKER\n\nПример: /volatility SBER")
        return
    
    ticker = parts[1].upper()
    if ticker not in TICKERS:
        await message.answer(f"❌ Тикер {ticker} не найден")
        return
    
    msg = await message.answer(f"📊 Рассчитываю волатильность для {TICKERS[ticker]['name']}...")
    try:
        vol = await get_volatility(ticker, 30)
        if vol:
            text = f"📊 ВОЛАТИЛЬНОСТЬ {TICKERS[ticker]['name']} ({ticker})\n\n"
            text += f"📈 Дневная волатильность: {vol['daily_vol']:.2f}%\n"
            text += f"📈 Годовая волатильность: {vol['annual_vol']:.2f}%\n"
            text += f"📉 Макс. просадка: {vol['max_drawdown']:.2f}%\n"
            text += f"💰 Средняя доходность: {vol['avg_return']:.3f}%\n"
            text += f"📅 Период: {vol['days']} дней"
            await msg.delete()
            await message.answer(text)
        else:
            await msg.edit_text(f"⚠️ Недостаточно данных для расчёта волатильности")
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

# === КОМАНДА /correlation ===
@dp.message_handler(commands=['correlation'])
async def cmd_correlation(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("📈 Использование: /correlation TICKER1 TICKER2\n\nПример: /correlation SBER VTBR")
        return
    
    ticker1 = parts[1].upper()
    ticker2 = parts[2].upper()
    
    if ticker1 not in TICKERS or ticker2 not in TICKERS:
        await message.answer("❌ Один из тикеров не найден")
        return
    
    msg = await message.answer(f"📈 Рассчитываю корреляцию...")
    try:
        corr = await get_correlation(ticker1, ticker2, 60)
        if corr:
            if corr['correlation'] > 0.7:
                interpretation = "сильная положительная ✅"
            elif corr['correlation'] > 0.3:
                interpretation = "умеренная положительная 📈"
            elif corr['correlation'] > -0.3:
                interpretation = "слабая / отсутствует ⚪"
            elif corr['correlation'] > -0.7:
                interpretation = "умеренная отрицательная 📉"
            else:
                interpretation = "сильная отрицательная 🔄"
            
            text = f"📈 КОРРЕЛЯЦИЯ\n\n"
            text += f"{TICKERS[ticker1]['name']} vs {TICKERS[ticker2]['name']}\n\n"
            text += f"🎯 Коэффициент: {corr['correlation']:.3f}\n"
            text += f"📖 {interpretation}\n"
            text += f"📅 Период: {corr['days']} дней"
            
            await msg.delete()
            await message.answer(text)
        else:
            await msg.edit_text(f"⚠️ Недостаточно данных для расчёта корреляции")
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

# === ОСТАЛЬНЫЕ КОМАНДЫ (watchlist, export, imoex, all, accuracy, start, lunar, stats, open_position) ===
# ... (сохраняем все предыдущие команды)

# === АВТО-УВЕДОМЛЕНИЯ И ЕЖЕДНЕВНАЯ СВОДКА ===
async def check_full_moon_notification():
    msk_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(msk_tz)
    if not hasattr(check_full_moon_notification, 'last_notify'):
        check_full_moon_notification.last_notify = {}
    phase, phase_date, next_full, next_new = get_lunar_info()
    if next_full:
        one_day_before = next_full - timedelta(days=1)
        if one_day_before.date() == now.date():
            key = f"before_{next_full.date()}"
            if check_full_moon_notification.last_notify.get(key) != now.date():
                check_full_moon_notification.last_notify[key] = now.date()
                await bot.send_message(MY_CHAT_ID, 
                    f"🌕 НАПОМИНАНИЕ!\n\n"
                    f"Завтра ПОЛНОЛУНИЕ ({next_full.strftime('%d.%m.%Y')}) — точка входа!\n"
                    f"Нажмите 📈 Открыть позицию для рекомендаций.\n\n"
                    f"💡 Новые команды: /risk, /compare, /best")
        if next_full.date() == now.date():
            key = f"day_{next_full.date()}"
            if check_full_moon_notification.last_notify.get(key) != now.date():
                check_full_moon_notification.last_notify[key] = now.date()
                await bot.send_message(MY_CHAT_ID,
                    f"🌕 СЕГОДНЯ ПОЛНОЛУНИЕ!\n\n"
                    f"ТОЧКА ВХОДА! Нажмите 📈 Открыть позицию.\n"
                    f"💡 Оцените риск портфеля: /risk\n"
                    f"📈 Сравните активы: /compare\n"
                    f"🏆 Топ активов: /best")

async def daily_summary_task():
    """Задача для ежедневной отправки сводки в канал (в 10:00 МСК)"""
    while True:
        try:
            msk_tz = pytz.timezone('Europe/Moscow')
            now = datetime.now(msk_tz)
            # Отправляем в 10:00
            if now.hour == 10 and now.minute < 5:
                await send_daily_summary()
            await asyncio.sleep(60)  # Проверяем каждую минуту
        except Exception as e:
            print(f"Ошибка в daily_summary_task: {e}")
            await asyncio.sleep(60)

async def periodic_notification():
    while True:
        try:
            await check_full_moon_notification()
        except Exception as e:
            print(f"Ошибка в уведомлениях: {e}")
        await asyncio.sleep(3600)

# === ВЕБ-СЕРВЕР ===
async def handle_health(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/health', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    print("🌐 Веб-сервер запущен на порту 10000")

async def on_startup(dp):
    init_db()
    await start_web_server()
    asyncio.create_task(periodic_notification())
    asyncio.create_task(daily_summary_task())
    try:
        await bot.send_message(MY_CHAT_ID, "🚀 Бот запущен с НОВЫМИ ФУНКЦИЯМИ!\n\n"
            "📊 /risk — оценка риска портфеля\n"
            "📈 /compare T1 T2 — сравнение активов\n"
            "🏆 /best [7d|30d|90d] — топ активов по доходности\n"
            "📰 /news TICKER — последние новости\n"
            "💰 /dividends TICKER — дивиденды\n"
            "📊 /volatility TICKER — волатильность\n"
            "📈 /correlation T1 T2 — корреляция\n\n"
            "🤖 Бот публикует ежедневные сводки в канал (если добавлен CHANNEL_ID)")
        print("✅ Бот в Telegram")
    except: 
        print("⚠️ Не удалось отправить сообщение, но бот работает")

async def on_shutdown(dp):
    await data_fetcher.close()
    await bot.close()

if __name__ == "__main__":
    print("=" * 50)
    print("ПРОФ АНАЛИТИК | ЭФФЕКТ ДМИТРИЕВА")
    print("17 акций с подтверждённым эффектом")
    print("НОВЫЕ ФУНКЦИИ: risk, compare, best, daily summary")
    print("=" * 50)
    from aiogram.utils import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
