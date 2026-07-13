"""
Luistert continu naar Telegram-berichten van jouw eigen chat, zodat je
config.json (cookie, aantal weken historie, taal) kunt aanpassen of een
controle handmatig kunt starten zonder in te loggen op de server.

Draait als losse systemd-service naast de cron-job van cineville_notify.py.
"""
import json
import logging
import pathlib
import sys
import time

import requests

import cineville_notify as cn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cineville_bot_listener")

BOT_MESSAGES = {
    "nl": {
        "help": (
            "Beschikbare commando's:\n"
            "/status - toon huidige instellingen\n"
            "/setweeks <getal> - stel het aantal weken historie in (1-52)\n"
            "/setcookie <waarde> - vervang de Cineville-sessiecookie\n"
            "/setlanguage <nl|en> - taal van de meldingen\n"
            "/checknow - voer meteen een controle uit\n"
            "/help - deze lijst"
        ),
        "status": (
            "Steden: {cities}\nTaal: {language}\nWeken historie: {weeks}\n"
            "Cookie ingesteld: {cookie_set}\nLaatst gecontroleerde week: {last_week}"
        ),
        "cookie_yes": "ja",
        "cookie_no": "nee",
        "no_week_yet": "nog geen",
        "setweeks_usage": "Gebruik: /setweeks <getal tussen 1 en 52>",
        "setweeks_confirm": "Aantal weken historie is nu {n}.",
        "setcookie_usage": "Gebruik: /setcookie <plak hier de cookie-waarde>",
        "setcookie_confirm": "Cookie bijgewerkt.",
        "setlanguage_usage": "Gebruik: /setlanguage nl of /setlanguage en",
        "setlanguage_confirm": "Taal ingesteld op Nederlands.",
        "checknow_started": "Controle gestart...",
        "checknow_done": "Controle klaar.",
        "checknow_failed": "Controle mislukt: {error}",
        "unknown_command": "Onbekend commando. Stuur /help voor een overzicht.",
    },
    "en": {
        "help": (
            "Available commands:\n"
            "/status - show current settings\n"
            "/setweeks <number> - set weeks of history to keep (1-52)\n"
            "/setcookie <value> - replace the Cineville session cookie\n"
            "/setlanguage <nl|en> - notification language\n"
            "/checknow - run a check immediately\n"
            "/help - this list"
        ),
        "status": (
            "Cities: {cities}\nLanguage: {language}\nWeeks of history: {weeks}\n"
            "Cookie set: {cookie_set}\nLast checked week: {last_week}"
        ),
        "cookie_yes": "yes",
        "cookie_no": "no",
        "no_week_yet": "none yet",
        "setweeks_usage": "Usage: /setweeks <number between 1 and 52>",
        "setweeks_confirm": "Weeks of history is now {n}.",
        "setcookie_usage": "Usage: /setcookie <paste the cookie value here>",
        "setcookie_confirm": "Cookie updated.",
        "setlanguage_usage": "Usage: /setlanguage nl or /setlanguage en",
        "setlanguage_confirm": "Language set to English.",
        "checknow_started": "Check started...",
        "checknow_done": "Check done.",
        "checknow_failed": "Check failed: {error}",
        "unknown_command": "Unknown command. Send /help for an overview.",
    },
}


def bt(lang, key, **kwargs):
    strings = BOT_MESSAGES.get(lang, BOT_MESSAGES["nl"])
    return strings[key].format(**kwargs)


def load_config(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def save_config(path, config):
    tmp = pathlib.Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    tmp.replace(path)


def send(bot_token, chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    ).raise_for_status()


def load_offset(path):
    p = pathlib.Path(path)
    if not p.exists():
        return 0
    return json.loads(p.read_text(encoding="utf-8")).get("offset", 0)


def save_offset(path, offset):
    pathlib.Path(path).write_text(json.dumps({"offset": offset}), encoding="utf-8")


def handle_command(text, config, config_path):
    bot_token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    language = config.get("language", "nl")
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/help", "/start"):
        send(bot_token, chat_id, bt(language, "help"))

    elif cmd == "/status":
        history = cn.load_history(config["history_file"])
        weeks_known = sorted(history.keys())
        last_week = weeks_known[-1] if weeks_known else bt(language, "no_week_yet")
        cookie_set = bt(language, "cookie_yes" if config.get("cineville_cookie") else "cookie_no")
        send(
            bot_token, chat_id,
            bt(
                language, "status",
                cities=", ".join(config["cities"]), language=language,
                weeks=config.get("lookback_weeks", 4), cookie_set=cookie_set,
                last_week=last_week,
            ),
        )

    elif cmd == "/setweeks":
        try:
            n = int(arg)
            if not (1 <= n <= 52):
                raise ValueError
        except ValueError:
            send(bot_token, chat_id, bt(language, "setweeks_usage"))
            return
        config["lookback_weeks"] = n
        save_config(config_path, config)
        send(bot_token, chat_id, bt(language, "setweeks_confirm", n=n))

    elif cmd == "/setcookie":
        if not arg:
            send(bot_token, chat_id, bt(language, "setcookie_usage"))
            return
        config["cineville_cookie"] = arg
        save_config(config_path, config)
        send(bot_token, chat_id, bt(language, "setcookie_confirm"))

    elif cmd == "/setlanguage":
        new_language = arg.strip().lower()
        if new_language not in ("nl", "en"):
            send(bot_token, chat_id, bt(language, "setlanguage_usage"))
            return
        config["language"] = new_language
        save_config(config_path, config)
        send(bot_token, chat_id, bt(new_language, "setlanguage_confirm"))

    elif cmd == "/checknow":
        send(bot_token, chat_id, bt(language, "checknow_started"))
        try:
            cn.run_check(config)
            send(bot_token, chat_id, bt(language, "checknow_done"))
        except Exception as e:
            log.exception("Fout tijdens handmatige controle")
            send(bot_token, chat_id, bt(language, "checknow_failed", error=e))

    else:
        send(bot_token, chat_id, bt(language, "unknown_command"))


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    config = load_config(config_path)
    offset_path = config.get("bot_offset_file", "bot_offset.json")
    offset = load_offset(offset_path)
    bot_token = config["telegram_bot_token"]
    allowed_chat_id = str(config["telegram_chat_id"])

    log.info("Bot-listener gestart")
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{bot_token}/getUpdates",
                params={"offset": offset + 1, "timeout": 30},
                timeout=40,
            )
            r.raise_for_status()
            updates = r.json().get("result", [])
        except requests.RequestException:
            log.exception("Kon updates niet ophalen, probeer over 10s opnieuw")
            time.sleep(10)
            continue

        for update in updates:
            offset = update["update_id"]
            message = update.get("message") or {}
            text = message.get("text")
            msg_chat_id = str(message.get("chat", {}).get("id", ""))
            if not text:
                continue
            if msg_chat_id != allowed_chat_id:
                log.warning("Bericht van niet-toegestane chat_id %s genegeerd", msg_chat_id)
                continue
            config = load_config(config_path)  # herlaad, kan net gewijzigd zijn
            try:
                handle_command(text, config, config_path)
            except Exception:
                log.exception("Fout bij verwerken van commando: %s", text)

        if updates:
            save_offset(offset_path, offset)


if __name__ == "__main__":
    main()
