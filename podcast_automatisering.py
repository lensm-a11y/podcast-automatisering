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
from datetime import datetime, timedelta
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
    {"naam": "NOSVoetbalpodcast", "rss_url": "https://podcast.npo.nl/feed/nos-voetbalpodcast.xml"},
    {"naam": "KickOffTelegraaf", "rss_url": "https://www.omnycontent.com/d/playlist/fdd7ab40-270d-4a1e-a257-acd200da1324/f12b3a33-c5e5-4921-bb11-ae030151489d/244539d5-19fe-4548-a2dd-ae03015148c2/podcast.rss"},
    {"naam": "AZPodcastNHD", "rss_url": "https://www.omnycontent.com/d/playlist/fdd7ab40-270d-4a1e-a257-acd200da1324/d8f71e1d-5ad9-4428-b9bf-b441006d81f1/803738c5-fe14-4e7c-8915-b441006d8206/podcast.rss"},
    {"naam": "VandaagInside", "rss_url": "https://www.omnycontent.com/d/playlist/56ccbbb7-0ff7-4482-9d99-a88800f49f6c/7f3260de-b7ab-4b6d-818a-a96800ba1862/4d7e974e-719e-45f4-84fe-a96800bc8ad7/podcast.rss"},
    {"naam": "RondoZiggoSport", "rss_url": "https://app.springcast.fm/podcast-xml/17447"},
]

INBOX_FOLDER_ID = "1Sqia5kivNsQgxXbNzBHLNMzE3RJMxwB0"   # ID uit de Drive-URL van je Inbox-map
WHISPER_MODEL_GROOTTE = "small"           # tiny/base/small/medium/large-v3
WHISPER_CPU_THREADS = 4                   # match het aantal vCPU's van de runner (public repo = 4)
WHISPER_BEAM_SIZE = 1                     # 1 = sneller (greedy), 5 = nauwkeuriger maar trager (standaard-Whisper-instelling)
WHISPER_VAD_FILTER = True                 # slaat stiltes/muziek/intro's over — vaak een flinke tijdsbesparing bij podcasts
STATE_BESTAND = "verwerkte_afleveringen.json"
TIJDELIJKE_AUDIO_MAP = Path("tmp_audio")
MAX_AFLEVERING_LEEFTIJD_DAGEN = 60  # TIJDELIJK ruim gezet voor de eerste testronde — zet dit terug naar bv. 7 zodra alles werkt
ALLEEN_LAATSTE_AFLEVERING = True    # True = per feed maximaal 1 (de meest recente) nieuwe aflevering per run verwerken
ENABLE_DIARIZATION = True           # True = sprekers labelen (SPEAKER_00, etc.) via WhisperX+pyannote, trager dan zonder
SERVICE_ACCOUNT_JSON = "service_account.json"  # alleen gebruikt als fallback bij een Shared Drive/Workspace-account, zie get_drive_service()

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
    grens = datetime.now() - timedelta(days=MAX_AFLEVERING_LEEFTIJD_DAGEN)

    for entry in feed.entries:
        episode_id = entry.get("id") or entry.get("link")
        if episode_id in al_verwerkt:
            continue  # deze hadden we al

        pub_datum_dt = None
        if entry.get("published_parsed"):
            pub_datum_dt = datetime(*entry.published_parsed[:6])
            if pub_datum_dt < grens:
                continue  # te oud, negeren (voorkomt verwerken van de hele feedgeschiedenis)

        audio_url = None
        for enclosure in entry.get("enclosures", []):
            if enclosure.get("type", "").startswith("audio"):
                audio_url = enclosure.get("href")
                break
        if not audio_url:
            continue  # geen audio gevonden in dit item, overslaan

        pub_datum = pub_datum_dt.strftime("%Y%m%d") if pub_datum_dt else None

        nieuw.append({
            "id": episode_id,
            "titel": entry.get("title", "aflevering"),
            "audio_url": audio_url,
            "datum": pub_datum,
            "_sorteerdatum": pub_datum_dt or datetime.min  # ontbrekende datum onderaan sorteren
        })

    # Nieuwste eerst -- belangrijk zodat "alleen de laatste" ook echt de laatste is,
    # ongeacht de volgorde waarin de RSS-feed de items levert.
    nieuw.sort(key=lambda a: a["_sorteerdatum"], reverse=True)
    for a in nieuw:
        del a["_sorteerdatum"]

    if ALLEEN_LAATSTE_AFLEVERING:
        nieuw = nieuw[:1]

    return nieuw

