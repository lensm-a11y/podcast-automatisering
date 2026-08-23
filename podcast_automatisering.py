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
    # {"naam": "GeldEnMacht", "rss_url": "https://..."},
]

INBOX_FOLDER_ID = "1Sqia5kivNsQgxXbNzBHLNMzE3RJMxwB0"   # ID uit de Drive-URL van je Inbox-map
WHISPER_MODEL_GROOTTE = "small"           # tiny/base/small/medium/large-v3
WHISPER_CPU_THREADS = 4                   # match het aantal vCPU's van de runner (public repo = 4)
WHISPER_BEAM_SIZE = 1                     # 1 = sneller (greedy), 5 = nauwkeuriger maar trager (standaard-Whisper-instelling)
WHISPER_VAD_FILTER = True                 # slaat stiltes/muziek/intro's over — vaak een flinke tijdsbesparing bij podcasts
STATE_BESTAND = "verwerkte_afleveringen.json"
TIJDELIJKE_AUDIO_MAP = Path("tmp_audio")
MAX_AFLEVERING_LEEFTIJD_DAGEN = 7   # negeer afleveringen ouder dan dit — voorkomt dat de hele feedgeschiedenis in één keer verwerkt wordt
SERVICE_ACCOUNT_JSON = "service_account.json"  # alleen gebruikt als fallback bij een Shared Drive/Workspace-account, zie
