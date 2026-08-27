
"""
Eenmalig, los te draaien script: haalt alle bestaande transcripten uit de Inbox-map,
verrijkt ze met de nieuwste context/namenlijst + reclame-filtering via Gemini,
en werkt de bestanden in Drive bij (zelfde bestand, nieuwe inhoud).

Hergebruikt de configuratie en functies uit podcast_automatisering.py -- zet dit
bestand in dezelfde map/repo als podcast_automatisering.py.

Benodigde environment-variabelen (zelfde als het hoofdscript):
  OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REFRESH_TOKEN, GEMINI_API_KEY

Lokaal draaien: pip install google-api-python-client google-auth requests
(faster-whisper/whisperx zijn NIET nodig voor dit script, ook al staan ze
bovenin podcast_automatisering.py -- dit script gebruikt alleen de Drive/Gemini-delen.)
"""

import re
import time
import podcast_automatisering as pa
from googleapiclient.http import MediaIoBaseUpload
import io


def vind_feed_context(bestandsnaam):
    """Leidt uit de bestandsnaam (podcast_<naam>_<datum>.txt) welke feed dit is,
    en geeft de bijbehorende context terug uit FEEDS."""
    match = re.match(r"podcast_([A-Za-z]+?)(?:_\d{8})?\.txt$", bestandsnaam)
    if not match:
        return None, None
    naam_in_bestand = match.group(1)
    for feed in pa.FEEDS:
        if feed["naam"] == naam_in_bestand:
            return feed["naam"], feed.get("context")
    return naam_in_bestand, None  # geen match gevonden in FEEDS, toch proberen zonder context


def main():
    drive = pa.get_drive_service()

    print("Bestanden in Inbox ophalen...")
    resultaat = drive.files().list(
        q=f"'{pa.INBOX_FOLDER_ID}' in parents and name contains 'podcast_' and trashed = false",
        fields="files(id, name)",
        pageSize=1000
    ).execute()
    bestanden = resultaat.get("files", [])
    print(f"{len(bestanden)} bestand(en) gevonden om te verrijken.\n")

    for bestand in bestanden:
        bestand_id = bestand["id"]
        bestandsnaam = bestand["name"]
        naam, context = vind_feed_context(bestandsnaam)

        if not naam:
            print(f"[{bestandsnaam}] kon feednaam niet herleiden uit bestandsnaam, overgeslagen.")
            continue

        print(f"[{bestandsnaam}] downloaden...")
        try:
            ruwe_tekst = drive.files().get_media(fileId=bestand_id).execute().decode("utf-8")
        except Exception as e:
            print(f"[{bestandsnaam}] FOUT bij downloaden: {e}")
            continue

        if not ruwe_tekst.strip():
            print(f"[{bestandsnaam}] leeg bestand, overgeslagen.")
            continue

        print(f"[{bestandsnaam}] verrijken via Gemini (namen + reclame filteren)...")
        try:
            verrijkte_tekst = pa.verrijk_met_llm(ruwe_tekst, naam, context)
        except Exception as e:
            print(f"[{bestandsnaam}] FOUT bij verrijken: {e}")
            continue

        print(f"[{bestandsnaam}] bijwerken in Drive...")
        try:
            media = MediaIoBaseUpload(io.BytesIO(verrijkte_tekst.encode("utf-8")), mimetype="text/plain")
            drive.files().update(fileId=bestand_id, media_body=media).execute()
            print(f"[{bestandsnaam}] gelukt.\n")
        except Exception as e:
            print(f"[{bestandsnaam}] FOUT bij bijwerken in Drive: {e}\n")

        time.sleep(2)  # rustig aan tussen Gemini-aanroepen

    print("Klaar.")


if __name__ == "__main__":
    main()
