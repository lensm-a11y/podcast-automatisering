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


def zoek_show_naam(brontitel_uit_bestandsnaam):
    """
    Tolerante matching tussen een brontitel uit een bestandsnaam (bv.
    "ADVoetbalpodcast", zonder spaties) en de keys in VASTE_DEELNEMERS
    (bv. "AD Voetbalpodcast", met spaties) — zelfde aanpak als
    zoekBekendeDeelnemers_ in het Apps Script.
    """
    def normaliseer(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    zoekterm = normaliseer(brontitel_uit_bestandsnaam)
    for show_naam in VASTE_DEELNEMERS:
        genormaliseerd = normaliseer(show_naam)
        if zoekterm in genormaliseerd or genormaliseerd in zoekterm:
            return show_naam
    return None


def parse_bestandsnaam(bestandsnaam):
    """
    Verwacht dezelfde conventie als de Apps Script-kant: brontype_brontitel(_datum).ext
    Retourneert (brontype, brontitel_ruw) of (None, None) als het patroon niet past.
    """
    naam_zonder_ext = re.sub(r"\.[^.]+$", "", bestandsnaam)
    delen = naam_zonder_ext.split("_")
    if len(delen) < 2:
        return None, None
    # Negeer een eventueel datumsegment vooraan, voor compatibiliteit met de
    # oudere naamconventie.
    if re.match(r"^\d{4}-\d{2}-\d{2}$", delen[0]):
        delen = delen[1:]
    if len(delen) < 2:
        return None, None
    return delen[0], delen[1]


def batch_verwerk(audio_map, output_map, model_naam):
    import glob

    os_makedirs(output_map)
    AUDIO_EXTENSIES = (".mp3", ".wav", ".m4a", ".ogg", ".flac")

    bestanden = [
        f for f in sorted(glob.glob(f"{audio_map}/*"))
        if f.lower().endswith(AUDIO_EXTENSIES)
    ]
    if not bestanden:
        print(f"Geen audiobestanden gevonden in {audio_map} (verwacht: {', '.join(AUDIO_EXTENSIES)}).")
        return

    print(f"{len(bestanden)} audiobestand(en) gevonden. Model wordt eenmalig geladen...")

    from faster_whisper import WhisperModel
    model = WhisperModel(model_naam, device="auto", compute_type="auto")

    for i, audio_pad in enumerate(bestanden, 1):
        bestandsnaam = audio_pad.split("/")[-1]
        brontype, brontitel_ruw = parse_bestandsnaam(bestandsnaam)

        print(f"\n[{i}/{len(bestanden)}] {bestandsnaam}")

        if not brontitel_ruw:
            print(
                f"  WAARSCHUWING: bestandsnaam volgt niet de brontype_brontitel-conventie, "
                f"transcribeer zonder specifieke show-context."
            )
            initial_prompt = ALGEMENE_CONTEXT
        else:
            show_naam = zoek_show_naam(brontitel_uit_bestandsnaam=brontitel_ruw)
            if not show_naam:
                print(f"  WAARSCHUWING: geen match gevonden in VASTE_DEELNEMERS voor '{brontitel_ruw}'.")
            else:
                print(f"  Show herkend als: {show_naam}")

            # Optionele shownotes: zelfde bestandsnaam + _shownotes.txt
            shownotes_pad = re.sub(r"\.[^.]+$", "_shownotes.txt", audio_pad)
            shownotes_pad = shownotes_pad if os_bestaat(shownotes_pad) else None
            if shownotes_pad:
                print(f"  Shownotes gevonden: {shownotes_pad}")

            initial_prompt = bouw_initial_prompt(show_naam or "", shownotes_pad)

        segments, info = model.transcribe(
            audio_pad,
            language="nl",
            initial_prompt=initial_prompt,
            vad_filter=True,
        )

        output_naam = re.sub(r"\.[^.]+$", ".txt", bestandsnaam)
        output_pad = f"{output_map}/{output_naam}"
        with open(output_pad, "w", encoding="utf-8") as f:
            for segment in segments:
                f.write(segment.text.strip() + " ")

        print(f"  Weggeschreven: {output_pad}")

    print(f"\nKlaar. {len(bestanden)} bestand(en) getranscribeerd naar {output_map}/")
    print("Upload de .txt-bestanden daaruit naar de Inbox-map in Drive om verder te gaan.")


def os_makedirs(pad):
    import os
    os.makedirs(pad, exist_ok=True)


def os_bestaat(pad):
    import os
    return os.path.isfile(pad)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", help="Pad naar één audiobestand (voor losse verwerking)")
    parser.add_argument("--show", help="Naam van de show, zoals in VASTE_DEELNEMERS (bij --audio)")
    parser.add_argument("--shownotes", default=None, help="Pad naar shownotes-tekstbestand (optioneel, bij --audio)")
    parser.add_argument("--output", help="Pad voor het uitvoerbestand .txt (bij --audio)")
    parser.add_argument("--batch-folder", default=None, help="Map met meerdere audiobestanden om in één keer te verwerken")
    parser.add_argument("--output-folder", default=None, help="Map waar de .txt-resultaten van --batch-folder naartoe gaan")
    parser.add_argument("--model", default="large-v3", help="Whisper-modelgrootte (default: large-v3)")
    args = parser.parse_args()

    if args.batch_folder:
        if not args.output_folder:
            parser.error("--output-folder is verplicht in combinatie met --batch-folder")
        batch_verwerk(args.batch_folder, args.output_folder, args.model)
        return

    if not (args.audio and args.show and args.output):
        parser.error("Geef ofwel --batch-folder + --output-folder, ofwel --audio + --show + --output")

    initial_prompt = bouw_initial_prompt(args.show, args.shownotes)
    print("Gebruikt initial_prompt:\n" + initial_prompt + "\n")

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
