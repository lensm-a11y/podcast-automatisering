"""
Podcast-automatisering voor het claims-project
================================================
Controleert een lijst RSS-feeds op nieuwe afleveringen, downloadt de audio,
transcribeert met lokale Whisper, en uploadt het transcript automatisch
naar de Inbox-map in Google Drive (waar het Apps Script het verder oppakt).

Eenmalige opzet die dit script vereist (zie toelichting):
1. Google Cloud service-account met Drive API aan, JSON-sleutel gedownload.
2. Die service-account als 'Bewerker' toegevoegd aan de Inbox-map in Drive.
3. pip install feedparser requests faster-whisper google-api-python-client google-auth
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

import feedparser
import requests
from faster_whisper import WhisperModel
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ===== CONFIGURATIE =====

FEEDS = [
    # Voeg hier je podcasts toe: korte naam (voor de bestandsnaam) + RSS-URL
    {"naam": "ADVoetbalpodcast", "rss_url": "https://www.omnycontent.com/d/playlist/33dbd2dc-d464-471d-9feb-abae00330078/6b7fd3a5-faa4-49d7-8618-abae007e950b/176c1b6c-c144-48c0-9b70-abae007e950b/podcast.rss"},
    # {"naam": "GeldEnMacht", "rss_url": "https://..."},
]

INBOX_FOLDER_ID = "1Sqia5kivNsQgxXbNzBHLNMzE3RJMxwB0"   # ID uit de Drive-URL van je Inbox-map
SERVICE_ACCOUNT_JSON = "service_account.json"  # pad naar het gedownloade sleutelbestand (alleen gebruikt bij lokaal draaien)
WHISPER_MODEL_GROOTTE = "small"           # tiny/base/small/medium/large-v3
STATE_BESTAND = "verwerkte_afleveringen.json"
TIJDELIJKE_AUDIO_MAP = Path("tmp_audio")

# ===== STATE: welke afleveringen zijn al verwerkt =====

def laad_state():
    if Path(STATE_BESTAND).exists():
        return json.loads(Path(STATE_BESTAND).read_text())
    return {}

def sla_state_op(state):
    Path(STATE_BESTAND).write_text(json.dumps(state, indent=2))

# ===== RSS: nieuwe afleveringen herkennen =====

def vind_nieuwe_afleveringen(feed_url, al_verwerkt):
    feed = feedparser.parse(feed_url)
    nieuw = []
    for entry in feed.entries:
        episode_id = entry.get("id") or entry.get("link")
        if episode_id in al_verwerkt:
            continue  # deze hadden we al

        audio_url = None
        for enclosure in entry.get("enclosures", []):
            if enclosure.get("type", "").startswith("audio"):
                audio_url = enclosure.get("href")
                break
        if not audio_url:
            continue  # geen audio gevonden in dit item, overslaan

        pub_datum = None
        if entry.get("published_parsed"):
            pub_datum = datetime(*entry.published_parsed[:6]).strftime("%Y%m%d")

        nieuw.append({
            "id": episode_id,
            "titel": entry.get("title", "aflevering"),
            "audio_url": audio_url,
            "datum": pub_datum
        })
    return nieuw

# ===== Audio downloaden =====

def download_audio(url, doelpad):
    TIJDELIJKE_AUDIO_MAP.mkdir(exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(doelpad, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return doelpad

# ===== Whisper: transcriberen =====

_model = None

def get_whisper_model():
    global _model
    if _model is None:
        print(f"Whisper-model laden ({WHISPER_MODEL_GROOTTE})...")
        _model = WhisperModel(WHISPER_MODEL_GROOTTE, device="cpu", compute_type="int8")
    return _model

def transcribeer(audio_pad):
    model = get_whisper_model()
    segments, _info = model.transcribe(str(audio_pad), language="nl")
    return " ".join(segment.text.strip() for segment in segments)

# ===== Drive: uploaden naar Inbox =====

def get_drive_service():
    import os
    env_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if env_creds:
        # Cloud-variant (bv. GitHub Actions): credentials komen uit een secret/env var
        creds_info = json.loads(env_creds)
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/drive"]
        )
    else:
        # Lokale variant: credentials komen uit een bestand op schijf
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_JSON, scopes=["https://www.googleapis.com/auth/drive"]
        )
    return build("drive", "v3", credentials=creds)

def upload_naar_drive(service, tekst, bestandsnaam):
    tijdelijk_pad = TIJDELIJKE_AUDIO_MAP / bestandsnaam
    tijdelijk_pad.write_text(tekst, encoding="utf-8")

    metadata = {"name": bestandsnaam, "parents": [INBOX_FOLDER_ID]}
    media = MediaFileUpload(str(tijdelijk_pad), mimetype="text/plain")
    bestand = service.files().create(body=metadata, media_body=media, fields="id").execute()
    tijdelijk_pad.unlink()
    return bestand.get("id")

# ===== Bestandsnaam bouwen volgens de conventie: brontype_brontitel_YYYYMMDD.txt =====

def maak_bestandsnaam(bron_titel, datum):
    schoon = re.sub(r"[^A-Za-z0-9]+", "", bron_titel)
    if datum:
        return f"podcast_{schoon}_{datum}.txt"
    return f"podcast_{schoon}.txt"

# ===== Hoofdproces =====

def main():
    state = laad_state()
    drive = get_drive_service()

    for feed_info in FEEDS:
        naam = feed_info["naam"]
        al_verwerkt = state.get(naam, [])
        nieuwe = vind_nieuwe_afleveringen(feed_info["rss_url"], al_verwerkt)

        if not nieuwe:
            print(f"[{naam}] geen nieuwe afleveringen.")
            continue

        for aflevering in nieuwe:
            print(f"[{naam}] nieuwe aflevering gevonden: {aflevering['titel']}")
            audio_pad = TIJDELIJKE_AUDIO_MAP / f"{naam}_{aflevering['id'][:8]}.mp3"

            try:
                download_audio(aflevering["audio_url"], audio_pad)
                tekst = transcribeer(audio_pad)
                bestandsnaam = maak_bestandsnaam(naam, aflevering["datum"])
                upload_naar_drive(drive, tekst, bestandsnaam)
                print(f"[{naam}] geüpload als {bestandsnaam}")

                al_verwerkt.append(aflevering["id"])
                state[naam] = al_verwerkt
                sla_state_op(state)  # meteen opslaan, niet pas aan het eind
            except Exception as e:
                print(f"[{naam}] FOUT bij '{aflevering['titel']}': {e}")
                # deze aflevering NIET aan verwerkt toevoegen -> volgende run opnieuw geprobeerd
            finally:
                if audio_pad.exists():
                    audio_pad.unlink()  # audio zelf hoeft niet bewaard te blijven

            time.sleep(2)  # even rustig aan tussen afleveringen

if __name__ == "__main__":
    main()