# ===== Audio downloaden =====

def download_audio(url, doelpad):
    TIJDELIJKE_AUDIO_MAP.mkdir(exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }
    with requests.get(url, stream=True, timeout=60, headers=headers) as r:
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
        print(f"Whisper-model laden ({WHISPER_MODEL_GROOTTE}, {WHISPER_CPU_THREADS} threads)...")
        _model = WhisperModel(
            WHISPER_MODEL_GROOTTE,
            device="cpu",
            compute_type="int8",
            cpu_threads=WHISPER_CPU_THREADS
        )
    return _model

def transcribeer(audio_pad):
    if ENABLE_DIARIZATION:
        return transcribeer_met_sprekers(audio_pad)

    model = get_whisper_model()
    segments, _info = model.transcribe(
        str(audio_pad),
        language="nl",
        beam_size=WHISPER_BEAM_SIZE,
        vad_filter=WHISPER_VAD_FILTER
    )
    return " ".join(segment.text.strip() for segment in segments)

_whisperx_model = None
_align_model = None
_align_metadata = None
_diarize_model = None

def transcribeer_met_sprekers(audio_pad):
    global _whisperx_model, _align_model, _align_metadata, _diarize_model
    import os
    import whisperx

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        print("WAARSCHUWING: HF_TOKEN ontbreekt, diarization overgeslagen voor deze aflevering (terugval op tekst zonder sprekerlabels).")
        model = get_whisper_model()
        segments, _ = model.transcribe(str(audio_pad), language="nl", beam_size=WHISPER_BEAM_SIZE, vad_filter=WHISPER_VAD_FILTER)
        return " ".join(s.text.strip() for s in segments)

    device = "cpu"
    if _whisperx_model is None:
        print("WhisperX-model laden voor transcriptie + uitlijning (eenmalig per run)...")
        _whisperx_model = whisperx.load_model(WHISPER_MODEL_GROOTTE, device, compute_type="int8", language="nl")
    audio = whisperx.load_audio(str(audio_pad))
    result = _whisperx_model.transcribe(audio, batch_size=8, language="nl")

    print("Woorden uitlijnen...")
    if _align_model is None:
        _align_model, _align_metadata = whisperx.load_align_model(language_code="nl", device=device)
    result = whisperx.align(result["segments"], _align_model, _align_metadata, audio, device, return_char_alignments=False)

    print("Sprekers herkennen (diarization)...")
    if _diarize_model is None:
        _diarize_model = whisperx.diarize.DiarizationPipeline(token=hf_token, device=device)
    diarize_segments = _diarize_model(audio)
    result = whisperx.assign_word_speakers(diarize_segments, result)

    regels = []
    for seg in result["segments"]:
        spreker = seg.get("speaker", "Onbekende spreker")
        tekst = seg.get("text", "").strip()
        if tekst:
            regels.append(f"{spreker}: {tekst}")
    return "\n".join(regels)

# ===== Drive: uploaden naar Inbox =====

def get_drive_service():
    import os
    from google.oauth2.credentials import Credentials as UserCredentials

    client_id = os.environ.get("OAUTH_CLIENT_ID")
    client_secret = os.environ.get("OAUTH_CLIENT_SECRET")
    refresh_token = os.environ.get("OAUTH_REFRESH_TOKEN")

    if client_id and client_secret and refresh_token:
        # Cloud-variant (GitHub Actions): inloggen als jijzelf i.p.v. als service-account.
        # Dit is de aanbevolen route bij een persoonlijk Google-account, omdat een
        # service-account geen eigen opslagquota heeft en dus niet naar een gewone
        # "Mijn Drive"-map mag schrijven (HttpError 403: storageQuotaExceeded).
        creds = UserCredentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/drive"],
        )
    else:
        # Lokale variant / fallback: service-account-bestand op schijf.
        # Werkt alleen bij een Shared Drive of een Google Workspace-account met
        # domeindelegatie -- niet bij een gewoon persoonlijk Google-account.
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
            veilig_id = re.sub(r"[^A-Za-z0-9]", "", aflevering["id"])[:12]
            audio_pad = TIJDELIJKE_AUDIO_MAP / f"{naam}_{veilig_id}.mp3"

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
