"""
Checkt wekelijks of een film van je Cineville-watchlist, die de afgelopen
weken niet draaide, weer in de agenda staat in de geconfigureerde steden.
Stuurt een Telegram-bericht bij een match.
"""
import datetime
import json
import logging
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

import requests

API = "https://api.cineville.nl"
AUTH_SESSION_URL = "https://cineville.nl/api/auth/session"
OMDB_URL = "https://www.omdbapi.com/"
AMS_TZ = ZoneInfo("Europe/Amsterdam")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cineville_notify")

FILM_LOCALES = {"nl": "nl-NL", "en": "en-GB"}

MESSAGES = {
    "nl": {
        "notify": "🎬 {title_html} staat op je watchlist en is deze week te zien in {cities}!",
        "rating_imdb": "⭐ IMDb {value}/10",
        "rating_rt": "🍅 Rotten Tomatoes {value}",
        "summary_first_run": (
            "✅ Cineville-check gedaan (eerste run). Agenda komende week: "
            "{week_count} films in {cities}, waarvan {matches} op je watchlist. "
            "Geschiedenis is gevuld; vanaf volgende week krijg je meldingen bij "
            "nieuwe treffers."
        ),
        "summary": (
            "✅ Cineville-check gedaan: {matches} watchlist-film(s) deze week in "
            "{cities}, waarvan {new} nieuw beschikbaar."
        ),
        "cookie_expired": (
            "⚠️ Je Cineville-sessie is verlopen. Log opnieuw in via cineville.nl "
            "en ververs de 'cineville_cookie' in config.json."
        ),
        "cookie_expiring": (
            "⏳ Let op: je Cineville-sessie verloopt over {days} dag(en) (rond "
            "{date}). Log opnieuw in via cineville.nl en ververs de cookie "
            "(/setcookie of config.json) voordat de meldingen stoppen."
        ),
        "network_error": "⚠️ Cineville-check mislukt (netwerkfout): {error}",
        "unexpected_error": "⚠️ Cineville-check mislukt: {error}",
        "unknown_title": "Onbekende titel",
    },
    "en": {
        "notify": "🎬 {title_html} is on your watchlist and is showing this week in {cities}!",
        "rating_imdb": "⭐ IMDb {value}/10",
        "rating_rt": "🍅 Rotten Tomatoes {value}",
        "summary_first_run": (
            "✅ Cineville check done (first run). Agenda for next week: "
            "{week_count} films in {cities}, of which {matches} are on your "
            "watchlist. History has been seeded; from next week you'll get "
            "notifications for new matches."
        ),
        "summary": (
            "✅ Cineville check done: {matches} watchlist film(s) this week in "
            "{cities}, of which {new} newly available."
        ),
        "cookie_expired": (
            "⚠️ Your Cineville session has expired. Log in again via cineville.nl "
            "and refresh 'cineville_cookie' in config.json."
        ),
        "cookie_expiring": (
            "⏳ Heads up: your Cineville session expires in {days} day(s) (around "
            "{date}). Log in again via cineville.nl and refresh the cookie "
            "(/setcookie or config.json) before notifications stop."
        ),
        "network_error": "⚠️ Cineville check failed (network error): {error}",
        "unexpected_error": "⚠️ Cineville check failed: {error}",
        "unknown_title": "Unknown title",
    },
}


def t(lang, key, **kwargs):
    strings = MESSAGES.get(lang, MESSAGES["nl"])
    return strings[key].format(**kwargs)


