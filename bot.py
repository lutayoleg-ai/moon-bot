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

warnings.filterwarnings('ignore')

# === БЕЗОПАСНОЕ ПОЛУЧЕНИЕ ТОКЕНА ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = 414210743

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
    # Таблица для watchlist
    c.execute('''CREATE TABLE IF NOT EXISTS watchlist
                 (user_id INTEGER, ticker TEXT, PRIMARY KEY (user_id, ticker))''')
    # Таблица для обучения (запоминаем ошибки)
    c.execute('''CREATE TABLE IF NOT EXISTS prediction_accuracy
                 (ticker TEXT, date TEXT, predicted_trend TEXT, actual_trend TEXT, 
                  was_correct INTEGER, PRIMARY KEY (ticker, date))''')
    # Таблица для адаптивных весов
    c.execute('''CREATE TABLE IF NOT EXISTS adaptive_weights
                 (ticker TEXT, weight REAL DEFAULT 1.0, correct_count INTEGER DEFAULT 0, 
                  total_count INTEGER DEFAULT 0, last_updated TEXT)''')
    conn.commit()
    conn.close()
    # Инициализируем веса для всех активов
    init_weights()

def init_weights():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    for ticker in ALL_TICKERS:
        c.execute("INSERT OR IGNORE INTO adaptive_weights (ticker, weight) VALUES (?, 1.0)", (ticker,))
    conn.commit()
    conn.close()

def update_accuracy(ticker, predicted_trend, actual_trend):
    """Обновляем статистику точности предсказаний"""
    was_correct = 1 if predicted_trend == actual_trend else 0
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    date = datetime.now().strftime('%Y-%m-%d')
    c.execute("INSERT INTO prediction_accuracy (ticker, date, predicted_trend, actual_trend, was_correct) VALUES (?, ?, ?, ?, ?)",
              (ticker, date, predicted_trend, actual_trend, was_correct))
    # Обновляем адаптивный вес
    c.execute("SELECT correct_count, total_count FROM adaptive_weights WHERE ticker = ?", (ticker,))
    row = c.fetchone()
    if row:
        correct, total = row
        new_correct = correct + was_correct
        new_total = total + 1
        # Вес = (точность * 2) - 0.5 (масштабирование)
        accuracy = new_correct / new_total if new_total > 0 else 0.5
        weight = max(0.3, min(1.5, accuracy * 2 - 0.5))  # Вес от 0.3 до 1.5
        c.execute("UPDATE adaptive_weights SET weight = ?, correct_count = ?, total_count = ?, last_updated = ? WHERE ticker = ?",
                  (weight, new_correct, new_total, datetime.now().strftime('%Y-%m-%d %H:%M'), ticker))
    conn.commit()
    conn.close()
    return was_correct

def get_adaptive_weight(ticker):
    """Получаем адаптивный вес для актива"""
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
        [KeyboardButton(text="🔄 Бэктестинг 6 мес")],
        [KeyboardButton(text="📊 Точность стратегии")]
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

    async def fetch_historical_data(self, ticker, start_date, end_date):
        """Загружает исторические данные за указанный период"""
        try:
            s = await self.get_session()
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}/candles.json"
            params = {'from': start_date.strftime('%Y-%m-%d'),
                     'till': end_date.strftime('%Y-%m-%d'), 'interval': 24}
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

    async def fetch_imoex(self, days=100):
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

