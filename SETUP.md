# Setup guide

Full walkthrough to get the Cineville watchlist notifier running. No prior
server experience assumed.

## 1. Create a Telegram bot

1. Open Telegram, search for **@BotFather**, and start a chat.
2. Send `/newbot`, give it a name and a username (must end in `bot`).
3. You'll get a **bot token** back (looks like `123456789:AAF...`). Save it.
4. Search for **@userinfobot**, start a chat with it — it replies with your
   own **chat ID** (a number).
5. Send your new bot a message (e.g. "hi") — Telegram only delivers
   messages to a bot after you've messaged it first.

## 2. Get your Cineville session cookie

The watchlist is only available once logged in, and Cineville has no public
login API, so the script reuses a session cookie from your browser.

1. Log in at **https://cineville.nl** in a normal browser (Chrome, Edge,
   Firefox).
2. Open Developer Tools (F12) → **Network** tab.
3. Reload the page while logged in. Find the request named **`session`**
   (full URL: `https://cineville.nl/api/auth/session`).
4. Right-click it → **Copy** → **Copy as cURL**. Don't manually
   select-and-copy the displayed header value — both Chrome and Firefox
   truncate long values in the UI with a literal `…` character, which
   silently corrupts the cookie if you copy that way. "Copy as cURL" always
   gives you the full, untruncated value.
5. Paste that somewhere and pull out the full value after `cookie: ` (or
   `-H 'Cookie: ...'` depending on browser/OS).
6. Put that whole string as `cineville_cookie` in `config.json` (see step 4).

Cineville sessions tend to stay valid for roughly a month. The script warns
you a few days before yours expires (`cookie_warning_days` in the config),
so you don't need to repeat this often.

## 3. (Optional) IMDb / Rotten Tomatoes ratings via OMDb

1. Go to **https://www.omdbapi.com/apikey.aspx**, choose the free "FREE!"
   tier (1,000 requests/day), and enter your email.
2. You'll receive the key by email (check spam too).
3. If the signup form asks for a reason/use case, something like this
   works: *"Personal, non-commercial project. I get a weekly list of films
   playing at cinemas near me and want to check the IMDb/Rotten Tomatoes
   rating for the small number of titles that match my personal watchlist.
   Expected usage is a few lookups per week."*
4. Put the key in `config.json` as `omdb_api_key`.

Without a key, everything still works, just without the ratings line.
Note OMDb is matched by title, and Cineville titles sometimes have a suffix
like "(re-release)" — the script retries without it automatically, but an
occasional mismatch isn't impossible.

## 4. Configuration file

Copy `config.example.json` to `config.json` and fill it in:

```json
{
  "cineville_cookie": "... (step 2) ...",
  "telegram_bot_token": "... (step 1) ...",
  "telegram_chat_id": "... (step 1) ...",
  "cities": ["amsterdam", "amstelveen"],
  "lookback_weeks": 4,
  "history_file": "history.json",
  "bot_offset_file": "bot_offset.json",
  "omdb_api_key": "... (step 3, optional) ...",
  "cookie_warning_days": 5,
  "language": "nl"
}
```

See the [configuration reference table](README.md#configuration-reference-configjson)
in the README for what each field does. Remember JSON strings need double
quotes around them (`"omdb_api_key": "abc123"`, not `"omdb_api_key": abc123`);
only `null`, `true`, `false`, and numbers don't need quotes.

Sanity-check the file before moving on:

```bash
python3 -c "import json; print(json.load(open('config.json')).keys())"
```

## 5. Server setup

Any machine that can run Python 3.10+ and a weekly cron job works: a
Raspberry Pi, a cheap VPS, a Proxmox LXC container, or even your own
laptop. The steps below assume a Debian/Ubuntu-like Linux system; adjust
package manager commands if you're on something else.

```bash
apt update && apt install -y python3 python3-venv python3-pip
timedatectl set-timezone Europe/Amsterdam   # important for the weekly schedule to line up correctly

mkdir -p /opt/cineville-notify
cd /opt/cineville-notify
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Copy `cineville_notify.py`, `requirements.txt`, and your filled-in
`config.json` into `/opt/cineville-notify/` (e.g. via `scp`, WinSCP, or
`rsync`).

Test it manually:

```bash
cd /opt/cineville-notify
./venv/bin/python cineville_notify.py config.json
```

On a successful first run it creates `history.json` and deliberately sends
**no** Telegram notification yet — there's nothing to compare against on
the first run. From the second week onward you'll get real notifications.

## 6. Weekly cron job

```bash
crontab -e
```

Add:

```
0 8 * * 2 cd /opt/cineville-notify && ./venv/bin/python cineville_notify.py config.json >> run.log 2>&1
```

This runs every Tuesday at 08:00 local time — comfortably after Cineville
publishes the new schedule on Monday evening. Check `run.log` occasionally
if you want to confirm it's still running.

## 7. Bot commands (optional)

`cineville_bot_listener.py` lets you control the bot via Telegram itself,
without logging into the server:

- `/status` — current settings and last checked week
- `/setweeks <n>` — change how many weeks of history to keep (1-52)
- `/setcookie <value>` — replace the session cookie (see step 2 for how to
  get a fresh one)
- `/setlanguage <nl|en>` — switch notification language
- `/checknow` — run a check immediately
- `/help` — list commands

Only messages from your own `telegram_chat_id` (from `config.json`) are
accepted; everything else is ignored.

This needs to run continuously (a systemd service, not a cron job). Copy
`cineville_bot_listener.py` to `/opt/cineville-notify/` too, then create
`/etc/systemd/system/cineville-bot.service`:

```ini
[Unit]
Description=Cineville Telegram bot listener
After=network.target

[Service]
WorkingDirectory=/opt/cineville-notify
ExecStart=/opt/cineville-notify/venv/bin/python cineville_bot_listener.py config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
systemctl daemon-reload
systemctl enable --now cineville-bot.service
```

Test with `/help` in your Telegram chat with the bot. View logs with:

```bash
journalctl -u cineville-bot.service -f
```

## Troubleshooting

- **`UnicodeEncodeError: 'latin-1' codec can't encode character '…'`**
  — your cookie contains a `…` character from a truncated copy-paste. Redo
  step 2 using "Copy as cURL".
- **`json.decoder.JSONDecodeError`** — usually a missing comma or a stray
  character in `config.json`. Run the sanity-check command from step 4 to
  pinpoint it.
- **`ERROR Geen geldig accessToken/user_id`** in the logs, or a Telegram
  message saying your session expired — your cookie is invalid or expired;
  redo step 2.
- **No ratings ever show up** — check that `omdb_api_key` is set and valid
  by querying `https://www.omdbapi.com/?apikey=YOUR_KEY&t=Some+Film+Title`
  directly.
