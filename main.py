import os
import json
import time
import logging
from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparser

import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from telegram import Bot
from telegram.constants import ParseMode

# ----------------- ENV -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL = os.getenv("CHANNEL_USERNAME")  # e.g. @kfkfkjfjfc

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
LEAGUES = os.getenv("LEAGUES", "any")          # "any" => все хоккейные лиги из TheOddsAPI
MARKETS = os.getenv("MARKETS", "h2h,totals")   # "h2h,totals"
ODDS_RANGE = os.getenv("ODDS_RANGE", "1.70-2.50")
POST_TIMES = os.getenv("POST_TIMES", "11:00,18:30")  # МСК слоты, через запятую
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "3"))   # Москва = +3

# ----------------- CONST -----------------
ODDS_BASE = "https://api.the-odds-api.com/v4"
SPORTS_CACHE_FILE = "sports_cache.json"
STATE_FILE = "posted_events.json"  # хранит id уже опубликованных событий для пост-итога

HEADERS = {"User-Agent": "IronHockeyBot/1.0"}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("iron-hockey-auto")

# ----------------- UTILS -----------------
def now_utc():
    return datetime.now(timezone.utc)

def to_msk(dt: datetime) -> datetime:
    return dt + timedelta(hours=TZ_OFFSET)

def fmt_dt(dt: datetime) -> str:
    return to_msk(dt).strftime("%d.%m %H:%M")

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"Failed to save %s: %s", path, e)

def parse_range(text: str):
    lo, hi = text.split("-")
    return float(lo.strip()), float(hi.strip())

ODDS_MIN, ODDS_MAX = parse_range(ODDS_RANGE)

# ----------------- ODDS API -----------------
def fetch_sports():
    """Получить список видов спорта и закешировать"""
    url = f"{ODDS_BASE}/sports/?apiKey={ODDS_API_KEY}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    sports = r.json()
    save_json(SPORTS_CACHE_FILE, sports)
    return sports

def hockey_sports():
    sports = load_json(SPORTS_CACHE_FILE, [])
    if not sports:
        try:
            sports = fetch_sports()
        except Exception as e:
            log.warning("fetch_sports failed: %s", e)
            return []
    # фильтруем хоккей
    hk = [s for s in sports if "icehockey" in (s.get("key","")) or "hockey" in (s.get("title","").lower())]
    return hk

def fetch_odds_for_sport(sport_key: str):
    url = f"{ODDS_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,us,uk",
        "markets": MARKETS,
        "oddsFormat": "decimal",
        "dateFormat": "iso"
    }
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json()

def fetch_scores_for_sport(sport_key: str, days_from=2):
    url = f"{ODDS_BASE}/sports/{sport_key}/scores"
    params = {
        "apiKey": ODDS_API_KEY,
        "daysFrom": str(days_from),
        "dateFormat": "iso"
    }
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json()

# ----------------- PICK LOGIC -----------------
def pick_best_bet(events):
    best = None
    for ev in events:
        try:
            commence = dtparser.isoparse(ev["commence_time"])
        except Exception:
            continue
        if commence < now_utc() or commence > now_utc() + timedelta(hours=36):
            continue

        home = ev.get("home_team","")
        away = ev.get("away_team","")
        bookmakers = ev.get("bookmakers", [])
        for bm in bookmakers:
            for mk in bm.get("markets", []):
                key = mk.get("key")
                outcomes = mk.get("outcomes", [])
                if key == "h2h":
                    for o in outcomes:
                        try:
                            price = float(o.get("price", 0))
                        except Exception:
                            continue
                        if ODDS_MIN <= price <= ODDS_MAX and o.get("name"):
                            cand = {
                                "sport": ev.get("sport_key"),
                                "id": ev.get("id"),
                                "commence_time": ev.get("commence_time"),
                                "home": home, "away": away,
                                "market": "h2h",
                                "selection": o["name"],
                                "line": None,
                                "price": price,
                                "bookmaker": bm.get("title", bm.get("key"))
                            }
                            if (best is None) or (cand["price"] > best["price"]):
                                best = cand
                elif key == "totals":
                    for o in outcomes:
                        try:
                            price = float(o.get("price", 0))
                        except Exception:
                            continue
                        point = o.get("point")
                        name = o.get("name","").lower()
                        if point is None or not name:
                            continue
                        if ODDS_MIN <= price <= ODDS_MAX and name in ("over","under"):
                            cand = {
                                "sport": ev.get("sport_key"),
                                "id": ev.get("id"),
                                "commence_time": ev.get("commence_time"),
                                "home": home, "away": away,
                                "market": "totals",
                                "selection": name,
                                "line": float(point),
                                "price": price,
                                "bookmaker": bm.get("title", bm.get("key"))
                            }
                            if (best is None) or (cand["price"] > best["price"]):
                                best = cand
    return best