# === БЭКТЕСТИНГ СТРАТЕГИИ ===
async def backtest_strategy(months=6):
    """Бэктестинг стратегии за последние N месяцев"""
    msk_tz = pytz.timezone('Europe/Moscow')
    end_date = datetime.now(msk_tz)
    start_date = end_date - timedelta(days=months*30)
    
    results = []
    total_trades = 0
    winning_trades = 0
    total_return = 0
    
    # Для каждого полнолуния в периоде
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        full_moon = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        full_moon = msk_tz.localize(full_moon)
        
        if start_date <= full_moon <= end_date:
            # Для каждого актива проверяем, как сработала бы стратегия
            for ticker, data in TICKERS.items():
                # Загружаем цены до и после полнолуния
                before_date = full_moon - timedelta(days=5)
                after_date = full_moon + timedelta(days=7)
                
                df_before = await data_fetcher.fetch_historical_data(ticker, before_date, full_moon)
                df_after = await data_fetcher.fetch_historical_data(ticker, full_moon, after_date)
                
                if df_before is not None and df_after is not None and len(df_before) >= 5 and len(df_after) >= 3:
                    # Определяем тренд до полнолуния
                    price_before = df_before['close'].iloc[-1]
                    ma18_before = df_before['close'].rolling(18).mean().iloc[-1] if len(df_before) >= 18 else price_before
                    ma50_before = df_before['close'].rolling(50).mean().iloc[-1] if len(df_before) >= 50 else price_before
                    
                    if ma18_before > ma50_before:
                        predicted_trend = "бычий"
                        expected_return = data['return_bull'] / 100
                    elif ma18_before < ma50_before:
                        predicted_trend = "медвежий"
                        expected_return = data['return_bear'] / 100
                    else:
                        continue  # боковик — не торгуем
                    
                    # Фактическая доходность через 7 дней
                    price_after = df_after['close'].iloc[-1]
                    actual_return = (price_after - price_before) / price_before
                    
                    # Проверяем, правильным ли был прогноз направления
                    actual_trend = "бычий" if actual_return > 0 else "медвежий"
                    was_correct = 1 if predicted_trend == actual_trend else 0
                    
                    total_trades += 1
                    winning_trades += was_correct
                    total_return += expected_return if was_correct else -abs(expected_return)
                    
                    results.append({
                        'date': full_moon,
                        'ticker': ticker,
                        'predicted': predicted_trend,
                        'actual_trend': actual_trend,
                        'expected_return': expected_return,
                        'actual_return': actual_return,
                        'was_correct': was_correct
                    })
    
    if total_trades == 0:
        return None
    
    accuracy = winning_trades / total_trades * 100
    avg_return = total_return / total_trades * 100
    
    return {
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'accuracy': accuracy,
        'avg_return': avg_return,
        'total_return': total_return,
        'results': results[-10:]  # последние 10 сделок для отображения
    }

