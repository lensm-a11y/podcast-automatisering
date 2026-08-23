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
    {"naam": "ADVoetbalpodcast", "rss_url": "https://www.omnycontent.com/d/playlist/33dbd2dc-d464-471d-9feb-abae00330078/6b7fd3a5-faa4-49d7-8618-abae007e950b/176c1b6c-c144-48c0-9b70-abae007e950b/podcast.rss"},
    {"naam": "NOSVoetbalpodcast", "rss_url": "https://podcast.npo.nl/feed/nos-voetbalpodcast.xml"},
    {"naam": "KickOffTelegraaf", "rss_url": "https://www.omnycontent.com/d/playlist/fdd7ab40-270d-4a1e-a257-acd200da1324/f12b3a33-c5e5-4921-bb11-ae030151489d/244539d5-19fe-4548-a2dd-ae03015148c2/podcast.rss"},
    {"naam": "AZPodcastNHD", "rss_url": "https://www.omnycontent.com/d/playlist/fdd7ab40-270d-4a1e-a257-acd200da1324/d8f71e1d-5ad9-4428-b9bf-b441006d81f1/803738c5-fe14-4e7c-8915-b441006d8206/podcast.rss"},
    {"naam": "VandaagInside", "rss_url": "https://www.omnycontent.com/d/playlist/56ccbbb7-0ff7-4482-9d99-a88800f49f6c/7f3260de-b7ab-4b6d-818a-a96800ba1862/4d7e974e-719e-45f4-84fe-a96800bc8ad7/podcast.rss"},
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