def compose_pick_text(pick):
    dt = dtparser.isoparse(pick["commence_time"])
    dt_str = fmt_dt(dt)
    h, a = pick["home"], pick["away"]
    if pick["market"] == "h2h":
        sel = pick["selection"]
        if sel == h:
            sel_text = f"Победа {h}"
        elif sel == a:
            sel_text = f"Победа {a}"
        else:
            sel_text = f"Исход: {sel}"
    else:
        sel = pick["selection"]
        sign = "Больше" if sel == "over" else "Меньше"
        sel_text = f"Тотал: {sign} ({pick['line']})"

    return (
        f"🏒 *ЖЕЛЕЗНЫЙ ХОККЕЙ*\n"
        f"{h} — {a}\n"
        f"🕒 {dt_str} (МСК)\n\n"
        f"**Прогноз:** {sel_text}\n"
        f"**Коэффициент:** {pick['price']:.2f}\n"
        f"Букмекер: {pick['bookmaker']}\n\n"
        f"📌 Учитываем форму, свежесть составов и динамику тоталов за последние игры.\n"
        f"⚠️ Ставки на спорт — это риск. Контент носит информационный характер.\n"
    )

# ----------------- STATE -----------------
def get_state():
    return load_json(STATE_FILE, {"posted": []})

def remember_posted(event_key):
    st = get_state()
    if event_key not in st["posted"]:
        st["posted"].append(event_key)
        save_json(STATE_FILE, st)

def was_posted(event_key):
    st = get_state()
    return event_key in st["posted"]

# ----------------- MAIN -----------------
async def autopost_once(bot: Bot):
    if not ODDS_API_KEY:
        log.error("ODDS_API_KEY is missing")
        return
    sports = hockey_sports()
    if not sports:
        log.warning("No hockey sports available from TheOddsAPI")
        return

    picked = None
    for s in sports:
        key = s.get("key")
        try:
            odds = fetch_odds_for_sport(key)
        except Exception as e:
            log.warning("odds fetch failed for %s: %s", key, e)
            continue
        for ev in odds:
            ev["sport_key"] = key
        cand = pick_best_bet(odds)
        if cand and ((picked is None) or (cand["price"] > picked["price"])):
            picked = cand

    if not picked:
        log.info("No suitable pick in range; skipping this slot.")
        return

    ev_key = f"{picked['sport']}::{picked['id']}::{picked['market']}"
    if was_posted(ev_key):
        log.info("Already posted for %s, skipping", ev_key)
        return

    text = compose_pick_text(picked)
    await bot.send_message(CHANNEL, text, parse_mode=ParseMode.MARKDOWN)
    remember_posted(ev_key)
    log.info("Posted: %s", ev_key)

async def post_results(bot: Bot):
    st = get_state()
    if not st["posted"]:
        return
    sports_set = set([p.split("::")[0] for p in st["posted"]])
    for sport_key in sports_set:
        try:
            scores = fetch_scores_for_sport(sport_key, days_from=2)
        except Exception as e:
            log.warning("scores fetch failed for %s: %s", sport_key, e)
            continue
        for sc in scores:
            ev_id = sc.get("id")
            if not ev_id:
                continue
            for market in ("h2h","totals"):
                ev_key = f"{sport_key}::{ev_id}::{market}"
                if not was_posted(ev_key):
                    continue
                if sc.get("completed"):
                    home = sc.get("home_team","")
                    away = sc.get("away_team","")
                    scores_map = sc.get("scores") or []
                    sh = sa = None
                    for t in scores_map:
                        if t.get("name") == home:
                            sh = t.get("score")
                        elif t.get("name") == away:
                            sa = t.get("score")
                    if sh is not None and sa is not None:
                        text = (
                            "✅ *ИТОГ МАТЧА*\n"
                            f"{home} — {away}\n"
                            f"Счёт: {sh}:{sa}\n\n"
                            "**Если ставка зашла** — двигаемся дальше и закрепляем плюс.\n"
                            "**Если не зашла** — сохраняем холодную голову: на дистанции мы в плюсе.\n"
                            "Следующий прогноз — в ближайшее время."
                        )
                        try:
                            await bot.send_message(CHANNEL, text, parse_mode=ParseMode.MARKDOWN)
                        except Exception as e:
                            log.warning("result post failed: %s", e)

def setup_scheduler(bot: Bot):
    sched = AsyncIOScheduler(timezone=timezone.utc)
    times = [t.strip() for t in POST_TIMES.split(",") if t.strip()]
    for t in times:
        try:
            hh, mm = t.split(":")
            h_utc = (int(hh) - TZ_OFFSET) % 24
            m_utc = int(mm)
            sched.add_job(lambda: bot.loop.create_task(autopost_once(bot)),
                          CronTrigger(hour=h_utc, minute=m_utc))
            log.info("Scheduled autopost at %s MSK -> %02d:%02d UTC", t, h_utc, m_utc)
        except Exception as e:
            log.warning("Bad time '%s': %s", t, e)

    sched.add_job(lambda: bot.loop.create_task(post_results(bot)),
                  CronTrigger(minute="*/30"))
    sched.start()
    return sched

async def main():
    if not BOT_TOKEN or not CHANNEL:
        raise SystemExit("BOT_TOKEN и CHANNEL_USERNAME are required")
    if not ODDS_API_KEY:
        logging.warning("ODDS_API_KEY is missing; autopost will be skipped")

    bot = Bot(BOT_TOKEN)
    setup_scheduler(bot)

    logging.info("Iron Hockey autonomous bot is running…")
    while True:
        time.sleep(10)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
