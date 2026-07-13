# Cineville watchlist-notificatie

Dit scriptje checkt elke week of een film van je Cineville-watchlist, die de
laatste 4 weken niet in Amsterdam/Amstelveen draaide, weer in de agenda staat
— en stuurt je dan een Telegram-berichtje.

Bestanden:
- `cineville_notify.py` — het script
- `config.example.json` — voorbeeldconfig, kopieer naar `config.json` en vul in
- `requirements.txt` — de twee benodigde Python-packages
- `history.json` — wordt door het script zelf aangemaakt, niet zelf invullen

De filmagenda wordt opgehaald via de openbare (niet-officiële) API die
cineville.nl zelf gebruikt — daar hoef je niet voor in te loggen. Alleen voor
je **watchlist** is een login nodig; dat stukje kon ik niet zelf testen (ik
kan/mag niet met jouw account inloggen), dus het is goed mogelijk dat we daar
na de eerste echte run nog een kleine aanpassing aan moeten doen. Stuur me in
dat geval gewoon de foutmelding of de ruwe watchlist-respons.

## 1. Telegram-bot aanmaken

1. Open Telegram, zoek naar **@BotFather** en start een chat.
2. Stuur `/newbot`, geef een naam en een gebruikersnaam (moet eindigen op `bot`).
3. Je krijgt een **bot-token** terug (iets als `123456789:AAF...`). Bewaar die.
4. Zoek naar **@userinfobot**, start een chat, en stuur een bericht — je krijgt
   je eigen **chat ID** terug (een getal).
5. Stuur je nieuwe bot ook even een bericht (bijv. "hoi") — Telegram levert pas
   berichten af aan een bot als je zelf als eerste contact hebt gelegd.

## 2. Cineville-sessiecookie ophalen

1. Log in op **https://cineville.nl** in een gewone browser (Chrome/Edge/Firefox).
2. Open de Developer Tools (F12) → tab **Network**.
3. Ververs de pagina terwijl je bent ingelogd. Zoek in de lijst naar een
   request met de naam **`session`** (volledige URL:
   `https://cineville.nl/api/auth/session`).
4. Klik erop, ga naar de **Headers**-sectie, en zoek bij *Request Headers* de
   regel die begint met `Cookie:`. Kopieer de **hele waarde** achter `Cookie:`
   (dit is één lange string met een heleboel `naam=waarde;` stukjes).
5. Plak die hele string als waarde van `"cineville_cookie"` in `config.json`.

Ik weet nog niet hoe lang deze sessie geldig blijft. Als het script een
Telegram-melding stuurt dat de sessie verlopen is, herhaal je gewoon deze
stap.

## 2b. (Optioneel) IMDb/Rotten Tomatoes-scores via OMDb

Wil je bij elke melding ook een IMDb- en Rotten Tomatoes-score zien?

1. Ga naar **https://www.omdbapi.com/apikey.aspx**, kies de gratis "FREE!"-optie
   (1000 requests/dag, ruim voldoende) en vul je e-mailadres in.
2. Je krijgt de key per e-mail toegestuurd (check ook je spam-folder).
3. Zet die key in `config.json` bij `"omdb_api_key"`.

Zonder deze key werkt alles gewoon door, dan blijven de scores uit het
bericht weg. Let op: OMDb zoekt op titel, en Cineville-titels hebben soms een
toevoeging (bv. "(re-release)") — het script probeert het automatisch ook
zonder die toevoeging, maar een enkele mismatch is niet uitgesloten.

## 3. Config klaarzetten

Kopieer `config.example.json` naar `config.json` en vul in:

```json
{
  "cineville_cookie": "... (stap 2) ...",
  "telegram_bot_token": "... (stap 1) ...",
  "telegram_chat_id": "... (stap 1) ...",
  "cities": ["amsterdam", "amstelveen"],
  "lookback_weeks": 4,
  "history_file": "history.json"
}
```

## 4. LXC-container in Proxmox

Op de Proxmox-webinterface:

1. **Create CT** → kies een **Debian 12** template (download 'm eerst via
   *local (storage) → CT Templates* als die er nog niet is).
2. Geef 'm bv. 1 vCPU, 512 MB RAM, 2 GB schijf — dit scriptje heeft vrijwel
   niets nodig.
3. Zet networking op DHCP (of een vast IP als je dat gewend bent).
4. Start de container en open een console (of SSH erin).

In de container:

```bash
apt update && apt install -y python3 python3-venv python3-pip
timedatectl set-timezone Europe/Amsterdam

mkdir -p /opt/cineville-notify
cd /opt/cineville-notify
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Kopieer vanaf je Windows-machine de bestanden `cineville_notify.py`,
`requirements.txt` en je ingevulde `config.json` naar
`/opt/cineville-notify/` op de container (bv. met `scp`, of via de Proxmox
file manager / WinSCP).

Test het handmatig:

```bash
cd /opt/cineville-notify
./venv/bin/python cineville_notify.py config.json
```

Bij een geslaagde eerste run maakt hij een `history.json` aan en stuurt hij
(bewust) nog geen Telegram-bericht — de eerste keer is er niets om mee te
vergelijken. Vanaf de tweede week krijg je pas echte meldingen.

## 5. Cronjob (dinsdagochtend)

```bash
crontab -e
```

Voeg toe:

```
0 8 * * 2 cd /opt/cineville-notify && ./venv/bin/python cineville_notify.py config.json >> run.log 2>&1
```

Dit draait elke dinsdag om 08:00 (lokale tijd, dankzij de `timedatectl`
hierboven). Check af en toe `run.log` als je twijfelt of het goed gaat.

## 6. Bot-commando's (optioneel)

Met `cineville_bot_listener.py` kun je de bot via Telegram zelf aansturen,
zonder in te loggen op de server:

- `/status` — huidige instellingen en laatst gecontroleerde week
- `/setweeks <getal>` — aantal weken historie aanpassen (1-52)
- `/setcookie <waarde>` — nieuwe sessiecookie doorgeven (zie stap 2 hierboven
  voor hoe je die uit je browser haalt)
- `/checknow` — meteen een controle uitvoeren
- `/help` — overzicht van commando's

Alleen berichten vanuit jouw eigen `telegram_chat_id` (uit `config.json`)
worden geaccepteerd; andere afzenders worden genegeerd.

Dit is een proces dat continu moet blijven draaien (geen cronjob, maar een
systemd-service). Kopieer `cineville_bot_listener.py` ook naar
`/opt/cineville-notify/` en maak dan `/etc/systemd/system/cineville-bot.service`
aan met deze inhoud:

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

Activeren:

```bash
systemctl daemon-reload
systemctl enable --now cineville-bot.service
```

Test met `/help` in je Telegram-chat met de bot. Logs bekijken:

```bash
journalctl -u cineville-bot.service -f
```
