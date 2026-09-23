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
import json
import tempfile
import sqlite3
import signal

warnings.filterwarnings('ignore')

# === ТОКЕН ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = 414210743
CHANNEL_ID = os.environ.get("CHANNEL_ID")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден")

if not CHANNEL_ID:
    raise ValueError("❌ CHANNEL_ID не найден")

# === ПАРАМЕТРЫ СТРАТЕГИИ ===
STRATEGY = {
    'MA_FAST': 10,
    'MA_SLOW': 30,
    'ADX_THRESHOLD': 20,
    'STOP_LOSS': 0.06,
    'TAKE_PROFIT': 0.12,
    'DAILY_LOSS_LIMIT': 0.06,
    'CAPITAL': 100000,
    'POSITION_SIZE': 0.25
}

MAX_CONCURRENT_POSITIONS = 4
COMMISSION = 0.003
MOEX_TIMEOUT = 15
STATE_FILE = "positions.json"
MOEX_STALE_THRESHOLD_MIN = 30  # P1-FINAL: порог для critical-лога

DISCLAIMER = (
    "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ Данная информация не является индивидуальной инвестиционной рекомендацией, "
    "а финансовые инструменты и операции могут не соответствовать вашему инвестиционному профилю."
)

LUNAR_DISCLAIMER = (
    "\n\n⚠️ Лунная стратегия — не является торговой рекомендацией. "
    "Использовать только как дополнительный фактор."
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === 17 АКТИВОВ ===
TICKERS = {
    "SBER": {"name": "Сбер"},
    "VTBR": {"name": "ВТБ"},
    "GAZP": {"name": "Газпром"},
    "LKOH": {"name": "Лукойл"},
    "ROSN": {"name": "Роснефть"},
    "TATN": {"name": "Татнефть"},
    "NLMK": {"name": "НЛМК"},
    "GMKN": {"name": "Норникель"},
    "MTLR": {"name": "Мечел"},
    "ALRS": {"name": "Алроса"},
    "AFLT": {"name": "Аэрофлот"},
    "YDEX": {"name": "Яндекс"},
    "OZON": {"name": "OZON"},
    "MGNT": {"name": "Магнит"},
    "CBOM": {"name": "МКБ"},
    "WUSH": {"name": "Whoosh"},
    "ASTR": {"name": "Астра"},
}
ALL_TICKERS = list(TICKERS.keys())

# === ЛУННЫЕ ДАННЫЕ ===
# ⚠️ ВАЖНО: При расширении после 2027 верифицировать через NASA/USNO
LUNAR_PHASES = {
    "full_moons": [
        ("2026-01-03", "13:04"), ("2026-02-02", "01:10"), ("2026-03-03", "14:39"),
        ("2026-04-02", "05:13"), ("2026-05-01", "20:24"), ("2026-05-31", "11:46"),
        ("2026-06-30", "02:58"), ("2026-07-29", "17:37"), ("2026-08-28", "07:19"),
        ("2026-09-26", "19:50"), ("2026-10-26", "07:13"), ("2026-11-24", "17:55"),
        ("2026-12-24", "04:29"),
        ("2027-01-22", "15:17"), ("2027-02-20", "23:23"), ("2027-03-22", "05:44"),
        ("2027-04-20", "13:27"), ("2027-05-20", "00:59"), ("2027-06-18", "15:44"),
        ("2027-07-18", "07:03"), ("2027-08-16", "22:29"), ("2027-09-15", "13:04"),
        ("2027-10-15", "02:48"), ("2027-11-13", "15:26"), ("2027-12-13", "02:18"),
    ],
    "new_moons": [
        ("2026-01-18", "22:53"), ("2026-02-17", "15:03"), ("2026-03-19", "04:26"),
        ("2026-04-17", "14:54"), ("2026-05-16", "23:03"), ("2026-06-15", "05:56"),
        ("2026-07-14", "12:45"), ("2026-08-12", "20:37"), ("2026-09-11", "06:27"),
        ("2026-10-10", "18:50"), ("2026-11-09", "10:02"), ("2026-12-09", "03:52"),
        ("2027-01-07", "23:25"), ("2027-02-06", "18:57"), ("2027-03-08", "12:31"),
        ("2027-04-07", "02:52"), ("2027-05-06", "13:59"), ("2027-06-04", "21:41"),
        ("2027-07-04", "06:02"), ("2027-08-02", "13:07"), ("2027-08-31", "21:41"),
        ("2027-09-30", "05:36"), ("2027-10-29", "15:34"), ("2027-11-28", "04:24"),
        ("2027-12-27", "19:12"),
    ]
}

def _msk():
    return pytz.timezone('Europe/Moscow')

def _parse_lunar_datetime(date_str, time_str):
    return _msk().localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))

def _get_next_phase_date(phase_list, now=None):
    if now is None:
        now = datetime.now(_msk())
    for date_str, time_str in phase_list:
        dt = _parse_lunar_datetime(date_str, time_str)
        if dt > now:
            return dt
    return None

def get_days_until_full_moon():
    next_full = _get_next_phase_date(LUNAR_PHASES["full_moons"])
    if next_full:
        return (next_full - datetime.now(_msk())).days
    return None

def get_days_until_new_moon():
    next_new = _get_next_phase_date(LUNAR_PHASES["new_moons"])
    if next_new:
        return (next_new - datetime.now(_msk())).days
    return None

def get_lunar_info():
    now = datetime.now(_msk())
    next_full = _get_next_phase_date(LUNAR_PHASES["full_moons"], now)
    next_new = _get_next_phase_date(LUNAR_PHASES["new_moons"], now)

    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = _parse_lunar_datetime(date_str, time_str)
        delta_days = (now - dt).days
        if 0 <= delta_days <= 1:
            return "полнолуние", next_full, next_new
        if (dt - now).days == 1:
            return "полнолуние_завтра", next_full, next_new

    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = _parse_lunar_datetime(date_str, time_str)
        delta_days = (now - dt).days
        if 0 <= delta_days <= 1:
            return "новолуние", next_full, next_new
        if (dt - now).days == 1:
            return "новолуние_завтра", next_full, next_new

    new_moons = [_parse_lunar_datetime(d, t) for d, t in LUNAR_PHASES["new_moons"]]
    last_new = max([d for d in new_moons if d <= now], default=None)
    if last_new:
        days = (now - last_new).days
        return ("растущая" if days < 14 else "убывающая"), next_full, next_new

    return "обычный день", next_full, next_new

