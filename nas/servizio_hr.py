#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagrammi H-R 1.5 – versione per il NAS (Docker): analizza TUTTI gli ammassi e si ferma.
© 2026 Edoardo Battistel

Uso (dentro il container):  python servizio_hr.py /dati
    /dati/programma/   questo file, motore_hr.py, openngc_compatto.csv
    /dati/schede/      RISULTATO: una scheda PDF per ammasso (in inglese) + 00_summary.pdf
    /dati/_lavoro/     dati temporanei (Gaia, isocrone, letteratura, stato): cancellati alla fine

Ammassi: i 22 famosi del programma per il PC + tutti gli ammassi aperti, globulari e con nebulosa
del catalogo dell'Atlante Celeste (OpenNGC, 938). Se il container si riavvia riprende da dove era.
Alla fine scrive 00_summary.pdf, cancella _lavoro e termina (il container si ferma).
Se Gaia non risponde aspetta e riprova lo stesso ammasso (5, 10, 20, 30, 30, 30 minuti); alla fine
riprova una volta gli ammassi persi per la rete. All'inizio di un lavoro nuovo cancella le schede vecchie.
Opzione: --solo N (prova con i primi N ammassi).
Dati OpenNGC di Mattia Verga (CC-BY-SA-4.0).
"""
import argparse
import csv
import json
import math
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import motore_hr as M  # noqa: E402

from matplotlib.figure import Figure  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402

PAUSA_TRA_AMMASSI = 2.0      # secondi: per non affollare gli archivi
ATTESE_RETE = (5, 10, 20, 30, 30, 30)   # minuti di attesa tra un tentativo e l'altro se Gaia non risponde
LINGUA = "en"                # schede e riepilogo sempre in inglese
NOME_RIEPILOGO = "00_summary.pdf"
NOTE = {"eta_max": "age at limit", "eta_min": "age at limit", "av_max": "A_V at limit", "dm_bordo": "distance at edge"}
MIN_MEMBRI = 30


def registro(testo, file_registro=None):
    riga = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {testo}"
    print(riga, flush=True)
    if file_registro:
        try:
            with open(file_registro, "a", encoding="utf-8") as f:
                f.write(riga + "\n")
        except Exception:
            pass


# --------------------------------------------------------------------------- elenco degli ammassi
def _gradi_ra(t):
    h, m, s = (float(x) for x in t.split(":"))
    return 15 * (h + m / 60 + s / 3600)


def _gradi_dec(t):
    segno = -1 if t.strip().startswith("-") else 1
    d, m, s = (float(x) for x in t.strip().lstrip("+-").split(":"))
    return segno * (d + m / 60 + s / 3600)


def nome_leggibile(nome):
    m = re.match(r"^([A-Za-z]+)0*(\d+)(.*)$", nome)
    if not m:
        return nome
    sigla = {"Mel": "Melotte", "Cr": "Collinder", "Tr": "Trumpler"}.get(m.group(1), m.group(1))
    return f"{sigla} {m.group(2)}{m.group(3)}"


def elenco_ammassi(cartella_programma):
    famosi = [dict(a) for a in M.AMMASSI]
    elenco = list(famosi)
    righe = [r for r in open(Path(cartella_programma) / "openngc_compatto.csv", encoding="utf-8")
             if not r.startswith("#")]
    for r in csv.DictReader(righe, delimiter=";"):
        if r["tipo"] not in ("OCl", "GCl", "Cl+N"):
            continue
        try:
            ra, dec = _gradi_ra(r["ra"]), _gradi_dec(r["dec"])
        except Exception:
            continue
        # già tra i famosi (stesse coordinate entro 0,3°)?
        if any(abs(dec - f["dec"]) < 0.3 and abs(((ra - f["ra"] + 180) % 360 - 180) * math.cos(math.radians(dec))) < 0.3
               for f in famosi):
            continue
        nome = nome_leggibile(r["nome"])
        if r.get("messier"):
            nome += f" (M {int(r['messier'])})"
        elif r.get("nomi_comuni"):
            nome += f" ({r['nomi_comuni'].split(',')[0].strip()})"
        try:
            asse = float(r["asse_maggiore_arcmin"])
        except ValueError:
            asse = 10.0
        globulare = r["tipo"] == "GCl"
        raggio = max(0.1, min(1.5, 1.5 * asse / 60))   # 3 volte il raggio apparente
        a = dict(chiave=re.sub(r"[^a-z0-9]+", "_", r["nome"].lower()), it=nome, en=nome, ra=ra, dec=dec,
                 raggio=round(raggio, 3), tipo="globulare" if globulare else "aperto",
                 feh=-1.5 if globulare else 0.0, dal_catalogo=True)
        elenco.append(a)
    return elenco


def regola_con_letteratura(amm, lett):
    """Per gli ammassi del catalogo: raggio e finestra di parallasse dai valori pubblicati (meno stelle di campo)."""
    if not amm.get("dal_catalogo"):
        return amm
    for sigla in ("HR23", "CG20"):
        v = (lett or {}).get(sigla) or {}
        plx = v.get("plx")
        if plx and plx > 0.2:
            larg = max(0.4, 0.35 * plx)
            amm["plx_min"] = round(max(0.0, plx - larg), 3)
            amm["plx_max"] = round(plx + larg, 3)
            break
    for sigla in ("HR23", "CG20"):
        r50 = (((lett or {}).get(sigla) or {}).get("_colonne") or {}).get("r50")
        try:
            r50 = float(r50)
        except (TypeError, ValueError):
            continue
        if r50 > 0:
            amm["raggio"] = round(max(0.1, min(1.5, 4 * r50)), 3)
            break
    return amm


# --------------------------------------------------------------------------- riepilogo
def scrivi_riepilogo(percorso, stato, lingua=LINGUA):
    righe = sorted(stato.values(), key=lambda s: s["nome"].lower())
    fatti = [s for s in righe if s["esito"] == "ok"]
    saltati = [s for s in righe if s["esito"] != "ok"]
    dubbi = [s for s in fatti if s["valori"].get("dubbi")]
    intest = ["Cluster", "Type", "Members", "Age", "log age", "Distance", "A_V", "CG20", "HR23", "Notes"]
    per_pagina = 30

    def n(v, c=2):
        return "–" if v is None else f"{v:.{c}f}"

    tabella = []
    for s in fatti:
        v = s["valori"]
        note = ", ".join(sorted({NOTE.get(d, d) for d in v.get("dubbi", [])}))
        tabella.append([s["nome"][:34], "globular" if s["tipo"] == "globulare" else "open", str(s.get("membri", "")),
                        M.testo_eta(v["log_eta"], "en"), f"{n(v['log_eta'])} ± {n(v['err_log_eta'])}",
                        f"{M.intero(v['dist_pc'], 'en')} pc", n(v["av"]), n(s.get("cg20")), n(s.get("hr23")), note])
    with PdfPages(percorso) as pdf:
        info = pdf.infodict()
        info["Title"] = "H-R Diagrams – summary"
        info["Author"] = "Edoardo Battistel"
        # copertina
        fig = Figure(figsize=(11.69, 8.27))
        FigureCanvasAgg(fig)
        fig.patch.set_facecolor(M.SFONDO)
        fig.text(0.06, 0.88, "H-R Diagrams – summary", color=M.TESTO, fontsize=26, weight="bold")
        motivi = {}
        for s in saltati:
            m = s.get("motivo", "")
            k = ("Gaia parallax too small (e.g. Magellanic Clouds)" if "parallasse" in m else
                 "too few members" if "membri" in m else
                 "too few Gaia stars in the field" if "stelle Gaia" in m else
                 "network: Gaia archives did not answer" if ("ErroreRete" in m or "rete" in m) else "other")
            motivi[k] = motivi.get(k, 0) + 1
        testo = (f"Clusters analysed: {len(fatti)}   ·   not analysed: {len(saltati)}   ·   to be checked: {len(dubbi)}\n"
                 f"Date: {time.strftime('%Y-%m-%d %H:%M')}   ·   H-R Diagrams {M.VERSIONE} (NAS)   ·   {M.FIRMA}\n\n"
                 "Not analysed: " + "; ".join(f"{k} {v}" for k, v in sorted(motivi.items(), key=lambda x: -x[1])) +
                 "\n\nEach analysed cluster has its own PDF sheet in this folder (CMD, members, comparison with the literature).\n"
                 "CG20 / HR23 = log age from Cantat-Gaudin et al. 2020 / Hunt & Reffert 2023 (open clusters only).\n"
                 "Globular clusters: [M/H] = [Fe/H] + 0.25 for α-enhancement; the most metal-poor ones may stay at the age limit.\n"
                 "Notes: «age at limit» = best age at the edge of the searched range (6.0 or 10.15 in log age);\n"
                 "«A_V at limit» = extinction at the maximum searched; «distance at edge» = at the edge of the parallax range.\n"
                 "These results should be checked by eye on the cluster sheet.\n\n" +
                 M.T("en", "nota_isocrone") + "\n" + M.T("en", "nota_estinzione") + "\n" + M.T("en", "nota_colori"))
        import textwrap
        testo = "\n".join(textwrap.fill(r, 135) if r else "" for r in testo.split("\n"))
        fig.text(0.06, 0.82, testo, color=TESTO_CHIARO, fontsize=10.5, va="top")
        if fatti:
            ax = fig.add_axes([0.08, 0.08, 0.5, 0.3])
            M._stile_assi(ax)
            eta = [s["valori"]["log_eta"] for s in fatti]
            ax.hist(eta, bins=np.arange(6.0, 10.3, 0.1), color="#41e0ff", alpha=0.8)
            ax.set_xlabel("log age [yr]", color=M.TESTO)
            ax.set_ylabel("Clusters", color=M.TESTO)
        fig.text(0.995, 0.005, M.FIRMA, ha="right", va="bottom", color="#8a93ab", fontsize=9)
        pdf.savefig(fig, facecolor=M.SFONDO)
        # tabelle
        for i in range(0, len(tabella), per_pagina):
            blocco = tabella[i:i + per_pagina]
            fig = Figure(figsize=(11.69, 8.27))
            FigureCanvasAgg(fig)
            ax = fig.add_axes([0.02, 0.05, 0.96, 0.88])
            ax.set_axis_off()
            tab = ax.table(cellText=blocco, colLabels=intest, loc="upper center", cellLoc="left",
                           colWidths=[0.2, 0.06, 0.06, 0.15, 0.09, 0.08, 0.05, 0.05, 0.05, 0.13])
            tab.auto_set_font_size(False)
            tab.set_fontsize(8)
            tab.scale(1, 1.25)
            for (r, c), cella in tab.get_celld().items():
                if r > 0 and c == 9 and blocco[r - 1][9]:
                    cella.set_facecolor("#fff1d6")
            fig.text(0.02, 0.96, f"Results {i + 1}–{i + len(blocco)}", fontsize=13, weight="bold")
            fig.text(0.995, 0.005, M.FIRMA, ha="right", va="bottom", color="#666", fontsize=8)
            pdf.savefig(fig)
        # ammassi non analizzati, con il motivo
        for i in range(0, len(saltati), 45):
            fig = Figure(figsize=(11.69, 8.27))
            FigureCanvasAgg(fig)
            fig.text(0.03, 0.96, "Not analysed", fontsize=13, weight="bold")
            y = 0.92
            for s in saltati[i:i + 45]:
                fig.text(0.03, y, f"{s['nome'][:40]}", fontsize=8.5)
                fig.text(0.33, y, s.get("motivo_en", s.get("motivo", ""))[:120], fontsize=8.5, color="#444")
                y -= 0.019
            fig.text(0.995, 0.005, M.FIRMA, ha="right", va="bottom", color="#666", fontsize=8)
            pdf.savefig(fig)


TESTO_CHIARO = "#c9d1e6"


# --------------------------------------------------------------------------- lavoro
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cartella")
    ap.add_argument("--solo", type=int, default=0, help="prova: solo i primi N ammassi")
    a = ap.parse_args()
    base = Path(a.cartella)
    schede = base / "schede"
    lavoro = base / "_lavoro"
    schede.mkdir(parents=True, exist_ok=True)
    riepilogo = schede / NOME_RIEPILOGO
    if riepilogo.exists() and not lavoro.exists():
        registro("Lavoro già finito: le schede sono in " + str(schede) +
                 f". Per rifare tutto cancella schede/{NOME_RIEPILOGO} e riavvia il container.")
        return 0
    if not lavoro.exists():
        vecchie = list(schede.glob("*.pdf"))
        if vecchie:
            registro(f"Lavoro nuovo: cancello {len(vecchie)} schede vecchie in {schede}")
            for f in vecchie:
                f.unlink(missing_ok=True)
    lavoro.mkdir(parents=True, exist_ok=True)
    reg = lavoro / "registro.txt"
    M.CARTELLA_DATI = lavoro
    M.CARTELLA_GAIA = lavoro / "gaia"
    M.CARTELLA_MIST = lavoro / "mist"
    M.CARTELLA_LETT = lavoro / "letteratura"
    file_stato = lavoro / "stato.json"
    stato = json.loads(file_stato.read_text(encoding="utf-8")) if file_stato.exists() else {}

    registro(f"Diagrammi H-R {M.VERSIONE} sul NAS – {M.FIRMA}", reg)
    M.SCALA_TESTO = 1.0
    if not M.griglia_pronta():
        registro("Isocrone MIST: scarico 160 MB e le preparo (una volta sola)…", reg)
        ultimo = [0.0]

        def prog(fr, mb=None, tot=None):
            if mb is not None and fr - ultimo[0] >= 0.1:
                ultimo[0] = fr
                registro(f"  MIST {mb:.0f}/{tot:.0f} MB", reg)
        for tentativo in range(20):
            try:
                M.scarica_e_prepara_mist(lambda s: registro(s, reg), prog)
                break
            except Exception as e:
                registro(f"  MIST non riuscito ({e}): riprovo tra 10 minuti", reg)
                time.sleep(600)
        else:
            registro("MIST non disponibile: mi fermo (riavvia il container più tardi).", reg)
            return 1
    griglia = M.carica_griglia()
    (M.CARTELLA_MIST / f"{M.NOME_MIST}.txz").unlink(missing_ok=True)   # l'archivio non serve più

    ammassi = elenco_ammassi(Path(__file__).parent)
    if a.solo:
        ammassi = ammassi[:a.solo]
    da_fare = [x for x in ammassi if x["chiave"] not in stato]
    registro(f"Ammassi: {len(ammassi)} in tutto, {len(ammassi) - len(da_fare)} già fatti, {len(da_fare)} da fare", reg)

    def analizza_uno(amm, etichetta):
        nome = M.nome_ammasso(amm, LINGUA)
        t0 = time.time()
        voce = dict(nome=nome, tipo=amm["tipo"], esito="errore")
        try:
            lett = M.letteratura(amm, lambda s: None)
            amm = regola_con_letteratura(dict(amm), lett)
            an = dict(ammasso=amm, versione=M.VERSIONE, data=time.strftime("%Y-%m-%d %H:%M"), soglia=0.7, lett=lett)
            for tentativo, attesa in enumerate(list(ATTESE_RETE) + [None]):
                try:
                    an["dati"], an["fonte"] = M.scarica_gaia(amm, False, lambda s: None)
                    break
                except M.ErroreRete as e:
                    if attesa is None:
                        raise
                    registro(f"  {nome}: Gaia non risponde ({str(e)[:90]}); riprovo tra {attesa} minuti", reg)
                    time.sleep(attesa * 60)
            if len(an["dati"]["source_id"]) < 50:
                raise ValueError(f"solo {len(an['dati']['source_id'])} stelle Gaia nel campo")
            an["p"], an["membri"] = M.calcola_membri(an["dati"], amm, lett, lambda s: None)
            voce["membri"] = an["membri"]["n_membri"]
            if an["membri"]["n_membri"] < MIN_MEMBRI:
                raise ValueError(f"troppi pochi membri ({an['membri']['n_membri']})")
            an["dist_plx"] = M.distanza_da_parallasse(an["dati"], an["p"], 0.7)
            if not an["dist_plx"] or "dm" not in an["dist_plx"]:
                raise ValueError("parallasse troppo piccola: distanza non misurabile con Gaia")
            an["adattamento"] = M.riadatta(an, griglia, amm.get("feh", 0.0), lambda s: None)
            pdf = schede / f"{amm['chiave']}.pdf"
            M.salva_pdf(pdf, an, LINGUA, griglia)
            aperto = amm.get("tipo") != "globulare"   # CG20 e HR23 sono cataloghi di ammassi aperti
            voce.update(esito="ok", pdf=pdf.name, valori=an["adattamento"],
                        cg20=((lett.get("CG20") or {}).get("log_eta")) if aperto else None,
                        hr23=((lett.get("HR23") or {}).get("log_eta")) if aperto else None)
            r = an["adattamento"]
            registro(f"[{etichetta}] {nome}: {voce['membri']} membri, log età {r['log_eta']:.2f}, "
                     f"{r['dist_pc']:.0f} pc, A_V {r['av']:.2f}{'  DA VERIFICARE ' + ','.join(r['dubbi']) if r.get('dubbi') else ''}"
                     f"  ({time.time() - t0:.0f} s)", reg)
        except Exception as e:
            rete = isinstance(e, M.ErroreRete)
            voce["esito"] = "rete" if rete else "errore"
            voce["motivo"] = f"{type(e).__name__}: {e}"[:200]
            m = str(e)
            voce["motivo_en"] = ("network: Gaia archives did not answer" if rete else
                                 "Gaia parallax too small: distance not measurable" if "parallasse" in m else
                                 "too few members" + m[m.find("("):] if "membri" in m else
                                 m.replace("solo", "only").replace("stelle Gaia nel campo", "Gaia stars in the field")
                                 if "stelle Gaia" in m else f"{type(e).__name__}: {e}")[:200]
            registro(f"[{etichetta}] {nome}: non analizzato – {voce['motivo']}", reg)
            if not isinstance(e, (ValueError, M.ErroreRete)):
                traceback.print_exc()
        for f in M.CARTELLA_GAIA.glob(f"{amm['chiave']}_*"):   # dati Gaia di questo ammasso: non servono più
            f.unlink(missing_ok=True)
        stato[amm["chiave"]] = voce
        tmp = file_stato.with_suffix(".tmp")
        tmp.write_text(json.dumps(stato, ensure_ascii=False), encoding="utf-8")
        tmp.replace(file_stato)
        time.sleep(PAUSA_TRA_AMMASSI)

    for i, amm in enumerate(da_fare, 1):
        analizza_uno(amm, f"{i}/{len(da_fare)}")

    # ultimo giro: gli ammassi persi per la rete
    persi = [x for x in ammassi if stato.get(x["chiave"], {}).get("esito") == "rete"]
    if persi:
        registro(f"Ultimo giro: riprovo {len(persi)} ammassi persi per la rete (tra 30 minuti)", reg)
        time.sleep(30 * 60)
        for i, amm in enumerate(persi, 1):
            analizza_uno(amm, f"riprova {i}/{len(persi)}")

    registro("Tutti gli ammassi fatti: scrivo il riepilogo.", reg)
    scrivi_riepilogo(riepilogo, stato)
    fatti = sum(1 for s in stato.values() if s["esito"] == "ok")
    registro(f"FINITO: {fatti} schede in {schede}. Cancello i dati temporanei.", reg)
    shutil.rmtree(lavoro, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
