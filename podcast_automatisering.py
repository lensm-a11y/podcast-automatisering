"""
transcribeer_met_context.py
============================
Transcribeert een audiobestand met faster-whisper, met een initial_prompt
opgebouwd uit (1) je vaste-deelnemerslijst per show en (2) de shownotes van
de specifieke aflevering (waar vaak de daadwerkelijke gasten in genoemd
staan). Dit vergroot de kans dat namen in één keer goed gespeld worden,
nog vóór de Gemini-correctiestap in Apps Script er iets aan hoeft te doen.

GEBRUIK
-------
python transcribeer_met_context.py \
    --audio "aflevering.mp3" \
    --show "AD Voetbalpodcast" \
    --shownotes "shownotes_aflevering.txt" \
    --output "podcast_ADVoetbalpodcast_20260826.txt"

Als je geen shownotes-bestand hebt, laat --shownotes gewoon weg.

VASTE DEELNEMERS
----------------
Vul VASTE_DEELNEMERS hieronder in — dit is dezelfde info als in je
"Podcast-deelnemers"-tabblad in de Overzicht-sheet. Twee plekken met
dezelfde info onderhouden is niet ideaal, maar dit script draait lokaal en
kan niet zomaar bij je Google Sheet — kopieer de tekst gewoon over als je
het tabblad bijwerkt. (Zie de toelichting onderaan dit bestand voor een
alternatief als je dit wilt automatiseren.)
"""

import argparse
import re
import sys

# ============================================================
# VASTE DEELNEMERS PER SHOW — kopieer dit bij uit je "Podcast-deelnemers"-
# tabblad zodra je dat bijwerkt.
# ============================================================
VASTE_DEELNEMERS = {
    "AD Voetbalpodcast": (
        "Etienne Verhoeff, Sjoerd Mossou, Maarten Wijffels, Mikos Gouka, "
        "Johan Inan, Bob Hermus"
    ),
    "Kick-off podcast Telegraaf": (
        "Valentijn Driessen, Mike Verweij, Pim Sedee, Hein Keijser, "
        "Steven Kooijman, Jeroen Kapteijns, Tijmen Lensink"
    ),
    "NOS Voetbalpodcast": (
        "Arno Vermeulen, Arman Avsaroglu, Thierry Boon, Jan Roelfs, "
        "Jeroen Grueter, Jeroen Elshoff"
    ),
    "AZ Podcast (NHD)": (
        "Chris Wobben, Jeroen Haarsma, Theo Brinkman, Brian Wijker"
    ),
    "Rondo": (
        "Wytse van der Goot, Hélène Hendriks, Marco van Basten, Ruud Gullit, "
        "Rafael van der Vaart, Youri Mulder, Theo Janssen, Wesley Sneijder"
    ),
    "Vandaag Inside": (
        "Johan Derksen, René van der Gijp, Wilfred Genee, Valentijn Driessen, "
        "Merel Ek, Job Knoester, Hélène Hendriks, Bas Nijhuis, Chris Woerts"
    ),
}

ALGEMENE_CONTEXT = (
    "Dit is een Nederlandstalige voetbalpodcast. Onderwerpen: competities, "
    "clubs, spelers, transfers, wedstrijden, trainers."
)


def haal_namen_uit_shownotes(pad):
    """
    Simpele, robuuste extractie: pakt woordreeksen die met een hoofdletter
    beginnen en die typisch op een naam lijken (Voornaam Achternaam).
    Dit is bewust een grove aanpak, geen echte named-entity-recognition —
    het doel is niet perfectie, maar een goede kans dat de shownotes-namen
    ook echt in het initial_prompt terechtkomen. Overtollige/foute treffers
    zijn onschuldig: het initial_prompt hoeft niet perfect te zijn om nuttig
    te zijn.
    """
    with open(pad, "r", encoding="utf-8") as f:
        tekst = f.read()

    # Patroon: twee of drie opeenvolgende woorden die met een hoofdletter
    # beginnen (dekt "Jan de Vries", "Piet Bakker", etc.)
    kandidaten = re.findall(
        r"\b([A-ZÀ-Ý][a-zà-ÿ'’]+(?:\s+(?:de|van|der|den|van der|van den)\s+)?"
        r"[A-ZÀ-Ý][a-zà-ÿ'’]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ'’]+)?)\b",
        tekst,
    )
    # Dedupliceren, volgorde behouden
    gezien = set()
    namen = []
    for naam in kandidaten:
        naam = naam.strip()
        if naam and naam not in gezien:
            gezien.add(naam)
            namen.append(naam)
    return namen