async def get_lunar_signal():
    now = datetime.now(_msk())
    next_full = _get_next_phase_date(LUNAR_PHASES["full_moons"], now)
    next_new = _get_next_phase_date(LUNAR_PHASES["new_moons"], now)

    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = _parse_lunar_datetime(date_str, time_str)
        if dt.date() == now.date():
            return "full_today", dt, next_full, next_new

    if next_full:
        days_until_full = (next_full - now).days
        if 1 <= days_until_full <= 3:
            return "prepare", next_full, next_full, next_new

    for date_str, time_str in LUNAR_PHASES["full_moons"]:
        dt = _parse_lunar_datetime(date_str, time_str)
        if dt < now:
            days_after = (now - dt).days
            if 1 <= days_after <= 5:
                return "hold", dt, next_full, next_new

    for date_str, time_str in LUNAR_PHASES["new_moons"]:
        dt = _parse_lunar_datetime(date_str, time_str)
        if dt.date() == now.date():
            return "new_today", dt, next_full, next_new

    return "none", None, next_full, next_new

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
positions = {}
last_signal_sent = {}
daily_pnl = 0.0
daily_trade_blocked = False
last_reset_date = None
lunar_notified_full = set()
lunar_notified_new = set()
positions_lock = asyncio.Lock()

# === СОХРАНЕНИЕ СОСТОЯНИЯ (ATOMIC) ===
def save_state():
    """Атомарная запись через tempfile + os.replace()"""
    try:
        data = {
            'positions': positions,
            'last_signal_sent': {
                t: {'signal': v['signal'], 'time': v['time'] if isinstance(v['time'], str) else v['time'].isoformat()}
                for t, v in last_signal_sent.items()
            },
            'daily_pnl': daily_pnl,
            'daily_trade_blocked': daily_trade_blocked,
            'last_reset_date': last_reset_date
        }
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir='.', prefix='positions_', suffix='.tmp'
        )
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, STATE_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except:
                pass
            raise
    except Exception as e:
        logger.exception(f"Ошибка сохранения состояния: {e}")


def load_state():
    """
    P1-FINAL: обработка по позициям — одна плохая позиция не ломает остальные.
    """
    global positions, last_signal_sent, daily_pnl, daily_trade_blocked, last_reset_date
    if not os.path.exists(STATE_FILE):
        logger.info("📁 Файл состояния не найден, стартуем с чистого листа")
        return
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # P1-FINAL: проверка типа positions
        raw_positions = data.get('positions', {})
        if not isinstance(raw_positions, dict):
            logger.warning("⚠️ positions в файле не dict — игнорируем")
            raw_positions = {}

        # P1-FINAL: обрабатываем по позициям
        loaded_positions = {}
        failed_count = 0
        for t, p in raw_positions.items():
            try:
                if not isinstance(p, dict):
                    failed_count += 1
                    continue
                # Валидация обязательных полей
                ptype = p.get('type')
                if ptype not in (None, 'long', 'short'):
                    logger.warning(f"⚠️ {t}: некорректный type={ptype}, пропуск")
                    failed_count += 1
                    continue
                loaded_positions[t] = {
                    'type': ptype,
                    'entry_price': float(p['entry_price']) if p.get('entry_price') is not None else None,
                    'entry_time': p.get('entry_time')  # iso-строка или None
                }
            except Exception as e:
                logger.warning(f"⚠️ {t}: ошибка загрузки позиции ({e}), пропуск")
                failed_count += 1
                continue

        positions = loaded_positions

        # last_signal_sent
        raw_lss = data.get('last_signal_sent', {})
        if isinstance(raw_lss, dict):
            for t, v in raw_lss.items():
                try:
                    t_str = v['time'] if isinstance(v['time'], str) else v['time'].isoformat()
                    last_signal_sent[t] = {'signal': v['signal'], 'time': t_str}
                except:
                    pass

        try:
            daily_pnl = float(data.get('daily_pnl', 0.0))
        except:
            daily_pnl = 0.0

        daily_trade_blocked = bool(data.get('daily_trade_blocked', False))
        last_reset_date = data.get('last_reset_date')

        open_count = len([p for p in positions.values() if p.get('type')])
        logger.info(f"✅ Состояние загружено: {open_count} открытых, {failed_count} с ошибками, "
                    f"P&L: {daily_pnl:.2f}%, blocked: {daily_trade_blocked}")
    except Exception as e:
        logger.exception(f"Ошибка загрузки состояния: {e}")
        positions = {}
        last_signal_sent = {}
        daily_pnl = 0.0
        daily_trade_blocked = False
        last_reset_date = None


# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, ticker TEXT, type TEXT,
            entry REAL, exit REAL,
            pnl_percent REAL, commission_percent REAL,
            capital REAL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY, summary TEXT
        )''')


def save_trade(ticker, trade_type, entry, exit_price, pnl_percent, commission_percent):
    with sqlite3.connect('bot_data.db') as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO trades (date, ticker, type, entry, exit, pnl_percent, commission_percent, capital) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), ticker, trade_type, entry, exit_price,
             pnl_percent, commission_percent, STRATEGY['CAPITAL'])
        )


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
    moex_last_success = None  # P1-FINAL: datetime последнего успешного запроса

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
                                        DataFetcher.moex_last_success = datetime.now()
                                        return p
                                except (ValueError, TypeError):
                                    pass
            return None
        except asyncio.TimeoutError:
            logger.error(f"Таймаут get_price({ticker})")
            return None
        except Exception as e:
            logger.error(f"Ошибка get_price({ticker}): {type(e).__name__}: {e}")
            return None

    async def fetch_candles_daily(self, ticker, days=100):
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}/candles.json"
            params = {
                'from': start.strftime('%Y-%m-%d'),
                'till': end.strftime('%Y-%m-%d'),
                'interval': 24
            }

            data = await self._fetch_json(url, params)
            candles = data.get('candles', {})
            rows = candles.get('data', [])
            cols = candles.get('columns', [])

            if rows and len(rows) >= 3:
                idx_date = next((i for i, c in enumerate(cols) if c.lower() in ('begin', 'date')), None)
                idx_open = next((i for i, c in enumerate(cols) if c.lower() == 'open'), None)
                idx_high = next((i for i, c in enumerate(cols) if c.lower() == 'high'), None)
                idx_low = next((i for i, c in enumerate(cols) if c.lower() == 'low'), None)
                idx_close = next((i for i, c in enumerate(cols) if c.lower() == 'close'), None)

                if idx_date is not None and idx_close is not None:
                    records = []
                    for row in rows:
                        if len(row) > max(idx_date, idx_close):
                            try:
                                rec = {
                                    'date': pd.to_datetime(row[idx_date]),
                                    'close': float(row[idx_close]) if idx_close is not None else None,
                                    'open': float(row[idx_open]) if idx_open is not None and row[idx_open] is not None else None,
                                    'high': float(row[idx_high]) if idx_high is not None and row[idx_high] is not None else None,
                                    'low': float(row[idx_low]) if idx_low is not None and row[idx_low] is not None else None,
                                }
                                if rec['high'] is None:
                                    rec['high'] = rec['close']
                                if rec['low'] is None:
                                    rec['low'] = rec['close']
                                if rec['open'] is None:
                                    rec['open'] = rec['close']
                                records.append(rec)
                            except:
                                pass

                    if len(records) >= 5:
                        df = pd.DataFrame(records).sort_values('date').reset_index(drop=True)
                        DataFetcher.moex_last_success = datetime.now()
                        return df
            return None
        except asyncio.TimeoutError:
            logger.error(f"Таймаут fetch_candles_daily({ticker})")
            return None
        except Exception as e:
            logger.error(f"Ошибка fetch_candles_daily({ticker}): {type(e).__name__}: {e}")
            return None

    async def healthcheck_moex(self):
        """
        P1-FINAL: moex_last_success как datetime.
        Если разница > 30 минут → logger.critical.
        Race не проблема — просто timestamp.
        """
        warned_critical = False
        while True:
            try:
                await self.get_price("SBER")

                if DataFetcher.moex_last_success is not None:
                    stale_minutes = (datetime.now() - DataFetcher.moex_last_success).total_seconds() / 60
                    if stale_minutes > MOEX_STALE_THRESHOLD_MIN:
                        if not warned_critical:
                            logger.critical(f"🚨 MOEX недоступен более {MOEX_STALE_THRESHOLD_MIN} минут "
                                            f"(последний успех: {stale_minutes:.0f} мин назад)")
                            warned_critical = True
                    else:
                        if warned_critical:
                            logger.info("✅ MOEX снова доступен")
                            warned_critical = False
            except Exception as e:
                logger.error(f"Healthcheck MOEX ошибка: {e}")
            await asyncio.sleep(180)

data_fetcher = DataFetcher()


# === ИСТОРИЯ ЦЕН ===
def get_historical_prices(df):
    if df is None or len(df) < 22:
        return None

    today = datetime.now(_msk()).date()
    last_candle_date = df['date'].iloc[-1].date()

    if last_candle_date == today:
        y_offset, w_offset, m_offset = -2, -6, -22
    else:
        y_offset, w_offset, m_offset = -1, -5, -21

    closes = df['close'].values
    result = {}

    if len(closes) >= abs(y_offset):
        result['yesterday'] = {'price': closes[y_offset], 'change_pct': 0.0}
    if len(closes) >= abs(w_offset):
        result['week_ago'] = {'price': closes[w_offset], 'change_pct': 0.0}
    if len(closes) >= abs(m_offset):
        result['month_ago'] = {'price': closes[m_offset], 'change_pct': 0.0}

    return result


def update_hist_pct(hist, current_price):
    if not hist:
        return hist
    for key in ['yesterday', 'week_ago', 'month_ago']:
        if key in hist:
            h = hist[key]['price']
            hist[key]['change_pct'] = (current_price - h) / h * 100 if h > 0 else 0.0
    return hist


def format_historical_prices(hist):
    if not hist:
        return ""
    lines = []
    for key, label in [('yesterday', 'Вчера'), ('week_ago', 'Неделю назад'), ('month_ago', 'Месяц назад')]:
        if key in hist:
            d = hist[key]
            icon = "📈" if d['change_pct'] > 0 else "📉" if d['change_pct'] < 0 else "➡️"
            lines.append(f"{icon} {label}: {d['price']:.2f} ₽ ({d['change_pct']:+.2f}%)")
    return "\n".join(lines)


# === ИНДИКАТОРЫ ===
def calculate_adx(df, period=14):
    if df is None or len(df) < period * 3:
        return None

    high = df['high']
    low = df['low']
    close = df['close']

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)

    plus_cond = (up_move > down_move) & (up_move > 0)
    plus_dm[plus_cond] = up_move[plus_cond]

    minus_cond = (down_move > up_move) & (down_move > 0)
    minus_dm[minus_cond] = down_move[minus_cond]

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0/period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1.0/period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1.0/period, adjust=False).mean() / atr)

    di_sum = plus_di + minus_di
    di_sum = di_sum.replace(0, np.nan)
    dx = (abs(plus_di - minus_di) / di_sum) * 100
    adx_series = dx.ewm(alpha=1.0/period, adjust=False).mean()

    adx_val = adx_series.iloc[-1]
    if pd.isna(adx_val):
        return None
    return float(adx_val)


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
    if df is None or len(df) < 50:
        return "недостаточно данных"
    ma18 = df['close'].rolling(18).mean().iloc[-1]
    ma50 = df['close'].rolling(50).mean().iloc[-1]
    if np.isnan(ma18) or np.isnan(ma50):
        return "недостаточно данных"
    spread = abs(ma18 - ma50) / ma50 * 100
    return "боковик" if spread < 0.7 else ("бычий" if ma18 > ma50 else "медвежий")


# === ИНФОРМАЦИЯ ПО АКТИВУ ===
async def get_asset_info(ticker):
    df = await data_fetcher.fetch_candles_daily(ticker, 100)
    price = await data_fetcher.get_price(ticker)

    if df is None or price is None or price <= 0:
        return None, "Нет данных от MOEX"

    ma18 = df['close'].rolling(18).mean().iloc[-1] if len(df) >= 18 else None
    ma50 = df['close'].rolling(50).mean().iloc[-1] if len(df) >= 50 else None
    adx = calculate_adx(df)
    trend = get_trend(df)

    recommendation = None
    if trend == "bullish" and adx is not None and adx > STRATEGY['ADX_THRESHOLD']:
        recommendation = "✅ Рекомендую открыть LONG"
    elif trend == "bearish" and adx is not None and adx > STRATEGY['ADX_THRESHOLD']:
        recommendation = "✅ Рекомендую открыть SHORT"

    adx_status = "тренд" if adx is not None and adx > STRATEGY['ADX_THRESHOLD'] else "флет"
    adx_str = f"{adx:.1f}" if adx is not None else "N/A"

    hist = get_historical_prices(df)
    hist = update_hist_pct(hist, price)

    msg = f"📊 {TICKERS[ticker]['name']} ({ticker}) 💰 {price:.2f} ₽\n"
    msg += f"{'─' * 30}\n"

    if hist:
        msg += format_historical_prices(hist) + "\n"
        msg += f"{'─' * 30}\n"

    if ma18 and ma50:
        msg += f"📈 MA18: {ma18:.2f} | MA50: {ma50:.2f}\n"
    msg += f"📊 ADX: {adx_str} ({adx_status})\n"

    if recommendation:
        msg += recommendation
    else:
        msg += "❌ Сигналов нет"

    return msg, None


# === СИГНАЛ ===
def get_signal_for_ticker(ticker, df, price):
    if df is None or price is None or price <= 0:
        return None, None, "Нет данных от MOEX"

    trend = get_trend(df)
    adx = calculate_adx(df)

    if adx is None:
        return None, {
            'ticker': ticker, 'name': TICKERS[ticker]['name'],
            'price': price, 'trend': trend, 'adx': None,
            'ma10': None, 'ma30': None
        }, "Недостаточно данных для ADX"

    ma10 = df['close'].rolling(10).mean()
    ma30 = df['close'].rolling(30).mean()
    last_ma10 = ma10.iloc[-1]
    last_ma30 = ma30.iloc[-1]

    if trend == "bullish" and adx > STRATEGY['ADX_THRESHOLD']:
        stop_price = price * (1 - STRATEGY['STOP_LOSS'])
        target_price = price * (1 + STRATEGY['TAKE_PROFIT'])
        return "LONG", {
            'ticker': ticker, 'name': TICKERS[ticker]['name'],
            'price': price, 'trend': trend, 'adx': round(adx, 1),
            'target': target_price, 'stop': stop_price,
            'signal_type': "ТРЕНД + ADX",
            'ma10': last_ma10, 'ma30': last_ma30
        }, None

    if trend == "bearish" and adx > STRATEGY['ADX_THRESHOLD']:
        stop_price = price * (1 + STRATEGY['STOP_LOSS'])
        target_price = price * (1 - STRATEGY['TAKE_PROFIT'])
        return "SHORT", {
            'ticker': ticker, 'name': TICKERS[ticker]['name'],
            'price': price, 'trend': trend, 'adx': round(adx, 1),
            'target': target_price, 'stop': stop_price,
            'signal_type': "ТРЕНД + ADX",
            'ma10': last_ma10, 'ma30': last_ma30
        }, None

    reasons = []
    if adx <= STRATEGY['ADX_THRESHOLD']:
        reasons.append(f"ADX = {adx:.1f} (нужно > {STRATEGY['ADX_THRESHOLD']}) — флет")
    if trend == "neutral":
        reasons.append("Тренд нейтральный")
    if not reasons:
        reasons.append("Условия не выполнены")

    return None, {
        'ticker': ticker, 'name': TICKERS[ticker]['name'],
        'price': price, 'trend': trend, 'adx': round(adx, 1),
        'ma10': last_ma10, 'ma30': last_ma30
    }, "\n".join(reasons)


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


def count_open_positions():
    """Только для использования под lock"""
    return sum(1 for p in positions.values() if p.get('type') is not None)


# === ГЛАВНЫЙ ЦИКЛ ===
async def process_all_tickers():
    """
    P1-FINAL:
      - daily_trade_blocked проверяется после каждого закрытия → break
      - count_open_positions под lock с флагом limit_reached
    """
    global positions, daily_pnl, daily_trade_blocked, last_signal_sent

    if not CHANNEL_ID:
        logger.error("CHANNEL_ID не задан")
        return

    await reset_daily_pnl()

    if daily_trade_blocked:
        logger.info("⛔ DAILY_LOSS_LIMIT достигнут — сигналы не отправляются")
        return

    now = datetime.now(_msk())
    now_iso = now.isoformat()
    events = []

    for ticker in ALL_TICKERS:
        # P1-FINAL: проверка флага перед каждой итерацией
        if daily_trade_blocked:
            logger.info(f"⛔ DAILY_LOSS_LIMIT в процессе цикла — прерываю обработку (последний: {ticker})")
            break

        try:
            df = await data_fetcher.fetch_candles_daily(ticker, 100)
            price = await data_fetcher.get_price(ticker)

            if df is None or price is None or price <= 0:
                continue

            current_position = positions.get(ticker, {})
            pos_type = current_position.get('type')
            pos_entry = current_position.get('entry_price')

            # === СЛУЧАЙ 1: ЕСТЬ ПОЗИЦИЯ ===
            if pos_type and pos_entry:
                if pos_type == 'long':
                    pnl_pct = (price - pos_entry) / pos_entry * 100
                else:
                    pnl_pct = (pos_entry - price) / pos_entry * 100

                exit_needed = False
                exit_reason = None

                if pnl_pct <= -STRATEGY['STOP_LOSS'] * 100:
                    exit_needed = True
                    exit_reason = f"🛑 СТОП-ЛОСС ({pnl_pct:.2f}%)"
                elif pnl_pct >= STRATEGY['TAKE_PROFIT'] * 100:
                    exit_needed = True
                    exit_reason = f"✅ ТЕЙК-ПРОФИТ ({pnl_pct:.2f}%)"

                if exit_needed:
                    pnl_percent, commission_percent = calculate_pnl_percent(pos_entry, price, pos_type)

                    async with positions_lock:
                        daily_pnl += pnl_percent
                        save_trade(ticker, pos_type, pos_entry, price, pnl_percent, commission_percent)
                        positions[ticker] = {'type': None, 'entry_price': None, 'entry_time': None}
                        last_signal_sent.pop(ticker, None)

                        if daily_pnl <= -STRATEGY['DAILY_LOSS_LIMIT'] * 100:
                            daily_trade_blocked = True
                            logger.warning(f"⛔ DAILY_LOSS_LIMIT: {daily_pnl:.2f}%")

                        save_state()

                    events.append({
                        'type': 'close',
                        'ticker': ticker,
                        'data': {
                            'name': TICKERS[ticker]['name'],
                            'pos_type': pos_type,
                            'entry': pos_entry,
                            'exit': price,
                            'pnl_pct': pnl_pct,
                            'reason': exit_reason,
                            'daily_pnl': daily_pnl
                        }
                    })

                    # P1-FINAL: после закрытия проверяем флаг и выходим из цикла
                    if daily_trade_blocked:
                        logger.info(f"⛔ DAILY_LOSS_LIMIT после закрытия {ticker} — прерываю цикл")
                        break
                continue

            # === СЛУЧАЙ 2: НЕТ ПОЗИЦИИ — ИЩЕМ СИГНАЛ ===
            signal, data, _ = get_signal_for_ticker(ticker, df, price)
            if not signal or not data or data.get('price') is None:
                continue

            prev = last_signal_sent.get(ticker)
            if prev is not None:
                prev_signal = prev.get('signal')
                if prev_signal == signal:
                    continue

            # P1-FINAL: проверка лимита — читаемо, без continue внутри lock
            limit_reached = False
            async with positions_lock:
                if count_open_positions() >= MAX_CONCURRENT_POSITIONS:
                    limit_reached = True
                else:
                    positions[ticker] = {
                        'type': signal.lower(),
                        'entry_price': price,
                        'entry_time': now_iso
                    }
                    last_signal_sent[ticker] = {'signal': signal, 'time': now_iso}
                    save_state()

            if limit_reached:
                logger.info(f"⛔ Лимит позиций достигнут, {ticker} пропущен")
                continue

            events.append({
                'type': 'open',
                'ticker': ticker,
                'data': {
                    'signal': signal,
                    'name': data['name'],
                    'price': price,
                    'adx': data['adx'],
                    'stop': data['stop'],
                    'target': data['target'],
                    'trend': data.get('trend'),
                    'signal_type': data.get('signal_type')
                }
            })

        except Exception as e:
            logger.exception(f"Ошибка обработки {ticker}: {e}")
            continue

    if events:
        await send_events(events)


async def send_events(events):
    now = datetime.now(_msk())
    opens = [e for e in events if e['type'] == 'open']
    closes = [e for e in events if e['type'] == 'close']

    parts = []

    if opens:
        part = f"🔔 СИГНАЛЫ ({now.strftime('%H:%M')})\n"
        part += f"{'═' * 35}\n"
        for e in opens:
            d = e['data']
            emoji = "🟢" if d['signal'] == 'LONG' else "🔴"
            part += f"\n{emoji} {d['name']} ({e['ticker']}) — {d['signal']}\n"
            part += f"   💰 Цена: {d['price']:.2f} ₽ | ADX: {d['adx']}\n"
            part += f"   🛑 Стоп: {d['stop']:.2f} (-{STRATEGY['STOP_LOSS']*100:.0f}%)\n"
            part += f"   ✅ Тейк: {d['target']:.2f} (+{STRATEGY['TAKE_PROFIT']*100:.0f}%)\n"
        parts.append(part)

    if closes:
        part = f"🚨 ЗАКРЫТИЕ ({now.strftime('%H:%M')})\n"
        part += f"{'═' * 35}\n"
        for e in closes:
            d = e['data']
            emoji = "✅" if d['pnl_pct'] >= 0 else "🛑"
            pos_label = "LONG" if d['pos_type'] == 'long' else "SHORT"
            part += f"\n{emoji} {d['name']} ({e['ticker']}) — {pos_label}\n"
            part += f"   {d['reason']}\n"
            part += f"   {d['entry']:.2f} → {d['exit']:.2f}\n"
            part += f"   P&L: {'+' if d['pnl_pct'] >= 0 else ''}{d['pnl_pct']:.2f}%\n"
        part += f"\n📊 P&L дня: {'+' if closes[-1]['data']['daily_pnl'] >= 0 else ''}{closes[-1]['data']['daily_pnl']:.2f}%"
        parts.append(part)

    if not parts:
        return

    msg = "\n\n".join(parts)
    msg += DISCLAIMER

    try:
        await bot.send_message(CHANNEL_ID, msg, parse_mode='HTML')
    except Exception as e:
        logger.exception(f"Ошибка отправки: {e}")


async def reset_daily_pnl():
    """P1-FINAL: save_state под positions_lock"""
    global daily_pnl, last_reset_date, daily_trade_blocked
    today = datetime.now(_msk()).date().isoformat()
    if last_reset_date != today:
        async with positions_lock:
            daily_pnl = 0.0
            daily_trade_blocked = False
            last_reset_date = today
            save_state()


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
    text += DISCLAIMER
    return text


# === АНАЛИТИКА СРАВНЕНИЯ ===
def analyze_comparison(hist):
    if not hist or 'yesterday' not in hist or 'week_ago' not in hist or 'month_ago' not in hist:
        return {
            'verdict': '❓ НЕДОСТАТОЧНО ДАННЫХ',
            'trend': 'нет данных',
            'momentum': 'нейтрально',
            'day': 0, 'week': 0, 'month': 0
        }

    day = hist['yesterday']['change_pct']
    week = hist['week_ago']['change_pct']
    month = hist['month_ago']['change_pct']

    avg_day_week = week / 5 if abs(week) > 0.1 else 0

    if month > 2 and week > 0 and day > 0:
        trend = '🚀 СИЛЬНЫЙ РОСТ'
    elif month > 0 and week > 0:
        trend = '📈 УМЕРЕННЫЙ РОСТ'
    elif month < -2 and week < 0 and day < 0:
        trend = '💥 СИЛЬНОЕ ПАДЕНИЕ'
    elif month < 0 and week < 0:
        trend = '📉 УМЕРЕННОЕ ПАДЕНИЕ'
    elif month > 0 and week < 0:
        trend = '⚠️ КОРРЕКЦИЯ'
    elif month < 0 and week > 0:
        trend = '🔄 ОТСКОК'
    else:
        trend = '➡️ БОКОВИК'

    if avg_day_week != 0:
        if day > avg_day_week * 1.5 and day > 0:
            momentum = '🔥 УСКОРЕНИЕ ВВЕРХ'
        elif day < avg_day_week * 1.5 and day < 0:
            momentum = '⚡ УСКОРЕНИЕ ВНИЗ'
        elif abs(day) < abs(avg_day_week) * 0.5:
            momentum = '💤 ЗАТУХАНИЕ'
        elif (day > 0 and avg_day_week < 0) or (day < 0 and avg_day_week > 0):
            momentum = '🔄 РАЗВОРОТ'
        else:
            momentum = '➡️ СТАБИЛЬНО'
    else:
        momentum = '➡️ БОКОВИК'

    if 'СИЛЬНЫЙ РОСТ' in trend and 'УСКОРЕНИЕ ВВЕРХ' in momentum:
        verdict = '🟢 LONG'
    elif 'СИЛЬНОЕ ПАДЕНИЕ' in trend and 'УСКОРЕНИЕ ВНИЗ' in momentum:
        verdict = '🔴 SHORT'
    elif 'РАЗВОРОТ' in momentum and month > 5:
        verdict = '⚠️ ОСТОРОЖНО'
    elif 'РАЗВОРОТ' in momentum and month < -5:
        verdict = '🟡 НАБЛЮДЕНИЕ'
    elif 'ЗАТУХАНИЕ' in momentum:
        verdict = '⚪ ЖДАТЬ'
    else:
        verdict = '⚪ НЕЙТРАЛЬНО'

    return {
        'verdict': verdict,
        'trend': trend,
        'momentum': momentum,
        'day': day, 'week': week, 'month': month
    }


async def get_comparison_analytics():
    results = []
    for ticker in ALL_TICKERS:
        df = await data_fetcher.fetch_candles_daily(ticker, 100)
        price = await data_fetcher.get_price(ticker)
        if df is None or price is None or price <= 0:
            continue
        hist = get_historical_prices(df)
        hist = update_hist_pct(hist, price)
        if not hist:
            continue
        analysis = analyze_comparison(hist)
        results.append({
            'ticker': ticker,
            'name': TICKERS[ticker]['name'],
            'price': price,
            'hist': hist,
            'analysis': analysis
        })
        await asyncio.sleep(0.05)
    return results


def format_comparison_report(results):
    if not results:
        return "❌ Нет данных от MOEX" + DISCLAIMER

    now = datetime.now(_msk())
    msg = f"📈 АНАЛИТИКА СРАВНЕНИЯ\n"
    msg += f"📅 {now.strftime('%d.%m.%Y %H:%M')}\n"
    msg += f"{'═' * 35}\n\n"

    long_signals, short_signals, watch, neutral = [], [], [], []
    for r in results:
        v = r['analysis']['verdict']
        if '🟢 LONG' in v:
            long_signals.append(r)
        elif '🔴 SHORT' in v:
            short_signals.append(r)
        elif '⚠️' in v or '🟡' in v:
            watch.append(r)
        else:
            neutral.append(r)

    for group, label in [(long_signals, "🟢 LONG"), (short_signals, "🔴 SHORT"), (watch, "⚠️ НАБЛЮДЕНИЕ")]:
        if group:
            msg += f"{label} ({len(group)}):\n"
            for r in group[:5]:
                a = r['analysis']
                msg += f"  • {r['name']} ({r['ticker']}) — {r['price']:.2f} ₽\n"
                msg += f"    1д {a['day']:+.2f}% | 1н {a['week']:+.2f}% | 1м {a['month']:+.2f}%\n"
                msg += f"    {a['momentum']}\n\n"

    if neutral:
        msg += f"⚪ БЕЗ СИГНАЛА ({len(neutral)}):\n"
        for r in neutral[:10]:
            a = r['analysis']
            msg += f"  • {r['name']} ({r['ticker']}): 1д {a['day']:+.1f}% | 1н {a['week']:+.1f}% | 1м {a['month']:+.1f}%\n"
        msg += "\n"

    msg += f"{'═' * 35}\n"
    msg += f"📊 ИТОГО: 🟢 {len(long_signals)} | 🔴 {len(short_signals)} | ⚠️ {len(watch)} | ⚪ {len(neutral)}\n"
    msg += DISCLAIMER

    return msg


# === ЛУННАЯ СТРАТЕГИЯ ===
async def lunar_notify():
    global lunar_notified_full, lunar_notified_new
    while True:
        days_until_full = get_days_until_full_moon()
        days_until_new = get_days_until_new_moon()

        if days_until_full is not None and days_until_full <= 3 and days_until_full not in lunar_notified_full:
            lunar_notified_full.add(days_until_full)
            if days_until_full == 3:
                await bot.send_message(MY_CHAT_ID, f"🌕 ЧЕРЕЗ 3 ДНЯ ПОЛНОЛУНИЕ" + LUNAR_DISCLAIMER)
            elif days_until_full == 2:
                await bot.send_message(MY_CHAT_ID, f"🌕 ЧЕРЕЗ 2 ДНЯ ПОЛНОЛУНИЕ" + LUNAR_DISCLAIMER)
            elif days_until_full == 1:
                await bot.send_message(MY_CHAT_ID, f"🌕 ЗАВТРА ПОЛНОЛУНИЕ" + LUNAR_DISCLAIMER)

        if days_until_new is not None and days_until_new <= 3 and days_until_new not in lunar_notified_new:
            lunar_notified_new.add(days_until_new)
            if days_until_new == 3:
                await bot.send_message(MY_CHAT_ID, f"🌑 ЧЕРЕЗ 3 ДНЯ НОВОЛУНИЕ" + LUNAR_DISCLAIMER)
            elif days_until_new == 2:
                await bot.send_message(MY_CHAT_ID, f"🌑 ЧЕРЕЗ 2 ДНЯ НОВОЛУНИЕ" + LUNAR_DISCLAIMER)
            elif days_until_new == 1:
                await bot.send_message(MY_CHAT_ID, f"🌑 ЗАВТРА НОВОЛУНИЕ" + LUNAR_DISCLAIMER)

        await asyncio.sleep(3600)


async def daily_lunar_summary():
    if not CHANNEL_ID:
        return
    today = datetime.now(_msk()).strftime('%Y-%m-%d')
    if get_last_summary_date() == today:
        return

    ph, nxt_full, nxt_new = get_lunar_info()
    trends = {}
    for ticker in ALL_TICKERS:
        df = await data_fetcher.fetch_candles_daily(ticker, 100)
        trend = calc_trend_for_ticker(df)
        trends[ticker] = trend

    long_cnt = sum(1 for t in trends.values() if t == 'бычий')
    short_cnt = sum(1 for t in trends.values() if t == 'медвежий')
    side_cnt = sum(1 for t in trends.values() if t == 'боковик')
    total_cnt = long_cnt + short_cnt + side_cnt

    days_full = get_days_until_full_moon()
    days_new = get_days_until_new_moon()

    if total_cnt == 0:
        txt = f"📊 ЕЖЕДНЕВНАЯ СВОДКА {datetime.now(_msk()).strftime('%d.%m.%Y %H:%M')}\n\n"
        txt += "⚠️ НЕТ ДАННЫХ ОТ MOEX"
        txt += LUNAR_DISCLAIMER
        save_daily_summary(today, txt)
        try:
            await bot.send_message(CHANNEL_ID, txt, parse_mode='HTML')
        except Exception as e:
            logger.exception(f"Ошибка сводки: {e}")
        return

    long_percent = round((long_cnt / total_cnt) * 100, 1)
    short_percent = round((short_cnt / total_cnt) * 100, 1)

    if short_cnt > long_cnt:
        predominance = "МЕДВЕЖИЙ (SHORT)"
    elif long_cnt > short_cnt:
        predominance = "БЫЧИЙ (LONG)"
    else:
        predominance = "НЕЙТРАЛЬНЫЙ"

    txt = f"📊 ЕЖЕДНЕВНАЯ СВОДКА {datetime.now(_msk()).strftime('%d.%m.%Y %H:%M')}\n\n"
    txt += f"🌙 Фаза Луны: {ph.upper()}\n"
    if nxt_full:
        txt += f"🌕 Полнолуние: {nxt_full.strftime('%d.%m.%Y')}"
        if days_full is not None:
            txt += f" (через {days_full} дн.)\n"
        else:
            txt += "\n"
    if nxt_new:
        txt += f"🌑 Новолуние: {nxt_new.strftime('%d.%m.%Y')}"
        if days_new is not None:
            txt += f" (через {days_new} дн.)\n"
        else:
            txt += "\n"

    txt += f"\n📊 ТРЕНД (17 активов)\n"
    txt += f"🟢 LONG: {long_cnt} ({long_percent}%)\n"
    txt += f"🔴 SHORT: {short_cnt} ({short_percent}%)\n"
    txt += f"⚪ БОКОВИК: {side_cnt}\n"
    txt += f"\n📈 ПРЕОБЛАДАНИЕ: {predominance}"
    txt += LUNAR_DISCLAIMER

    save_daily_summary(today, txt)
    try:
        await bot.send_message(CHANNEL_ID, txt, parse_mode='HTML')
    except Exception as e:
        logger.exception(f"Ошибка сводки: {e}")


async def daily_loop():
    while True:
        now = datetime.now(_msk())
        if now.hour == 10 and now.minute < 5:
            await daily_lunar_summary()
            clean_old_trades(30)
        await asyncio.sleep(60)


async def hourly_signals_loop():
    await asyncio.sleep(10)
    last_run_hour = None
    while True:
        now = datetime.now(_msk())
        if 10 <= now.hour <= 22 and now.minute < 3 and last_run_hour != now.hour:
            try:
                await process_all_tickers()
            except Exception as e:
                logger.exception(f"Ошибка hourly: {e}")
            last_run_hour = now.hour
        await asyncio.sleep(60)


# === НАСТРОЙКА БОТА ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 Фазы Луны"), KeyboardButton(text="📊 Информация")],
        [KeyboardButton(text="📈 Сравнение"), KeyboardButton(text="📋 Тикеры")],
        [KeyboardButton(text="🚨 Срочный срез")],
    ],
    resize_keyboard=True
)


@dp.message_handler(commands=['start'])
async def start_cmd(m):
    await m.answer(
        "📊 АНАЛИТИК\n\n"
        f"🔹 АВТО-СИГНАЛЫ (17 активов)\n"
        f"   Каждый час с 10:00 до 22:00\n"
        f"   Стратегия: MA10/MA30 + ADX\n"
        f"   Стоп 6% | Тейк 12%\n"
        f"   Макс. позиций: {MAX_CONCURRENT_POSITIONS}\n"
        f"   Лимит убытка дня: {STRATEGY['DAILY_LOSS_LIMIT']*100:.0f}%\n\n"
        "🔹 ЛУННАЯ СТРАТЕГИЯ\n"
        "   Ежедневная сводка в 10:00\n\n"
        "🔹 КНОПКИ:\n"
        "   🌙 Фазы Луны\n"
        "   📊 Информация — данные + история\n"
        "   📈 Сравнение — аналитика 1д/1н/1м\n"
        "   📋 Тикеры — список\n"
        "   🚨 Срочный срез — моментальный анализ\n\n"
        "🔹 /luna — лунная стратегия"
        + DISCLAIMER,
        reply_markup=keyboard, parse_mode='HTML')


@dp.message_handler(commands=['luna'])
async def luna_cmd(m):
    signal_type, full_date, next_full, next_new = await get_lunar_signal()
    now = datetime.now(_msk())
    ph, _, _ = get_lunar_info()

    new_moons = [_parse_lunar_datetime(d, t) for d, t in LUNAR_PHASES["new_moons"]]
    last_new = max([d for d in new_moons if d <= now], default=None)
    lunar_day = (now - last_new).days if last_new else 0

    msg = f"🌙 {ph.upper()} ({lunar_day} день)\n"
    msg += f"📅 {now.strftime('%d.%m.%Y')}\n\n"

    if signal_type == "full_today":
        msg += "🔥 Полнолуние сегодня"
    elif signal_type == "prepare":
        days = (next_full - now).days
        msg += f"✅ За {days} дн. до полнолуния"
    elif signal_type == "hold":
        days_after = (now - full_date).days
        msg += f"✅ {days_after} дн. после полнолуния"
    elif signal_type == "new_today":
        msg += "🌑 Новолуние сегодня"
    else:
        msg += "⏸ Вне зоны сигналов"

    if next_full:
        days_full = (next_full - now).days
        msg += f"\n\n🌕 След. полнолуние: {next_full.strftime('%d.%m.%Y')} (через {days_full} дн.)"
    if next_new:
        days_new = (next_new - now).days
        msg += f"\n🌑 След. новолуние: {next_new.strftime('%d.%m.%Y')} (через {days_new} дн.)"

    msg += LUNAR_DISCLAIMER
    await m.answer(msg, parse_mode='HTML')


@dp.message_handler(lambda msg: msg.text == "🌙 Фазы Луны")
async def btn_lunar(m):
    ph, next_full, next_new = get_lunar_info()
    now = datetime.now(_msk())
    days_full = get_days_until_full_moon()
    days_new = get_days_until_new_moon()

    trends = await get_all_trends()
    long_cnt = sum(1 for d in trends.values() if d['trend'] == 'бычий')
    short_cnt = sum(1 for d in trends.values() if d['trend'] == 'медвежий')
    side_cnt = sum(1 for d in trends.values() if d['trend'] == 'боковик')
    total_cnt = long_cnt + short_cnt + side_cnt

    txt = f"🌙 {ph.upper()}\n📅 {now.strftime('%d.%m.%Y')}"

    if next_full:
        txt += f"\n\n🌕 Полнолуние: {next_full.strftime('%d.%m.%Y %H:%M')}"
        if days_full is not None:
            txt += f"\n   ⏳ Через {days_full} дн."
    if next_new:
        txt += f"\n\n🌑 Новолуние: {next_new.strftime('%d.%m.%Y %H:%M')}"
        if days_new is not None:
            txt += f"\n   ⏳ Через {days_new} дн."

    if total_cnt > 0:
        long_percent = round((long_cnt / total_cnt) * 100, 1)
        short_percent = round((short_cnt / total_cnt) * 100, 1)
        txt += f"\n\n📊 ТРЕНД\n"
        txt += f"🟢 LONG: {long_cnt} ({long_percent}%)\n"
        txt += f"🔴 SHORT: {short_cnt} ({short_percent}%)\n"
        txt += f"⚪ БОКОВИК: {side_cnt}"
    else:
        txt += "\n\n⚠️ НЕТ ДАННЫХ"

    txt += LUNAR_DISCLAIMER
    await m.answer(txt, parse_mode='HTML')


@dp.message_handler(lambda msg: msg.text == "📊 Информация")
async def btn_info(m):
    await m.answer("📊 Загружаю данные по 17 активам...")

    all_info = []
    for ticker in ALL_TICKERS:
        info_msg, error = await get_asset_info(ticker)
        if info_msg:
            all_info.append(info_msg)
        await asyncio.sleep(0.1)

    if all_info:
        full_msg = "\n\n".join(all_info) + DISCLAIMER
        if len(full_msg) > 4000:
            parts = []
            current_part = ""
            for info in all_info:
                if len(current_part) + len(info) + 2 > 4000:
                    parts.append(current_part)
                    current_part = info
                else:
                    current_part = (current_part + "\n\n" + info) if current_part else info
            if current_part:
                parts.append(current_part)
            for part in parts:
                await m.answer(part, parse_mode='HTML')
        else:
            await m.answer(full_msg, parse_mode='HTML')
    else:
        await m.answer("⚠️ Нет данных от MOEX" + DISCLAIMER)


@dp.message_handler(lambda msg: msg.text == "📈 Сравнение")
async def btn_comparison(m):
    await m.answer("📈 Собираю аналитику...\n⏳ 30-60 секунд")
    try:
        results = await get_comparison_analytics()
        report = format_comparison_report(results)
        if len(report) > 4000:
            parts = []
            current = ""
            for line in report.split('\n'):
                if len(current) + len(line) + 1 > 3900:
                    parts.append(current)
                    current = line + '\n'
                else:
                    current += line + '\n'
            if current:
                parts.append(current)
            for part in parts:
                await m.answer(part, parse_mode='HTML')
        else:
            await m.answer(report, parse_mode='HTML')
    except Exception as e:
        logger.exception(f"Ошибка аналитики: {e}")
        await m.answer("⚠️ Ошибка")


@dp.message_handler(lambda msg: msg.text == "📋 Тикеры")
async def btn_tickers(m):
    await m.answer(get_tickers_list_text(), parse_mode='HTML')


@dp.message_handler(lambda msg: msg.text == "🚨 Срочный срез")
async def btn_emergency_snapshot(m):
    await m.answer("🚨 Срочный срез...")

    signals = []
    for ticker in ALL_TICKERS:
        if ticker in positions and positions[ticker].get('type') is not None:
            continue
        df = await data_fetcher.fetch_candles_daily(ticker, 100)
        price = await data_fetcher.get_price(ticker)
        if df is None or price is None or price <= 0:
            continue
        signal, data, _ = get_signal_for_ticker(ticker, df, price)
        if signal and data and data.get('price'):
            signals.append({'ticker': ticker, 'signal': signal, 'data': data, 'adx': data['adx']})

    now = datetime.now(_msk())
    msg = f"🚨 СРЕЗ {now.strftime('%H:%M:%S')}\n{'═' * 35}\n\n"

    if signals:
        signals.sort(key=lambda x: x['adx'] if x['adx'] else 0, reverse=True)
        msg += f"📊 СИГНАЛЫ ({len(signals)}):\n\n"
        for s in signals:
            d = s['data']
            emoji = "🟢" if s['signal'] == 'LONG' else "🔴"
            msg += f"{emoji} {d['name']} ({s['ticker']}) | {s['signal']} | ADX {d['adx']}\n"
            msg += f"   Цена: {d['price']:.2f} | 🛑 {d['stop']:.2f} | 🎯 {d['target']:.2f}\n\n"
    else:
        msg += "⚪ СИГНАЛОВ НЕТ\n\n"

    active = {t: p for t, p in positions.items() if p.get('type')}
    if active:
        msg += f"\n📌 АКТИВНЫЕ ПОЗИЦИИ ({len(active)}/{MAX_CONCURRENT_POSITIONS}):\n"
        for ticker, pos in active.items():
            current_price = await data_fetcher.get_price(ticker)
            if current_price:
                pos_type = pos['type']
                entry = pos['entry_price']
                if pos_type == 'long':
                    pnl = (current_price - entry) / entry * 100
                else:
                    pnl = (entry - current_price) / entry * 100
                msg += f"   • {ticker} {pos_type.upper()}: {entry:.2f} → {current_price:.2f} ({pnl:+.2f}%)\n"

    if daily_pnl != 0:
        msg += f"\n📊 P&L дня: {'+' if daily_pnl >= 0 else ''}{daily_pnl:.2f}%"
    if daily_trade_blocked:
        msg += f"\n⛔ ТОРГОВЛЯ ЗАБЛОКИРОВАНА (DAILY_LOSS_LIMIT)"

    msg += DISCLAIMER
    await m.answer(msg, parse_mode='HTML')


# === ЗАПУСК ===
async def main():
    init_db()
    load_state()
    asyncio.create_task(data_fetcher.healthcheck_moex())

    tasks = [
        asyncio.create_task(daily_loop()),
        asyncio.create_task(lunar_notify()),
        asyncio.create_task(hourly_signals_loop()),
    ]

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("📢 Останавливаю...")
        save_state()
        stop_event.set()

    try:
        loop.add_signal_handler(signal.SIGTERM, signal_handler)
        loop.add_signal_handler(signal.SIGINT, signal_handler)
    except NotImplementedError:
        pass

    logger.info("🚀 Бот запущен")

    async def run_polling():
        try:
            await dp.start_polling()
        except Exception as e:
            logger.exception(f"Ошибка polling: {e}")
        finally:
            stop_event.set()

    polling_task = asyncio.create_task(run_polling())
    await stop_event.wait()

    try:
        await dp.stop_polling()
    except Exception as e:
        logger.exception(f"Ошибка stop_polling: {e}")

    polling_task.cancel()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    save_state()

    if bot:
        try:
            await bot.close()
        except Exception as e:
            logger.exception(f"Ошибка закрытия: {e}")

    logger.info("✅ Бот остановлен")


async def run_bot_with_retry():
    attempt = 0
    max_attempts = 5
    while attempt < max_attempts:
        try:
            await main()
            break
        except Exception as e:
            attempt += 1
            logger.exception(f"❌ Бот упал ({attempt}/{max_attempts}): {e}")
            if attempt >= max_attempts:
                logger.critical("❌ Исчерпаны попытки")
                break
            await asyncio.sleep(min(10 * attempt, 60))


if __name__ == "__main__":
    print("=" * 55)
    print("АНАЛИТИК | ВЕРСИЯ P1-FINAL")
    print(f"• MAX_CONCURRENT_POSITIONS = {MAX_CONCURRENT_POSITIONS}")
    print(f"• DAILY_LOSS_LIMIT = {STRATEGY['DAILY_LOSS_LIMIT']*100:.0f}%")
    print("• daily_trade_blocked — break из цикла после закрытия")
    print("• healthcheck_moex — moex_last_success + critical через 30 мин")
    print("• save_state в reset_daily_pnl — под lock")
    print("• load_state — по позициям, не сбрасывает всё")
    print("• count_open_positions — читаемо через флаг")
    print("=" * 55)

    asyncio.run(run_bot_with_retry())
