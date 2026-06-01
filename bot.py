import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.contrib.middlewares.logging import LoggingMiddleware
import logging
import ssl
import certifi
import warnings
import os
from aiohttp import web

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
    now = datetime.now()
    next_full = next_new = None
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        if dt > now:
            next_full = dt
            break
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        if dt > now:
            next_new = dt
            break
    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        if (now - dt).days <= 1 and (now - dt).days >= 0:
            return "полнолуние", dt, next_full, next_new
        if (dt - now).days == 1:
            return "полнолуние_завтра", dt, next_full, next_new
    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        if abs((now - dt).days) <= 1:
            return "новолуние", dt, next_full, next_new
    new_moons = [datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M") for d, t in LUNAR_PHASES["new_moons"]]
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
        [KeyboardButton(text="📊 Историческая статистика")]
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

async def get_all_trends():
    results = {}
    for ticker in ALL_TICKERS:
        try:
            df = await data_fetcher.fetch_candles(ticker, 100)
            price = await data_fetcher.get_price(ticker)
            trend = calc_trend(df)
            results[ticker] = {**TICKERS[ticker], "price": price, "trend": trend}
        except:
            results[ticker] = {**TICKERS[ticker], "price": None, "trend": "ошибка"}
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

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        f"🌙 ПРОФ АНАЛИТИК | ЭФФЕКТ ДМИТРИЕВА\n\n"
        f"📊 17 акций с подтверждённым эффектом\n\n"
        f"🌙 Фазы Луны — информация о текущей фазе\n"
        f"📈 Открыть позицию — рекомендация по входу с учётом % успеха и R/R\n"
        f"📊 Историческая статистика — успешность по каждому активу\n\n"
        f"По методике: полнолуние → точка входа",
        reply_markup=keyboard
    )

@dp.message_handler(lambda message: message.text == "🌙 Фазы Луны")
async def lunar_phases_cmd(message: types.Message):
    msg = await message.answer("🌙 Загружаю данные...")
    try:
        phase, phase_date, next_full, next_new = get_lunar_info()
        now = datetime.now()
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
        text += f"{i}. {data['name']}: +{data['return_bull']:.2f}% (успех {data['success_bull']:.0f}%)\n"
    text += f"\n{'─' * 35}\n"
    text += f"📈 ПОЛНАЯ ТАБЛИЦА:\n"
    for ticker, data in sorted(TICKERS.items(), key=lambda x: -x[1]['return_bull']):
        stars = confidence_stars(data['p_value'])
        text += f"\n{data['name']} ({ticker}) {stars}\n"
        text += f"   📈 LONG: +{data['return_bull']:.2f}% | Успех {data['success_bull']:.0f}%\n"
        text += f"   📉 SHORT: +{data['return_bear']:.2f}% | Успех {data['success_bear']:.0f}%\n"
    text += f"\n{'─' * 35}\n"
    text += f"⚠️ Статистика основана на 2 годах данных\n📖 Решение принимает трейдер"
    await message.answer(text)

@dp.message_handler(lambda message: message.text == "📈 Открыть позицию")
async def open_position_cmd(message: types.Message):
    msg = await message.answer("📈 Анализирую рынок... ⏳ 30-40 сек")
    try:
        phase, phase_date, next_full, next_new = get_lunar_info()
        trends = await get_all_trends()
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
        text += f"\n📊 ТЕКУЩИЕ ТРЕНДЫ АКТИВОВ:\n\n"
        for ticker, data in trends.items():
            emoji = "🟢" if data['trend'] == "бычий" else "🔴" if data['trend'] == "медвежий" else "⚪"
            price_str = f"{data['price']:.2f}₽" if data['price'] else "Н/Д"
            stars = confidence_stars(data['p_value'])
            text += f"{emoji} {data['name']} ({ticker}): {price_str}\n"
            text += f"   📈 Тренд: {data['trend']} | Доверие: {stars}\n"
            if data['trend'] == "бычий":
                stop = data['price'] * 0.97 if data['price'] else None
                target = data['price'] * (1 + data['return_bull']/100) if data['price'] else None
                rr = calc_rr(data['price'], stop, target)
                text += f"   🟢 LONG: +{data['return_bull']:.2f}% | Успех {data['success_bull']:.0f}% | R/R: 1:{rr:.1f}\n"
            elif data['trend'] == "медвежий":
                stop = data['price'] * 1.03 if data['price'] else None
                target = data['price'] * (1 - data['return_bear']/100) if data['price'] else None
                rr = calc_rr(data['price'], stop, target)
                text += f"   🔴 SHORT: +{data['return_bear']:.2f}% | Успех {data['success_bear']:.0f}% | R/R: 1:{rr:.1f}\n"
            else:
                text += f"   ⚪ Эффект НЕ РАБОТАЕТ\n"
            text += f"\n"
        text += f"🎯 ИТОГОВАЯ РЕКОМЕНДАЦИЯ:\n"
        if phase == "полнолуние":
            text += f"📢 СЕГОДНЯ ПОЛНОЛУНИЕ — ТОЧКА ВХОДА!\n\n"
            for ticker, data in trends.items():
                if data['trend'] == "бычий":
                    stop = data['price'] * 0.97 if data['price'] else None
                    target = data['price'] * (1 + data['return_bull']/100) if data['price'] else None
                    rr = calc_rr(data['price'], stop, target)
                    text += f"✅ {data['name']}: ПОКУПКА (успех {data['success_bull']:.0f}%, +{data['return_bull']:.2f}%, R/R 1:{rr:.1f})\n"
                elif data['trend'] == "медвежий":
                    stop = data['price'] * 1.03 if data['price'] else None
                    target = data['price'] * (1 - data['return_bear']/100) if data['price'] else None
                    rr = calc_rr(data['price'], stop, target)
                    text += f"❌ {data['name']}: ПРОДАЖА (успех {data['success_bear']:.0f}%, +{data['return_bear']:.2f}%, R/R 1:{rr:.1f})\n"
                elif data['trend'] == "боковик":
                    text += f"⚠️ {data['name']}: НЕ ТОРГУЕМ\n"
        elif phase == "полнолуние_завтра" and next_full:
            text += f"📢 Полнолуние ЗАВТРА ({next_full.strftime('%d.%m.%Y')}) — готовьтесь!\n\n"
            for ticker, data in trends.items():
                if data['trend'] == "бычий":
                    text += f"🟢 {data['name']}: готовиться к ПОКУПКЕ (успех {data['success_bull']:.0f}%)\n"
                elif data['trend'] == "медвежий":
                    text += f"🔴 {data['name']}: готовиться к ПРОДАЖЕ (успех {data['success_bear']:.0f}%)\n"
        elif next_full:
            days = (next_full - datetime.now()).days
            text += f"⏳ Следующая точка входа: {next_full.strftime('%d.%m.%Y')} (через {days} дн.)\n"
        else:
            text += f"⏸ Активный сигнал отсутствует\n"
        text += f"\n⚠️ СТОП-ЛОСС ОБЯЗАТЕЛЕН! | 📊 Статистика за 2024-2026\n💡 R/R показывает соотношение потенциальной прибыли к риску"
        await msg.delete()
        await message.answer(text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка: {str(e)[:100]}")

# === ВЕБ-СЕРВЕР ДЛЯ RENDER ===
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
    await start_web_server()
    try:
        await bot.send_message(MY_CHAT_ID, "🚀 Бот запущен\n/start")
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
    print("=" * 50)
    from aiogram.utils import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