def load_config(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def send_telegram(token, chat_id, text):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    r.raise_for_status()


def get_access_token(cookie):
    """Ruilt je opgeslagen Cineville-sessiecookie in voor een vers API-token."""
    r = requests.get(AUTH_SESSION_URL, headers={"Cookie": cookie}, timeout=15)
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token")
    user_id = (data.get("user") or {}).get("id")
    expires = data.get("expires")
    return token, user_id, expires


def check_cookie_expiry(expires_str, warning_days, bot_token, chat_id, language):
    """Waarschuwt alvast als de sessiecookie binnen `warning_days` verloopt,
    in plaats van pas te melden zodra hij al verlopen is."""
    if not expires_str:
        return
    try:
        expires_dt = datetime.datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
    except ValueError:
        return
    days_left = (expires_dt - datetime.datetime.now(datetime.timezone.utc)).days
    if days_left <= warning_days:
        send_telegram(
            bot_token, chat_id,
            t(
                language, "cookie_expiring",
                days=days_left,
                date=expires_dt.astimezone(AMS_TZ).strftime("%d-%m-%Y"),
            ),
        )


def extract_production(item):
    """Watchlist-items kunnen het productionId/title op verschillende plekken
    hebben staan; dit vangt de bekende varianten af."""
    pid = item.get("productionId")
    title = item.get("productionTitle")
    prod = item.get("production") or item.get("_embedded", {}).get("production")
    if isinstance(prod, dict):
        pid = pid or prod.get("id")
        title = title or prod.get("title")
    return pid, title


def fetch_watchlist(user_id, token):
    ids_to_titles = {}
    url = f"{API}/users/{user_id}/watchlist?page[limit]=100"
    headers = {"Authorization": f"Bearer {token}"}
    while url:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        embedded = data.get("_embedded", {})
        items = next((v for v in embedded.values() if isinstance(v, list)), [])
        for item in items:
            pid, title = extract_production(item)
            if pid:
                ids_to_titles[pid] = title
        next_href = data.get("_links", {}).get("next", {}).get("href")
        url = f"{API}{next_href}" if next_href else None
    return ids_to_titles


def fetch_venue_ids(cities):
    params = {"isHidden[eq]": "false", "page[limit]": "1000"}
    for i, city in enumerate(cities):
        params[f"collection[cities][in][{i}]"] = city
    r = requests.get(f"{API}/venues", params=params, timeout=15)
    r.raise_for_status()
    return [v["id"] for v in r.json()["_embedded"]["venues"]]


def get_week_window(today=None):
    """Cineville-weken lopen donderdag t/m woensdag. Geeft de eerstvolgende
    (of lopende, als vandaag donderdag is) week terug als UTC-tijdstippen."""
    today = today or datetime.date.today()
    days_ahead = (3 - today.weekday()) % 7  # maandag=0 ... donderdag=3
    start = today + datetime.timedelta(days=days_ahead)
    end = start + datetime.timedelta(days=7)
    start_dt = datetime.datetime.combine(start, datetime.time(0, 0), AMS_TZ)
    end_dt = datetime.datetime.combine(end, datetime.time(0, 0), AMS_TZ)
    return start_dt.astimezone(datetime.timezone.utc), end_dt.astimezone(datetime.timezone.utc)


def fetch_week_production_ids(venue_ids, start_utc, end_utc):
    body = {
        "venueId": {"in": venue_ids},
        "startDate": {
            "gte": start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "lt": end_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        },
    }
    ids = set()
    url = f"{API}/events/search"
    while url:
        r = requests.post(url, json=body, timeout=20)
        r.raise_for_status()
        data = r.json()
        for ev in data.get("_embedded", {}).get("events", []):
            if ev.get("productionId"):
                ids.add(ev["productionId"])
        next_href = data.get("_links", {}).get("next", {}).get("href")
        url = f"{API}{next_href}" if next_href else None
    return ids


def fetch_production(pid):
    """Titel, slug (voor de Cineville-link) en releasejaar (voor OMDb-matching)."""
    r = requests.get(f"{API}/productions/{pid}", timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    return {
        "title": data.get("title"),
        "slug": data.get("slug"),
        "release_year": (data.get("attributes") or {}).get("releaseYear"),
    }


def fetch_ratings(title, release_year, api_key):
    """IMDb/Rotten Tomatoes-score via OMDb. Geeft None terug als er geen
    api_key is ingesteld, of als OMDb de titel niet kan vinden."""
    if not api_key or not title:
        return None

    def query(t):
        params = {"apikey": api_key, "t": t}
        if release_year:
            params["y"] = release_year
        try:
            r = requests.get(OMDB_URL, params=params, timeout=10)
            data = r.json()
        except (requests.RequestException, ValueError):
            return None
        return data if data.get("Response") == "True" else None

    data = query(title)
    if not data:
        # Cineville-titels hebben soms een toevoeging, bv. "Grease (re-release)"
        stripped = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
        if stripped != title:
            data = query(stripped)
    if not data:
        return None

    imdb = data.get("imdbRating")
    rt = next(
        (r["Value"] for r in data.get("Ratings", []) if r.get("Source") == "Rotten Tomatoes"),
        None,
    )
    return {"imdb": imdb if imdb and imdb != "N/A" else None, "rt": rt}


def build_summary_message(first_run, matches_count, newly_count, week_film_count, cities, language):
    cities_str = ", ".join(cities)
    if first_run:
        return t(
            language, "summary_first_run",
            week_count=week_film_count, cities=cities_str, matches=matches_count,
        )
    return t(language, "summary", matches=matches_count, cities=cities_str, new=newly_count)


def build_message(title, slug, cities, ratings, language):
    if slug:
        locale = FILM_LOCALES.get(language, "nl-NL")
        title_html = f'<a href="https://cineville.nl/{locale}/films/{slug}"><b>{title}</b></a>'
    else:
        title_html = f"<b>{title}</b>"
    lines = [t(language, "notify", title_html=title_html, cities=", ".join(cities))]
    if ratings:
        bits = []
        if ratings.get("imdb"):
            bits.append(t(language, "rating_imdb", value=ratings["imdb"]))
        if ratings.get("rt"):
            bits.append(t(language, "rating_rt", value=ratings["rt"]))
        if bits:
            lines.append(" · ".join(bits))
    return "\n".join(lines)


def load_history(path):
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_history(path, history):
    pathlib.Path(path).write_text(json.dumps(history, indent=2), encoding="utf-8")


def prune_history(history, lookback_weeks, cutoff_date):
    for week_start in list(history.keys()):
        if datetime.date.fromisoformat(week_start) < cutoff_date:
            del history[week_start]


def run_check(config):
    bot_token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    language = config.get("language", "nl")

    try:
        token, user_id, expires = get_access_token(config["cineville_cookie"])
    except requests.RequestException as e:
        log.exception("Kon Cineville-sessie niet verversen")
        send_telegram(bot_token, chat_id, t(language, "network_error", error=e))
        return

    if not token or not user_id:
        log.error("Geen geldig accessToken/user_id - cookie is waarschijnlijk verlopen")
        send_telegram(bot_token, chat_id, t(language, "cookie_expired"))
        return

    check_cookie_expiry(expires, config.get("cookie_warning_days", 5), bot_token, chat_id, language)

    try:
        watchlist = fetch_watchlist(user_id, token)
        log.info("Watchlist: %d films", len(watchlist))

        start_utc, end_utc = get_week_window()
        venue_ids = fetch_venue_ids(config["cities"])
        week_ids = fetch_week_production_ids(venue_ids, start_utc, end_utc)
        log.info("Agenda komende week: %d films in %d theaters", len(week_ids), len(venue_ids))

        lookback_weeks = config.get("lookback_weeks", 4)
        current_week_key = start_utc.astimezone(AMS_TZ).date().isoformat()
        cutoff_date = start_utc.astimezone(AMS_TZ).date() - datetime.timedelta(weeks=lookback_weeks)

        history = load_history(config["history_file"])
        first_run = len(history) == 0
        prune_history(history, lookback_weeks, cutoff_date)

        recent_ids = set()
        for ids in history.values():
            recent_ids |= set(ids)

        matches = [pid for pid in watchlist if pid in week_ids]
        newly_available = [pid for pid in matches if pid not in recent_ids]

        if first_run:
            log.info("Eerste run: geschiedenis wordt gevuld zonder notificaties te sturen")
        else:
            omdb_api_key = config.get("omdb_api_key")
            for pid in newly_available:
                details = fetch_production(pid) or {}
                title = details.get("title") or watchlist.get(pid) or t(language, "unknown_title")
                slug = details.get("slug")
                ratings = fetch_ratings(title, details.get("release_year"), omdb_api_key)
                log.info("Nieuw beschikbaar: %s", title)
                send_telegram(
                    bot_token, chat_id,
                    build_message(title, slug, config["cities"], ratings, language),
                )

        history[current_week_key] = sorted(week_ids)
        save_history(config["history_file"], history)

        send_telegram(
            bot_token, chat_id,
            build_summary_message(
                first_run, len(matches), len(newly_available), len(week_ids),
                config["cities"], language,
            ),
        )
    except Exception as e:
        log.exception("Onverwachte fout tijdens de controle")
        send_telegram(bot_token, chat_id, t(language, "unexpected_error", error=e))


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    run_check(load_config(config_path))


if __name__ == "__main__":
    main()