def bouw_initial_prompt(show, shownotes_pad):
    delen = [ALGEMENE_CONTEXT]

    vaste_namen = VASTE_DEELNEMERS.get(show)
    if vaste_namen:
        delen.append("Vaste deelnemers aan deze show: " + vaste_namen + ".")
    else:
        print(
            f"WAARSCHUWING: '{show}' niet gevonden in VASTE_DEELNEMERS — "
            "controleer de spelling, of vul het dictionary bovenaan dit "
            "script aan.",
            file=sys.stderr,
        )

    if shownotes_pad:
        namen_uit_notes = haal_namen_uit_shownotes(shownotes_pad)
        if namen_uit_notes:
            delen.append(
                "Namen genoemd in de shownotes van deze aflevering (mogelijk "
                "gasten): " + ", ".join(namen_uit_notes) + "."
            )

    # Whisper's initial_prompt heeft een praktische lengtelimiet (context-
    # window van het model) — hou het compact.
    prompt = " ".join(delen)
    return prompt[:800]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, help="Pad naar het audiobestand")
    parser.add_argument("--show", required=True, help="Naam van de show, zoals in VASTE_DEELNEMERS")
    parser.add_argument("--shownotes", default=None, help="Pad naar een tekstbestand met de shownotes (optioneel)")
    parser.add_argument("--output", required=True, help="Pad voor het uitvoerbestand (.txt)")
    parser.add_argument("--model", default="large-v3", help="Whisper-modelgrootte (default: large-v3)")
    args = parser.parse_args()

    initial_prompt = bouw_initial_prompt(args.show, args.shownotes)
    print("Gebruikt initial_prompt:\n" + initial_prompt + "\n")

    # Lazy import, zodat je dit script ook kunt draaien om alleen het
    # initial_prompt te bekijken zonder faster-whisper geïnstalleerd te hebben.
    from faster_whisper import WhisperModel

    print(f"Model laden ({args.model})...")
    model = WhisperModel(args.model, device="auto", compute_type="auto")

    print(f"Transcriberen: {args.audio}")
    segments, info = model.transcribe(
        args.audio,
        language="nl",
        initial_prompt=initial_prompt,
        vad_filter=True,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        for segment in segments:
            f.write(segment.text.strip() + " ")

    print(f"Klaar. Transcript weggeschreven naar: {args.output}")
    print(
        "Vergeet niet: dit bestand nog in de bestandsnaam-conventie "
        "brontype_brontitel.ext zetten en naar de Inbox-map te uploaden."
    )


if __name__ == "__main__":
    main()


# ============================================================
# TOELICHTING — dubbel onderhoud van de deelnemerslijst voorkomen
# ============================================================
#
# Dit script en je "Podcast-deelnemers"-tabblad in Google Sheets bevatten nu
# dezelfde informatie op twee plekken. Dat is met opzet niet geautomatiseerd
# gekoppeld: dit script draait lokaal op jouw machine (waar de audio staat),
# de Sheet leeft in Google Drive — die twee werelden raken elkaar pas zodra
# jij het transcript uploadt.
#
# Wil je dit later toch koppelen? De praktische route is dan: exporteer het
# "Podcast-deelnemers"-tabblad af en toe als CSV (Bestand > Downloaden > CSV
# in Google Sheets) en laat dit script die CSV inlezen in plaats van de
# VASTE_DEELNEMERS-dictionary hierboven. Zeg het gerust als je dat wilt —
# dat is een kleine aanpassing van dit script.