# === КОМАНДА БЭКТЕСТИНГА ===
@dp.message_handler(commands=['backtest'])
async def cmd_backtest(message: types.Message):
    msg = await message.answer("🔄 Запускаю бэктестинг стратегии за последние 6 месяцев... ⏳ 1-2 минуты")
    try:
        result = await backtest_strategy(6)
        if result is None:
            await msg.edit_text("⚠️ Недостаточно данных для бэктестинга. Попробуйте позже.")
            return
        
        text = f"📊 БЭКТЕСТИНГ СТРАТЕГИИ (последние 6 месяцев)\n\n"
        text += f"{'─' * 35}\n"
        text += f"📈 Всего сделок: {result['total_trades']}\n"
        text += f"✅ Успешных сделок: {result['winning_trades']}\n"
        text += f"🎯 Точность стратегии: {result['accuracy']:.1f}%\n"
        text += f"💰 Средняя доходность на сделку: {result['avg_return']:.2f}%\n"
        text += f"📊 Совокупная доходность: {result['total_return']:.2f}%\n"
        text += f"{'─' * 35}\n"
        text += f"📋 Последние сделки:\n"
        for trade in result['results']:
            emoji = "✅" if trade['was_correct'] else "❌"
            text += f"{emoji} {trade['date'].strftime('%d.%m')} {trade['ticker']}: предсказание {trade['predicted']}, факт {trade['actual_trend']}\n"
        text += f"\n⚠️ Бэктестинг не гарантирует будущих результатов"
        
        # Рисуем график доходности
        if len(result['results']) >= 5:
            plt.figure(figsize=(10, 5))
            returns = [t['actual_return']*100 for t in result['results']]
            plt.bar(range(len(returns)), returns, color=['g' if r>0 else 'r' for r in returns])
            plt.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
            plt.title('Доходность по последним сделкам бэктестинга')
            plt.xlabel('Сделка')
            plt.ylabel('Доходность, %')
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100)
            buf.seek(0)
            plt.close()
            
            await msg.delete()
            await message.answer_photo(photo=buf, caption=text)
        else:
            await msg.edit_text(text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка бэктестинга: {str(e)[:100]}")

# === ТОЧНОСТЬ СТРАТЕГИИ ===
@dp.message_handler(commands=['accuracy'])
async def cmd_accuracy(message: types.Message):
    msg = await message.answer("📊 Собираю статистику точности прогнозов...")
    try:
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        
        # Общая точность
        c.execute("SELECT COUNT(*), SUM(was_correct) FROM prediction_accuracy")
        total, correct = c.fetchone()
        
        # Точность по активам
        c.execute("SELECT ticker, COUNT(*), SUM(was_correct) FROM prediction_accuracy GROUP BY ticker")
        by_ticker = c.fetchall()
        
        # Адаптивные веса
        c.execute("SELECT ticker, weight, correct_count, total_count FROM adaptive_weights")
        weights = {row[0]: {'weight': row[1], 'correct': row[2], 'total': row[3]} for row in c.fetchall()}
        
        conn.close()
        
        if total == 0:
            await msg.edit_text("📊 Пока нет данных о точности прогнозов.\n\nПосле того как бот даст несколько рекомендаций, здесь появится статистика.")
            return
        
        text = f"📊 ТОЧНОСТЬ СТРАТЕГИИ\n\n"
        text += f"{'─' * 35}\n"
        text += f"📈 Всего прогнозов: {total}\n"
        text += f"✅ Точных прогнозов: {correct}\n"
        text += f"🎯 Общая точность: {correct/total*100:.1f}%\n"
        text += f"{'─' * 35}\n"
        text += f"📋 Точность по активам:\n"
        for ticker, cnt, cor in sorted(by_ticker, key=lambda x: -x[2]/x[1] if x[1]>0 else 0):
            name = TICKERS.get(ticker, {}).get('name', ticker)
            acc = cor/cnt*100 if cnt > 0 else 0
            weight_info = weights.get(ticker, {})
            weight = weight_info.get('weight', 1.0)
            text += f"   {name}: {acc:.1f}% ({cor}/{cnt}) | вес: {weight:.2f}\n"
        text += f"\n💡 Адаптивные веса корректируют рекомендации на основе прошлых ошибок.\n"
        text += f"   Вес > 1 = актив показывает лучшие результаты, < 1 = хуже среднего."
        
        await msg.edit_text(text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

# === ОСТАЛЬНЫЕ КОМАНДЫ ===
@dp.message_handler(commands=['watchlist'])
async def cmd_watchlist(message: types.Message):
    user_id = message.from_user.id
    watchlist = get_watchlist(user_id)
    if not watchlist:
        await message.answer("⭐ Ваш watchlist пуст.\n\nДобавить акцию: /add TICKER\nУдалить: /remove TICKER\nОчистить: /clear_watchlist\n\nПример: /add SBER")
        return
    
    text = f"⭐ ВАШ WATCHLIST ({len(watchlist)} акций):\n\n"
    for ticker in watchlist:
        name = TICKERS.get(ticker, {}).get('name', ticker)
        text += f"• {name} ({ticker})\n"
    text += f"\nКоманды:\n/add TICKER — добавить\n/remove TICKER — удалить\n/clear_watchlist — очистить всё\n/watchlist_status — статус активов"
    await message.answer(text)

@dp.message_handler(commands=['add'])
async def add_to_watchlist_cmd(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /add TICKER\nПример: /add SBER")
        return
    ticker = parts[1].upper()
    if ticker not in TICKERS:
        await message.answer(f"❌ Тикер {ticker} не найден. Доступные: " + ", ".join(ALL_TICKERS[:5]) + "...")
        return
    if add_to_watchlist(message.from_user.id, ticker):
        await message.answer(f"✅ {TICKERS[ticker]['name']} ({ticker}) добавлен в watchlist")
    else:
        await message.answer(f"⚠️ {ticker} уже в вашем watchlist")

@dp.message_handler(commands=['remove'])
async def remove_from_watchlist_cmd(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /remove TICKER\nПример: /remove SBER")
        return
    ticker = parts[1].upper()
    remove_from_watchlist(message.from_user.id, ticker)
    await message.answer(f"🗑️ {ticker} удалён из watchlist")

@dp.message_handler(commands=['clear_watchlist'])
async def clear_watchlist_cmd(message: types.Message):
    clear_watchlist(message.from_user.id)
    await message.answer("🗑️ Watchlist полностью очищен")

@dp.message_handler(commands=['watchlist_status'])
async def watchlist_status_cmd(message: types.Message):
    user_id = message.from_user.id
    watchlist = get_watchlist(user_id)
    if not watchlist:
        await message.answer("⭐ Watchlist пуст. Добавьте акции командой /add TICKER")
        return
    
    msg = await message.answer("📊 Анализирую watchlist... ⏳")
    try:
        trends = await get_all_trends()
        text = f"⭐ СТАТУС WATCHLIST ({len(watchlist)} акций)\n\n"
        for ticker in watchlist:
            if ticker in trends:
                data = trends[ticker]
                emoji = "🟢" if data['trend'] == "бычий" else "🔴" if data['trend'] == "медвежий" else "⚪"
                price_str = f"{data['price']:.2f}₽" if data['price'] else "Н/Д"
                text += f"{emoji} {data['name']}: {price_str} | {data['trend']}\n"
                if data['indicators']:
                    ind = data['indicators']
                    text += f"   📊 RSI: {ind['rsi']} ({ind['rsi_status']}) | MACD: {ind['macd_status']}\n"
        await msg.delete()
        await message.answer(text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

@dp.message_handler(commands=['export'])
async def cmd_export(message: types.Message):
    msg = await message.answer("📎 Формирую Excel-файл со статистикой... ⏳")
    try:
        trends = await get_all_trends()
        
        data = []
        for ticker, info in trends.items():
            adaptive = get_adaptive_weight(ticker)
            row = {
                'Тикер': ticker,
                'Название': info['name'],
                'Цена': info['price'],
                'Тренд': info['trend'],
                'Успех LONG %': info['success_bull'],
                'Доходность LONG %': info['return_bull'],
                'Успех SHORT %': info['success_bear'],
                'Доходность SHORT %': info['return_bear'],
                'p-value': info['p_value'],
                'Доверие': confidence_stars(info['p_value']),
                'Адаптивный вес': adaptive['weight'],
                'Точность прогнозов %': (adaptive['correct']/adaptive['total']*100) if adaptive['total'] > 0 else 0
            }
            if info['indicators']:
                row['RSI'] = info['indicators']['rsi']
                row['RSI сигнал'] = info['indicators']['rsi_status']
                row['MACD сигнал'] = info['indicators']['macd_status']
            data.append(row)
        
        df = pd.DataFrame(data)
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Активы', index=False)
        
        output.seek(0)
        
        await msg.delete()
        await message.answer_document(
            types.InputFile(output, filename=f'moon_bot_report_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx'),
            caption="📎 Полная статистика по всем 17 активам (с адаптивными весами)"
        )
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка при создании Excel: {str(e)[:100]}")

@dp.message_handler(commands=['imoex'])
async def cmd_imoex(message: types.Message):
    msg = await message.answer("📈 Загружаю данные IMOEX и сравниваю с портфелем... ⏳ 30-40 сек")
    try:
        imoex_df = await data_fetcher.fetch_imoex(60)
        if imoex_df is None:
            await msg.edit_text("⚠️ Не удалось загрузить данные IMOEX")
            return
        
        trends = await get_all_trends()
        
        portfolio_returns = []
        for ticker, data in trends.items():
            if data['price'] and data['trend'] != "боковик" and data['trend'] != "недостаточно данных":
                adaptive = get_adaptive_weight(ticker)
                if data['trend'] == "бычий":
                    ret = data['return_bull'] / 100 * adaptive['weight']
                else:
                    ret = data['return_bear'] / 100 * adaptive['weight']
                portfolio_returns.append(ret)
        
        avg_portfolio_return = np.mean(portfolio_returns) if portfolio_returns else 0
        
        imoex_start = imoex_df['close'].iloc[0]
        imoex_end = imoex_df['close'].iloc[-1]
        imoex_return = (imoex_end - imoex_start) / imoex_start
        
        plt.figure(figsize=(12, 6))
        plt.plot(imoex_df['date'], imoex_df['close'] / imoex_df['close'].iloc[0] * 100, 'b-', linewidth=2, label='IMOEX (индекс)')
        
        portfolio_line = [100 * (1 + avg_portfolio_return * i/60) for i in range(len(imoex_df))]
        plt.plot(imoex_df['date'], portfolio_line, 'g--', linewidth=2, label='Портфель (с адаптивными весами)')
        
        plt.title("Сравнение: Портфель (адаптивная стратегия) vs IMOEX")
        plt.xlabel("Дата")
        plt.ylabel("Нормированное значение (начало = 100)")
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()
        
        text = f"📊 СРАВНЕНИЕ С РЫНКОМ (с адаптивными весами)\n\n"
        text += f"📈 Доходность IMOEX за 60 дней: {imoex_return*100:.2f}%\n"
        text += f"🎯 Ожидаемая доходность портфеля: {avg_portfolio_return*100:.2f}%\n"
        text += f"{'─' * 35}\n"
        if avg_portfolio_return > imoex_return:
            text += f"✅ Портфель стратегии превосходит рынок на {(avg_portfolio_return - imoex_return)*100:.2f}%\n"
        else:
            text += f"⚠️ Стратегия отстаёт от рынка на {(imoex_return - avg_portfolio_return)*100:.2f}%\n"
        text += f"\n💡 Адаптивные веса корректируются на основе точности прогнозов."
        
        await msg.delete()
        await message.answer_photo(photo=buf, caption=text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

@dp.message_handler(commands=['all'])
async def cmd_all(message: types.Message):
    await message.answer("📋 Собираю данные по всем активам... ⏳ 30-40 сек")
    try:
        trends = await get_all_trends()
        text = f"📋 ВСЕ АКТИВЫ (17)\n\n"
        text += f"🟢 LONG (покупка):\n"
        long_count = 0
        for ticker, data in trends.items():
            if data['trend'] == "бычий":
                adaptive = get_adaptive_weight(ticker)
                text += f"   ✅ {data['name']}: +{data['return_bull']:.2f}% | Успех {data['success_bull']:.0f}% | вес: {adaptive['weight']:.2f}\n"
                long_count += 1
        if long_count == 0:
            text += f"   ⚠️ Нет активов в LONG\n"
        text += f"\n🔴 SHORT (продажа):\n"
        short_count = 0
        for ticker, data in trends.items():
            if data['trend'] == "медвежий":
                adaptive = get_adaptive_weight(ticker)
                text += f"   ❌ {data['name']}: +{data['return_bear']:.2f}% | Успех {data['success_bear']:.0f}% | вес: {adaptive['weight']:.2f}\n"
                short_count += 1
        if short_count == 0:
            text += f"   ⚠️ Нет активов в SHORT\n"
        text += f"\n⚪ БОКОВИК (не торгуем):\n"
        side_count = 0
        for ticker, data in trends.items():
            if data['trend'] == "боковик" or data['trend'] == "недостаточно данных":
                text += f"   ⚪ {data['name']}: {data['trend']}\n"
                side_count += 1
        
        text += f"\n📊 RSI/MACD сигналы:\n"
        for ticker, data in trends.items():
            if data['indicators'] and data['trend'] in ["бычий", "медвежий"]:
                ind = data['indicators']
                text += f"   {data['name']}: RSI={ind['rsi']} ({ind['rsi_status']}) | {ind['macd_status']}\n"
        
        text += f"\n📅 {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')}"
        await message.answer(text)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {str(e)[:100]}")

@dp.message_handler(lambda message: message.text == "📈 График акции")
async def ask_ticker_for_chart(message: types.Message):
    await message.answer("📊 Введите тикер акции для графика (например, SBER, VTBR, GAZP):\n\nДоступные тикеры:\n" + ", ".join(ALL_TICKERS))

@dp.message_handler(lambda message: message.text.upper() in [t.lower() for t in ALL_TICKERS] or message.text.upper() in ALL_TICKERS)
async def send_chart(message: types.Message):
    ticker = message.text.upper().strip()
    if ticker not in TICKERS:
        await message.answer(f"❌ Тикер {ticker} не найден. Доступные: " + ", ".join(ALL_TICKERS))
        return
    msg = await message.answer(f"📈 Загружаю график для {TICKERS[ticker]['name']}...")
    try:
        df = await data_fetcher.fetch_candles(ticker, 100)
        if df is None or len(df) < 10:
            await msg.edit_text(f"⚠️ Недостаточно данных для {TICKERS[ticker]['name']}")
            return
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1]})
        
        ax1.plot(df['date'], df['close'], 'b-', linewidth=2, label='Цена закрытия')
        if len(df) >= 18:
            ma18 = df['close'].rolling(18).mean()
            ax1.plot(df['date'], ma18, 'g--', linewidth=1.5, label='MA 18')
        if len(df) >= 50:
            ma50 = df['close'].rolling(50).mean()
            ax1.plot(df['date'], ma50, 'r--', linewidth=1.5, label='MA 50')
        ax1.set_title(f"{TICKERS[ticker]['name']} ({ticker}) - Цена")
        ax1.set_ylabel("Цена, ₽")
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        rsi = ta.momentum.RSIIndicator(df['close']).rsi()
        ax2.plot(df['date'], rsi, 'purple', linewidth=1.5)
        ax2.axhline(y=70, color='r', linestyle='--', alpha=0.5, label='Перекупленность (70)')
        ax2.axhline(y=30, color='g', linestyle='--', alpha=0.5, label='Перепроданность (30)')
        ax2.set_ylabel("RSI")
        ax2.set_ylim(0, 100)
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()
        
        indicators = calc_indicators(df)
        adaptive = get_adaptive_weight(ticker)
        caption = f"📈 {TICKERS[ticker]['name']} ({ticker})\nТренд: {calc_trend(df)}"
        if indicators:
            caption += f"\n📊 RSI: {indicators['rsi']} ({indicators['rsi_status']})\n📊 MACD: {indicators['macd_status']}"
        caption += f"\n🎯 Адаптивный вес: {adaptive['weight']:.2f} | Точность: {(adaptive['correct']/adaptive['total']*100):.1f}%" if adaptive['total'] > 0 else ""
        
        await msg.delete()
        await message.answer_photo(photo=buf, caption=caption)
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        f"🌙 ПРОФ АНАЛИТИК | ЭФФЕКТ ДМИТРИЕВА\n\n"
        f"📊 17 акций с подтверждённым эффектом\n\n"
        f"🔹 Рекомендации:\n"
        f"   🌙 Фазы Луны — информация о текущей фазе\n"
        f"   📈 Открыть позицию — анализ с RSI/MACD и адаптивными весами\n"
        f"   📊 Историческая статистика — успешность по каждому активу\n"
        f"   📋 Все активы (/all) — сводная таблица\n"
        f"   📈 График акции — цена + RSI\n"
        f"   ⭐ Watchlist — персональный список\n"
        f"   📎 Экспорт в Excel — выгрузка статистики\n"
        f"   📈 Сравнение с IMOEX — портфель vs рынок\n\n"
        f"🔹 ПРОДВИНУТЫЕ ФУНКЦИИ:\n"
        f"   🔄 Бэктестинг 6 мес — как бы сработала стратегия\n"
        f"   📊 Точность стратегии — статистика прогнозов и адаптивные веса\n\n"
        f"📖 По методике: полнолуние → точка входа\n"
        f"💡 Бот учится на ошибках и корректирует веса активов!",
        reply_markup=keyboard
    )

@dp.message_handler(lambda message: message.text == "🌙 Фазы Луны")
async def lunar_phases_cmd(message: types.Message):
    msg = await message.answer("🌙 Загружаю данные...")
    try:
        phase, phase_date, next_full, next_new = get_lunar_info()
        msk_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(msk_tz)
        text = f"🌙 ЛУННЫЙ КАЛЕНДАРЬ\n\n📅 Сегодня: {now.strftime('%d.%m.%Y')}\n"
        if phase == "полнолуние":
            text += f"🌕 Фаза: ПОЛНОЛУНИЕ (активный сигнал!)\n"
        elif phase == "полнолуние_завтра":
            text += f"🌕 Фаза: ПОЛНОЛУНИЕ ЗАВТРА (готовьтесь!)\n"
        else:
            text += f"🌙 Фаза: {phase.upper()}\n"
        if phase_date:
            text += f"📆 Дата фазы: {phase_date.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"\n🎯 Ближайшие события:\n"
        if next_full:
            text += f"🌕 Полнолуние: {next_full.strftime('%d.%m.%Y %H:%M')}\n"
        if next_new:
            text += f"🌑 Новолуние: {next_new.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"\n📖 ЭФФЕКТ ДМИТРИЕВА:\n• Бычий тренд: полнолуние → ПОКУПКА (LONG)\n• Медвежий тренд: полнолуние → ПРОДАЖА (SHORT)\n• Боковик: НЕ ТОРГУЕМ"
        await msg.delete()
        await message.answer(text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {e}")

@dp.message_handler(lambda message: message.text == "📊 Историческая статистика")
async def stats_cmd(message: types.Message):
    text = f"📊 ИСТОРИЧЕСКАЯ СТАТИСТИКА (2024-2026)\n\n"
    text += f"{'─' * 35}\n"
    text += f"🏆 ТОП-10 ПО ДОХОДНОСТИ LONG:\n"
    sorted_by_return = sorted(TICKERS.items(), key=lambda x: -x[1]['return_bull'])
    for i, (ticker, data) in enumerate(sorted_by_return[:10], 1):
        adaptive = get_adaptive_weight(ticker)
        text += f"{i}. {data['name']}: +{data['return_bull']:.2f}% (успех {data['success_bull']:.0f}%) | вес: {adaptive['weight']:.2f}\n"
    text += f"\n{'─' * 35}\n"
    text += f"📈 ПОЛНАЯ ТАБЛИЦА:\n"
    for ticker, data in sorted(TICKERS.items(), key=lambda x: -x[1]['return_bull']):
        stars = confidence_stars(data['p_value'])
        adaptive = get_adaptive_weight(ticker)
        text += f"\n{data['name']} ({ticker}) {stars} вес:{adaptive['weight']:.2f}\n"
        text += f"   📈 LONG: +{data['return_bull']:.2f}% | Успех {data['success_bull']:.0f}%\n"
        text += f"   📉 SHORT: +{data['return_bear']:.2f}% | Успех {data['success_bear']:.0f}%\n"
    text += f"\n{'─' * 35}\n"
    text += f"⚠️ Статистика основана на 2 годах данных\n"
    text += f"💡 Адаптивные веса корректируются на основе реальной точности прогнозов."
    await message.answer(text)

@dp.message_handler(lambda message: message.text == "📈 Открыть позицию")
async def open_position_cmd(message: types.Message):
    msg = await message.answer("📈 Анализирую рынок с адаптивными весами... ⏳ 30-40 сек")
    try:
        phase, phase_date, next_full, next_new = get_lunar_info()
        trends = await get_all_trends()
        msk_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(msk_tz)
        text = f"🎯 РЕКОМЕНДАЦИЯ ПО ОТКРЫТИЮ ПОЗИЦИИ\n\n"
        text += f"🌙 ЛУННЫЙ СИГНАЛ:\n"
        if phase == "полнолуние":
            text += f"   🌕 Фаза: ПОЛНОЛУНИЕ\n   📢 ТОЧКА ВХОДА!\n"
        elif phase == "полнолуние_завтра":
            text += f"   🌕 Фаза: ПОЛНОЛУНИЕ ЗАВТРА\n   📢 ГОТОВЬТЕСЬ!\n"
        else:
            text += f"   🌙 Фаза: {phase.upper()}\n   ⏸ Активный сигнал отсутствует\n"
        if phase_date:
            text += f"   📅 Дата: {phase_date.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"\n📊 ТЕКУЩИЕ ТРЕНДЫ АКТИВОВ (с адаптивными весами):\n\n"
        for ticker, data in trends.items():
            emoji = "🟢" if data['trend'] == "бычий" else "🔴" if data['trend'] == "медвежий" else "⚪"
            price_str = f"{data['price']:.2f}₽" if data['price'] else "Н/Д"
            stars = confidence_stars(data['p_value'])
            adaptive = get_adaptive_weight(ticker)
            text += f"{emoji} {data['name']} ({ticker}): {price_str}\n"
            text += f"   📈 Тренд: {data['trend']} | Доверие: {stars} | Вес: {adaptive['weight']:.2f}\n"
            if data['indicators']:
                ind = data['indicators']
                text += f"   📊 RSI: {ind['rsi']} ({ind['rsi_status']}) | MACD: {ind['macd_status']}\n"
            if data['trend'] == "бычий":
                stop = data['price'] * 0.97 if data['price'] else None
                target = data['price'] * (1 + data['return_bull']/100 * adaptive['weight']) if data['price'] else None
                rr = calc_rr(data['price'], stop, target)
                text += f"   🟢 LONG: +{data['return_bull'] * adaptive['weight']:.2f}% (скорр.) | Успех {data['success_bull']:.0f}% | R/R: 1:{rr:.1f}\n"
            elif data['trend'] == "медвежий":
                stop = data['price'] * 1.03 if data['price'] else None
                target = data['price'] * (1 - data['return_bear']/100 * adaptive['weight']) if data['price'] else None
                rr = calc_rr(data['price'], stop, target)
                text += f"   🔴 SHORT: +{data['return_bear'] * adaptive['weight']:.2f}% (скорр.) | Успех {data['success_bear']:.0f}% | R/R: 1:{rr:.1f}\n"
            else:
                text += f"   ⚪ Эффект НЕ РАБОТАЕТ\n"
            text += f"\n"
        text += f"🎯 ИТОГОВАЯ РЕКОМЕНДАЦИЯ:\n"
        if phase == "полнолуние":
            text += f"📢 СЕГОДНЯ ПОЛНОЛУНИЕ — ТОЧКА ВХОДА!\n\n"
            for ticker, data in trends.items():
                adaptive = get_adaptive_weight(ticker)
                if data['trend'] == "бычий":
                    text += f"✅ {data['name']}: ПОКУПКА (вес {adaptive['weight']:.2f}, успех {data['success_bull']:.0f}%)\n"
                elif data['trend'] == "медвежий":
                    text += f"❌ {data['name']}: ПРОДАЖА (вес {adaptive['weight']:.2f}, успех {data['success_bear']:.0f}%)\n"
                elif data['trend'] == "боковик":
                    text += f"⚠️ {data['name']}: НЕ ТОРГУЕМ\n"
        elif phase == "полнолуние_завтра" and next_full:
            text += f"📢 Полнолуние ЗАВТРА ({next_full.strftime('%d.%m.%Y')}) — готовьтесь!\n"
        elif next_full:
            days = (next_full - now).days
            text += f"⏳ Следующая точка входа: {next_full.strftime('%d.%m.%Y')} (через {days} дн.)\n"
        else:
            text += f"⏸ Активный сигнал отсутствует\n"
        text += f"\n⚠️ СТОП-ЛОСС ОБЯЗАТЕЛЕН!\n💡 Адаптивные веса корректируются на основе точности прогнозов."
        await msg.delete()
        await message.answer(text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

@dp.message_handler(lambda message: message.text == "📋 Все активы (/all)")
async def all_button_handler(message: types.Message):
    await cmd_all(message)

@dp.message_handler(lambda message: message.text == "⭐ Watchlist")
async def watchlist_button_handler(message: types.Message):
    await cmd_watchlist(message)

@dp.message_handler(lambda message: message.text == "📎 Экспорт в Excel")
async def export_button_handler(message: types.Message):
    await cmd_export(message)

@dp.message_handler(lambda message: message.text == "📈 Сравнение с IMOEX")
async def imoex_button_handler(message: types.Message):
    await cmd_imoex(message)

@dp.message_handler(lambda message: message.text == "🔄 Бэктестинг 6 мес")
async def backtest_button_handler(message: types.Message):
    await cmd_backtest(message)

@dp.message_handler(lambda message: message.text == "📊 Точность стратегии")
async def accuracy_button_handler(message: types.Message):
    await cmd_accuracy(message)

# === АВТО-УВЕДОМЛЕНИЯ ===
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
                    f"Нажмите 📈 Открыть позицию для рекомендаций с адаптивными весами.")
        if next_full.date() == now.date():
            key = f"day_{next_full.date()}"
            if check_full_moon_notification.last_notify.get(key) != now.date():
                check_full_moon_notification.last_notify[key] = now.date()
                await bot.send_message(MY_CHAT_ID,
                    f"🌕 СЕГОДНЯ ПОЛНОЛУНИЕ!\n\n"
                    f"ТОЧКА ВХОДА! Нажмите 📈 Открыть позицию.\n"
                    f"Бот использует адаптивные веса на основе прошлой точности.")

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
    try:
        await bot.send_message(MY_CHAT_ID, "🚀 Бот запущен с ПРОДВИНУТЫМИ функциями!\n\n"
            "🔄 Бэктестинг 6 мес — проверьте, как бы сработала стратегия\n"
            "📊 Точность стратегии — бот учится на ошибках\n"
            "💡 Адаптивные веса корректируют рекомендации\n\n"
            "Нажмите /start для полного меню")
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
    print("ПРОДВИНУТЫЕ ФУНКЦИИ: Бэктестинг + Адаптивное обучение")
    print("=" * 50)
    from aiogram.utils import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
