#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagrammi H-R – motore di calcolo (versione 1.4)
© 2026 Edoardo Battistel

Per un ammasso stellare:
  1. scarica le stelle di Gaia DR3 nel campo (IRSA, con ripiego sull'archivio ESA; copia locale)
  2. separa i membri dalle stelle di campo con moti propri, parallasse e posizione
     (HDBSCAN di scikit-learn per partire, poi un modello a miscela che dà a ogni stella
      la probabilità di appartenere all'ammasso; senza scikit-learn parte da un picco dei moti propri)
  3. confronta il diagramma colore-magnitudine con le isocrone MIST v1.2 (bande Gaia EDR3)
     e trova età, modulo di distanza ed estinzione migliori
  4. legge i valori di letteratura (Cantat-Gaudin 2020, Hunt & Reffert 2023) dallo specchio
     VizieR di Harvard
  5. disegna i grafici (matplotlib) e salva PDF, PNG, CSV dei membri e un riassunto JSON

Usa numpy, scipy (obbligatori), scikit-learn e matplotlib (se ci sono).
"""
import csv
import hashlib
import io
import json
import math
import os
import re
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.ndimage import gaussian_filter

try:
    from sklearn.cluster import HDBSCAN  # scikit-learn >= 1.3
    HA_SKLEARN = True
except Exception:  # pragma: no cover
    HA_SKLEARN = False

try:
    import matplotlib
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    HA_MPL = True
except Exception:  # pragma: no cover
    HA_MPL = False

VERSIONE = "1.4"
FIRMA = "© 2026 Edoardo Battistel"
UA = f"DiagrammiHR/{VERSIONE} (Python urllib)"

CARTELLA_DATI = Path.home() / ".diagrammi_hr"
CARTELLA_GAIA = CARTELLA_DATI / "gaia"
CARTELLA_MIST = CARTELLA_DATI / "mist"
CARTELLA_LETT = CARTELLA_DATI / "letteratura"


def cartella_risultati():
    for base in (Path.home() / "Scrivania" / "Astronomia", Path.home() / "Desktop" / "Astronomia"):
        if base.is_dir():
            return base / "Diagrammi_HR"
    return Path.home() / "Diagrammi_HR"


# Estinzione nelle bande Gaia (Wang & Chen 2019): A_G/A_V e E(BP-RP)/A_V
COEFF_AG = 0.789
COEFF_EBR = 1.002 - 0.589
ZP_PARALLASSE = 0.017   # mas, da sommare alle parallassi Gaia DR3 (Lindegren et al. 2021, valore medio)

URL_IRSA = "https://irsa.ipac.caltech.edu/TAP/sync"
URL_ESA = "https://gea.esac.esa.int/tap-server/tap/sync"
URL_MIST = "https://waps.cfa.harvard.edu/MIST/data/tarballs_v1.2/MIST_v1.2_vvcrit0.4_UBVRIplus.txz"
NOME_MIST = "MIST_v1.2_vvcrit0.4_UBVRIplus"
URL_VIZIER = "https://vizier.cfa.harvard.edu/viz-bin/asu-tsv"
CAT_CG20 = "J/A+A/640/A1/table1"
CAT_HR23 = "J/A+A/673/A114/clusters"

CAMPI_GAIA = ["source_id", "ra", "dec", "parallax", "parallax_error", "pmra", "pmdec",
              "pmra_error", "pmdec_error", "phot_g_mean_mag", "phot_bp_mean_mag",
              "phot_rp_mean_mag", "bp_rp", "ruwe", "phot_bp_rp_excess_factor"]

# --------------------------------------------------------------------------- testi
TESTI = {
    "it": {
        "titolo": "Diagrammi H-R", "ammasso": "Ammasso", "altro": "Altro (coordinate)…",
        "lingua": "Lingua", "analizza": "Scarica e analizza", "riscarica": "Riscarica i dati Gaia",
        "riadatta": "Riadatta", "torna": "Ripristina i valori trovati", "dim_testo": "Dimensione testo",
        "guida": "Guida: come leggere i risultati", "salva_pdf": "Salva PDF",
        "salva_csv": "Salva CSV", "apri_cartella": "Apri cartella dei risultati",
        "eta": "Età", "log_eta": "log età [anni]", "dm": "Modulo di distanza", "distanza": "Distanza",
        "av": "Estinzione A_V", "estinzione": "Estinzione", "feh": "[Fe/H]", "metallicita": "Metallicità [Fe/H]",
        "soglia": "Soglia di probabilità", "membri": "Membri", "campo": "Stelle di campo",
        "n_membri": "membri", "isocrona": "Isocrona MIST", "prob": "Probabilità di appartenenza",
        "asse_colore": "BP − RP [mag]", "asse_G": "G [mag]", "titolo_hr": "Diagramma colore-magnitudine",
        "tab_hr": "Diagramma H-R", "tab_membri": "Membri", "tab_risultati": "Risultati",
        "moti_propri": "Moti propri", "pmra": "μα* [mas/anno]", "pmdec": "μδ [mas/anno]",
        "parallasse": "Parallasse [mas]", "conteggi": "Numero di stelle", "cielo": "Posizione in cielo",
        "dra": "Δα cos δ [°]", "ddec": "Δδ [°]", "par_vs_G": "Parallasse e luminosità",
        "risultati": "Risultati", "adattamento": "Adattamento (questo programma)",
        "letteratura": "Letteratura", "non_trovato": "non trovato", "fonte_dati": "Dati",
        "nota_estinzione": "Estinzione: A_G = 0,789 A_V, E(BP−RP) = 0,413 A_V (Wang & Chen 2019); con MIST esce "
                           "di solito 0,1–0,2 più alta dei cataloghi. Parallassi corrette di +0,017 mas. "
                           "Adattamento sulle stelle con M_G < 6.",
        "nota_isocrone": "Isocrone MIST v1.2 (Choi et al. 2016; Dotter 2016), v/v_crit = 0,4, bande Gaia EDR3.",
        "data_analisi": "Analisi del", "stella": "Stella", "clic_stella": "Fai clic su una stella del diagramma "
                                                                         "per vederne i dati Gaia.",
        "stato_pronto": "Pronto.", "stato_lett": "Leggo i valori di letteratura…",
        "stato_gaia": "Scarico le stelle di Gaia DR3…", "stato_membri": "Separo i membri dalle stelle di campo…",
        "stato_adatta": "Confronto con le isocrone…", "stato_fatto": "Analisi finita.",
        "stato_mist": "Scarico le isocrone MIST (160 MB, una volta sola)…",
        "stato_mist_leggo": "Preparo le isocrone (qualche minuto, una volta sola)…",
        "chiedi_mist": "Per stimare l'età servono le isocrone MIST (circa 160 MB da Harvard, "
                       "scaricate una volta sola). Le scarico adesso?",
        "errore": "Errore", "salvato": "Salvato", "ra": "AR [°]", "dec": "Dec [°]", "raggio": "Raggio [°]",
        "nome": "Nome", "tipo_aperto": "ammasso aperto", "tipo_globulare": "ammasso globulare",
        "stelle_scaricate": "stelle scaricate", "da": "da", "anni": "anni", "milioni": "milioni di anni",
        "miliardi": "miliardi di anni", "senza_mpl": "matplotlib non è installato: servono "
                                                    "«sudo apt install python3-matplotlib».",
        "pc": "pc", "anni_luce": "anni luce", "membro_si": "membro", "membro_no": "campo",
        "nota_lett": "Valori di letteratura dallo specchio VizieR di Harvard.",
        "dubbio": "Da verificare", "eta_max": "età al limite massimo (14 miliardi di anni)",
        "eta_min": "età al limite minimo (1 milione di anni)", "av_max": "estinzione al limite massimo",
        "dm_bordo": "distanza al bordo dell'intervallo della parallasse",
        "colori_incerti": "Membri con colori incerti (affollamento)",
        "nota_colori": "Esclusi dall'adattamento i membri con colori BP−RP contaminati (C*, Riello et al. 2021).",
    },
    "en": {
        "titolo": "H-R Diagrams", "ammasso": "Cluster", "altro": "Other (coordinates)…",
        "lingua": "Language", "analizza": "Download and analyse", "riscarica": "Download Gaia data again",
        "riadatta": "Refit", "torna": "Reset to best fit", "dim_testo": "Text size",
        "guida": "Guide: how to read the results", "salva_pdf": "Save PDF",
        "salva_csv": "Save CSV", "apri_cartella": "Open results folder",
        "eta": "Age", "log_eta": "log age [yr]", "dm": "Distance modulus", "distanza": "Distance",
        "av": "Extinction A_V", "estinzione": "Extinction", "feh": "[Fe/H]", "metallicita": "Metallicity [Fe/H]",
        "soglia": "Probability threshold", "membri": "Members", "campo": "Field stars",
        "n_membri": "members", "isocrona": "MIST isochrone", "prob": "Membership probability",
        "asse_colore": "BP − RP [mag]", "asse_G": "G [mag]", "titolo_hr": "Colour-magnitude diagram",
        "tab_hr": "H-R diagram", "tab_membri": "Members", "tab_risultati": "Results",
        "moti_propri": "Proper motions", "pmra": "μα* [mas/yr]", "pmdec": "μδ [mas/yr]",
        "parallasse": "Parallax [mas]", "conteggi": "Number of stars", "cielo": "Position on the sky",
        "dra": "Δα cos δ [°]", "ddec": "Δδ [°]", "par_vs_G": "Parallax and brightness",
        "risultati": "Results", "adattamento": "Fit (this program)",
        "letteratura": "Literature", "non_trovato": "not found", "fonte_dati": "Data",
        "nota_estinzione": "Extinction: A_G = 0.789 A_V, E(BP−RP) = 0.413 A_V (Wang & Chen 2019); with MIST it is "
                           "usually 0.1–0.2 higher than catalogues. Parallaxes corrected by +0.017 mas. "
                           "Fit uses stars with M_G < 6.",
        "nota_isocrone": "MIST v1.2 isochrones (Choi et al. 2016; Dotter 2016), v/v_crit = 0.4, Gaia EDR3 bands.",
        "data_analisi": "Analysis of", "stella": "Star", "clic_stella": "Click a star in the diagram "
                                                                       "to see its Gaia data.",
        "stato_pronto": "Ready.", "stato_lett": "Reading literature values…",
        "stato_gaia": "Downloading Gaia DR3 stars…", "stato_membri": "Separating members from field stars…",
        "stato_adatta": "Fitting isochrones…", "stato_fatto": "Analysis finished.",
        "stato_mist": "Downloading MIST isochrones (160 MB, only once)…",
        "stato_mist_leggo": "Preparing isochrones (a few minutes, only once)…",
        "chiedi_mist": "Age estimates need the MIST isochrones (about 160 MB from Harvard, "
                       "downloaded only once). Download them now?",
        "errore": "Error", "salvato": "Saved", "ra": "RA [°]", "dec": "Dec [°]", "raggio": "Radius [°]",
        "nome": "Name", "tipo_aperto": "open cluster", "tipo_globulare": "globular cluster",
        "stelle_scaricate": "stars downloaded", "da": "from", "anni": "years", "milioni": "million years",
        "miliardi": "billion years", "senza_mpl": "matplotlib is not installed: run "
                                                  "“sudo apt install python3-matplotlib”.",
        "pc": "pc", "anni_luce": "light years", "membro_si": "member", "membro_no": "field",
        "nota_lett": "Literature values from the Harvard VizieR mirror.",
        "dubbio": "To be checked", "eta_max": "age at the upper limit (14 billion years)",
        "eta_min": "age at the lower limit (1 million years)", "av_max": "extinction at the upper limit",
        "dm_bordo": "distance at the edge of the parallax range",
        "colori_incerti": "Members with uncertain colours (crowding)",
        "nota_colori": "Members with contaminated BP−RP colours excluded from the fit (C*, Riello et al. 2021).",
    },
}


def T(lingua, chiave):
    return TESTI.get(lingua, TESTI["it"]).get(chiave, TESTI["it"].get(chiave, chiave))


# --------------------------------------------------------------------------- ammassi famosi
# tipo: "aperto" / "globulare"; feh: metallicità di partenza; plx_min: taglio in parallasse (mas)
# per ridurre il campo degli ammassi vicini e molto estesi; gmax: magnitudine limite scaricata.
AMMASSI = [
    dict(chiave="pleiadi", it="Pleiadi (M 45)", en="Pleiades (M 45)", ra=56.750, dec=24.117, raggio=2.0, tipo="aperto", feh=0.0, plx_min=3.0),
    dict(chiave="iadi", it="Iadi", en="Hyades", ra=66.725, dec=15.870, raggio=10.0, tipo="aperto", feh=0.13, plx_min=12.0),
    dict(chiave="presepe", it="Presepe (M 44)", en="Beehive (M 44)", ra=130.100, dec=19.667, raggio=1.5, tipo="aperto", feh=0.16, plx_min=3.0),
    dict(chiave="alfa_persei", it="α Persei (Melotte 20)", en="α Persei (Melotte 20)", ra=51.90, dec=49.00, raggio=3.5, tipo="aperto", feh=0.0, plx_min=3.0),
    dict(chiave="ic2391", it="IC 2391", en="IC 2391", ra=130.13, dec=-53.04, raggio=1.5, tipo="aperto", feh=0.0, plx_min=3.0),
    dict(chiave="m7", it="M 7 (Tolomeo)", en="M 7 (Ptolemy)", ra=268.46, dec=-34.79, raggio=1.2, tipo="aperto", feh=0.0, plx_min=2.0),
    dict(chiave="ngc752", it="NGC 752", en="NGC 752", ra=29.42, dec=37.785, raggio=1.2, tipo="aperto", feh=0.0, plx_min=1.2),
    dict(chiave="m35", it="M 35", en="M 35", ra=92.225, dec=24.33, raggio=0.5, tipo="aperto", feh=-0.15),
    dict(chiave="m67", it="M 67", en="M 67", ra=132.825, dec=11.80, raggio=0.5, tipo="aperto", feh=0.0),
    dict(chiave="m11", it="M 11 (Anatra selvatica)", en="M 11 (Wild Duck)", ra=282.775, dec=-6.27, raggio=0.2, tipo="aperto", feh=0.1),
    dict(chiave="h_persei", it="h Persei (NGC 869)", en="h Persei (NGC 869)", ra=34.75, dec=57.13, raggio=0.25, tipo="aperto", feh=0.0),
    dict(chiave="chi_persei", it="χ Persei (NGC 884)", en="χ Persei (NGC 884)", ra=35.60, dec=57.13, raggio=0.25, tipo="aperto", feh=0.0),
    dict(chiave="ngc188", it="NGC 188", en="NGC 188", ra=12.11, dec=85.255, raggio=0.5, tipo="aperto", feh=0.0),
    dict(chiave="ngc6791", it="NGC 6791", en="NGC 6791", ra=290.22, dec=37.77, raggio=0.2, tipo="aperto", feh=0.3),
    dict(chiave="m13", it="M 13 (Ammasso di Ercole)", en="M 13 (Hercules Cluster)", ra=250.4235, dec=36.4613, raggio=0.25, tipo="globulare", feh=-1.53),
    dict(chiave="m3", it="M 3", en="M 3", ra=205.548, dec=28.377, raggio=0.25, tipo="globulare", feh=-1.50),
    dict(chiave="m5", it="M 5", en="M 5", ra=229.638, dec=2.081, raggio=0.3, tipo="globulare", feh=-1.29),
    dict(chiave="m4", it="M 4", en="M 4", ra=245.897, dec=-26.526, raggio=0.4, tipo="globulare", feh=-1.16),
    dict(chiave="m92", it="M 92", en="M 92", ra=259.281, dec=43.136, raggio=0.2, tipo="globulare", feh=-2.31),
    dict(chiave="omega_cen", it="ω Centauri", en="ω Centauri", ra=201.697, dec=-47.480, raggio=0.5, tipo="globulare", feh=-1.53),
    dict(chiave="47tuc", it="47 Tucanae", en="47 Tucanae", ra=6.024, dec=-72.081, raggio=0.4, tipo="globulare", feh=-0.72),
    dict(chiave="ngc6397", it="NGC 6397", en="NGC 6397", ra=265.175, dec=-53.674, raggio=0.4, tipo="globulare", feh=-2.02),
]


def ammasso_personalizzato(nome, ra, dec, raggio, tipo="aperto", feh=0.0):
    chiave = "pers_" + re.sub(r"[^a-z0-9]+", "_", nome.lower()).strip("_")[:40] + f"_{ra:.3f}_{dec:+.3f}"
    return dict(chiave=chiave, it=nome, en=nome, ra=float(ra), dec=float(dec), raggio=float(raggio),
                tipo=tipo, feh=float(feh))


def nome_ammasso(amm, lingua):
    return amm.get(lingua) or amm.get("it") or amm["chiave"]


def trova_ammasso(testo):
    t = re.sub(r"[\s_]+", "", testo.lower())
    for a in AMMASSI:
        nomi = [a["chiave"], a["it"], a["en"]]
        if any(t == re.sub(r"[\s_]+", "", n.lower()) for n in nomi):
            return a
    for a in AMMASSI:
        if any(t in re.sub(r"[\s_()]+", "", n.lower()) for n in (a["it"], a["en"])):
            return a
    return None


# --------------------------------------------------------------------------- rete
class ErroreRete(Exception):
    pass


def richiesta(url, dati=None, tempo=60, intestazioni=None):
    h = {"User-Agent": UA}
    if intestazioni:
        h.update(intestazioni)
    corpo = None
    if dati is not None:
        corpo = urllib.parse.urlencode(dati).encode("utf-8")
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=corpo, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=tempo) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        try:
            dettaglio = e.read(400).decode("utf-8", "replace")
        except Exception:
            dettaglio = ""
        raise ErroreRete(f"HTTP {e.code} da {urllib.parse.urlsplit(url).netloc}: {dettaglio[:200]}")
    except Exception as e:
        raise ErroreRete(f"{urllib.parse.urlsplit(url).netloc}: {type(e).__name__}: {e}")


def leggi_csv_gaia(testo_bytes):
    testo = testo_bytes.decode("utf-8", "replace")
    lettore = csv.reader(io.StringIO(testo))
    try:
        intest = [c.strip().lower() for c in next(lettore)]
    except StopIteration:
        raise ErroreRete("risposta vuota")
    if "source_id" not in intest:
        raise ErroreRete("risposta inattesa: " + testo[:200])
    righe = [r for r in lettore if r]
    dati = {}
    for j, nome in enumerate(intest):
        col = [r[j] if j < len(r) else "" for r in righe]
        if nome == "source_id":
            dati[nome] = np.array([int(v) if v else -1 for v in col], dtype=np.int64)
        else:
            dati[nome] = np.array([float(v) if v not in ("", "null", "NaN", "nan") else np.nan for v in col])
    return dati


def adql_gaia(amm, tabella, limite=150000):
    cond = [f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {amm['ra']:.5f}, {amm['dec']:.5f}, {amm['raggio']:.4f})) = 1",
            "pmra IS NOT NULL", "parallax IS NOT NULL", "phot_g_mean_mag IS NOT NULL"]
    if amm.get("plx_min"):
        cond.append(f"parallax > {amm['plx_min']}")
    if amm.get("plx_max"):
        cond.append(f"parallax < {amm['plx_max']}")
    gmax = amm.get("gmax", 19.5 if amm.get("tipo") == "globulare" else 19.0)
    cond.append(f"phot_g_mean_mag < {gmax}")
    return f"SELECT TOP {limite} {', '.join(CAMPI_GAIA)} FROM {tabella} WHERE " + " AND ".join(cond)


def scarica_gaia(amm, forza=False, avviso=print):
    """Restituisce (dati, fonte). Usa la copia locale se c'è."""
    CARTELLA_GAIA.mkdir(parents=True, exist_ok=True)
    impronta = hashlib.sha1(adql_gaia(amm, "T").encode()).hexdigest()[:10]
    file_cache = CARTELLA_GAIA / f"{amm['chiave']}_{impronta}.csv"
    file_fonte = file_cache.with_suffix(".fonte")
    if file_cache.exists() and not forza:
        fonte = file_fonte.read_text(encoding="utf-8").strip() if file_fonte.exists() else "Gaia DR3"
        return leggi_csv_gaia(file_cache.read_bytes()), fonte + " (copia locale)"
    errori = []
    tentativi = [
        ("Gaia DR3 – IRSA", URL_IRSA, lambda q: {"QUERY": q, "LANG": "ADQL", "FORMAT": "csv"}, "gaia_dr3_source"),
        ("Gaia DR3 – ESA", URL_ESA, lambda q: {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": q},
         "gaiadr3.gaia_source"),
    ]
    for nome, url, parametri, tabella in tentativi:
        try:
            avviso(f"  {nome}…")
            t0 = time.time()
            cont = richiesta(url, parametri(adql_gaia(amm, tabella)), tempo=240)
            dati = leggi_csv_gaia(cont)   # anche 0 stelle è una risposta valida (campo vuoto), non un errore di rete
            file_cache.write_bytes(cont)
            file_fonte.write_text(nome, encoding="utf-8")
            avviso(f"  {nome}: {len(dati['source_id'])} stelle in {time.time() - t0:.0f} s")
            return dati, nome
        except ErroreRete as e:
            errori.append(f"{nome}: {e}")
            avviso(f"  {nome} non risponde: {e}")
    raise ErroreRete("Nessun archivio Gaia ha risposto. " + " | ".join(errori))


# --------------------------------------------------------------------------- letteratura
def _leggi_tsv_vizier(testo):
    righe = [r for r in testo.splitlines()]
    dati, intest, salta = [], None, 0
    for r in righe:
        if not r.strip():
            if intest and dati:
                break
            continue
        if r.startswith("#"):
            continue
        if intest is None:
            intest = [c.strip() for c in r.split("\t")]
            salta = 2  # unità e trattini
            continue
        if salta:
            salta -= 1
            continue
        dati.append([c.strip() for c in r.split("\t")])
    return intest or [], dati


def _scegli(intest, riga, modelli, testo=False):
    for m in modelli:
        for j, nome in enumerate(intest):
            if re.search(m, nome, re.I) and j < len(riga):
                v = riga[j].strip()
                if testo:
                    if v:
                        return v
                    continue
                try:
                    return float(v)
                except ValueError:
                    continue   # colonna vuota o non numerica: provo la prossima
    return None


def _valori_lett(intest, riga):
    v = {
        "nome": _scegli(intest, riga, [r"^cluster$", r"^name$", r"^name"], testo=True),
        "log_eta": _scegli(intest, riga, [r"^log_?age_?50$", r"^agenn$", r"^log_?age$", r"^log_?t_?50$", r"^log_?t$",
                                          r"^log.?age", r"^age.?50$"]),
        "av": _scegli(intest, riga, [r"^a_?v_?50$", r"^avnn$", r"^a_?v$", r"^a_?0_?50$", r"^a_?v"]),
        "dm": _scegli(intest, riga, [r"^(mod|dm|distmod)_?50$", r"^dmnn$", r"^(dm|mod)$"]),
        "dist_pc": _scegli(intest, riga, [r"^dist(pc)?50$", r"^distpc$", r"^dist"]),
        "plx": _scegli(intest, riga, [r"^plx$", r"^plx"]),
        "pmra": _scegli(intest, riga, [r"^pmra\*?$", r"^pmra"]),
        "pmdec": _scegli(intest, riga, [r"^pmde(c)?$", r"^pmde"]),
    }
    for k in ("log_eta", "av", "dm", "dist_pc", "plx", "pmra", "pmdec"):
        if not isinstance(v[k], float) or not math.isfinite(v[k]):
            v[k] = None
    if v["dm"] is None and v["dist_pc"]:
        v["dm"] = 5 * math.log10(v["dist_pc"]) - 5
    if v["dist_pc"] is None and v["dm"] is not None:
        v["dist_pc"] = 10 ** (v["dm"] / 5 + 1)
    if v["log_eta"] is not None and v["log_eta"] > 20:      # età in anni o milioni di anni, non in log
        v["log_eta"] = math.log10(v["log_eta"] * (1 if v["log_eta"] > 1e5 else 1e6))
    v["_colonne"] = {n: (riga[j] if j < len(riga) else "") for j, n in enumerate(intest)}
    return v


VARIANTI_VIZIER = ({"-out.all": ""}, {"-out": "**"}, {"-out.all": "1"}, {"-out": "*"})


def _vizier_testo(cat, amm, rd, colonne):
    param = {"-source": cat, "-c": f"{amm['ra']:.5f} {amm['dec']:+.5f}", "-c.rd": f"{rd:.3f}",
             **colonne, "-out.add": "_r", "-sort": "_r", "-out.max": "3"}
    return richiesta(URL_VIZIER + "?" + urllib.parse.urlencode(param), tempo=40).decode("utf-8", "replace")


def letteratura(amm, avviso=print, forza=False):
    """{'CG20': dict|None, 'HR23': dict|None}. Non solleva errori."""
    CARTELLA_LETT.mkdir(parents=True, exist_ok=True)
    f = CARTELLA_LETT / f"{amm['chiave']}_v4.json"
    if f.exists() and not forza:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    risultato, completo = {}, True
    rd = max(0.15, min(1.0, amm["raggio"] / 3))
    for sigla, cat in (("CG20", CAT_CG20), ("HR23", CAT_HR23)):
        try:
            risultato[sigla] = None
            # prima la richiesta che funziona (CG20 completo); se l'età manca provo altre forme
            for colonne in VARIANTI_VIZIER:
                testo = _vizier_testo(cat, amm, rd, colonne)
                intest, righe = _leggi_tsv_vizier(testo)
                if righe:
                    v = _valori_lett(intest, righe[0])
                    if risultato[sigla] is None or (v.get("log_eta") is not None):
                        risultato[sigla] = v
                    if v.get("log_eta") is not None:
                        break
        except ErroreRete as e:
            avviso(f"  letteratura {sigla}: {e}")
            risultato[sigla] = None
            completo = False
    if completo:
        f.write_text(json.dumps(risultato, ensure_ascii=False, indent=1), encoding="utf-8")
    return risultato


# --------------------------------------------------------------------------- membri
def _log_gauss3(X, mu, sigma, S):
    """log N(x | mu, sigma + diag(S)) per ogni stella. X (N,3), S (N,3)."""
    C = sigma[None, :, :] + S[:, :, None] * np.eye(3)[None, :, :]
    inv = np.linalg.inv(C)
    _, logdet = np.linalg.slogdet(C)
    d = X - mu
    m = np.einsum("ni,nij,nj->n", d, inv, d)
    return -0.5 * (m + logdet + 3 * math.log(2 * math.pi))


def _scala_robusta(v):
    med = np.nanmedian(v)
    mad = np.nanmedian(np.abs(v - med)) * 1.4826
    return med, (mad if mad > 0 else (np.nanstd(v) or 1.0))


def _inizio_hdbscan(X, atteso, avviso):
    N = len(X)
    scale = np.array([_scala_robusta(X[:, j])[1] for j in range(3)])
    Z = (X - np.median(X, axis=0)) / scale
    rng = np.random.default_rng(1)
    idx = rng.choice(N, size=min(N, 20000), replace=False) if N > 20000 else np.arange(N)
    mcs = int(max(15, min(200, 0.004 * len(idx))))
    etichette = HDBSCAN(min_cluster_size=mcs, min_samples=10, copy=True).fit_predict(Z[idx])
    gruppi = [g for g in set(etichette) if g >= 0]
    if not gruppi:
        return None
    migliore, punteggio_migliore = None, None
    for g in gruppi:
        sel = idx[etichette == g]
        if len(sel) < 10:
            continue
        med = np.median(X[sel], axis=0)
        dsp = np.std(X[sel], axis=0) + 1e-3
        if atteso is not None:
            # il più vicino ai valori di letteratura (in unità della dispersione del campo)
            punt = -np.sum(((med - atteso) / scale) ** 2)
        else:
            # il più numeroso e compatto nei moti propri
            punt = math.log(len(sel)) - math.log(dsp[0] * dsp[1])
        if punteggio_migliore is None or punt > punteggio_migliore:
            migliore, punteggio_migliore = sel, punt
    if migliore is None:
        return None
    avviso(f"  HDBSCAN: {len(gruppi)} gruppi, scelto quello con {len(migliore)} stelle")
    return migliore


def _inizio_picco(X, atteso):
    if atteso is not None:
        centro = atteso.copy()
    else:
        pa, pd = X[:, 0], X[:, 1]
        lo = np.percentile(X[:, :2], 1, axis=0)
        hi = np.percentile(X[:, :2], 99, axis=0)
        H, ex, ey = np.histogram2d(pa, pd, bins=200, range=[[lo[0], hi[0]], [lo[1], hi[1]]])
        D = gaussian_filter(H, 1.5) - gaussian_filter(H, 15)
        i, j = np.unravel_index(np.argmax(D), D.shape)
        cx, cy = 0.5 * (ex[i] + ex[i + 1]), 0.5 * (ey[j] + ey[j + 1])
        vicino = (pa - cx) ** 2 + (pd - cy) ** 2 < (0.03 * max(hi[0] - lo[0], hi[1] - lo[1])) ** 2
        plx = np.median(X[vicino, 2]) if vicino.sum() > 3 else np.median(X[:, 2])
        centro = np.array([cx, cy, plx])
    d = np.sqrt((X[:, 0] - centro[0]) ** 2 + (X[:, 1] - centro[1]) ** 2)
    raggio = max(0.3, np.percentile(d, 2))
    return np.where(d < raggio)[0]


def calcola_membri(dati, amm, lett=None, avviso=print, iterazioni=200):
    """Probabilità di appartenenza (0-1) per ogni stella e un riassunto."""
    X = np.column_stack([dati["pmra"], dati["pmdec"], dati["parallax"]])
    E = np.column_stack([dati["pmra_error"], dati["pmdec_error"], dati["parallax_error"]])
    E = np.where(np.isfinite(E), E, 1.0)
    ok = np.all(np.isfinite(X), axis=1)
    p_tutte = np.zeros(len(X))
    X, S = X[ok], E[ok] ** 2
    N = len(X)
    if N < 30:
        raise ValueError("troppe poche stelle con moti propri e parallasse")
    # posizione in cielo (piano tangente, gradi)
    c0 = math.cos(math.radians(amm["dec"]))
    dra = ((dati["ra"][ok] - amm["ra"] + 180) % 360 - 180) * c0
    ddec = dati["dec"][ok] - amm["dec"]
    R = amm["raggio"]

    atteso = None
    for sigla in ("HR23", "CG20"):
        v = (lett or {}).get(sigla) or {}
        if v.get("pmra") is not None and v.get("pmdec") is not None and v.get("plx") is not None:
            atteso = np.array([v["pmra"], v["pmdec"], v["plx"]])
            break
    sel = None
    if HA_SKLEARN:
        try:
            sel = _inizio_hdbscan(X, atteso, avviso)
        except Exception as e:  # pragma: no cover
            avviso(f"  HDBSCAN non riuscito ({e}), uso il picco dei moti propri")
    if sel is None or len(sel) < 10:
        sel = _inizio_picco(X, atteso)
        avviso(f"  partenza dal picco dei moti propri: {len(sel)} stelle")

    # componenti: 0 = ammasso, 1 e 2 = campo (stretto e largo)
    mu_c = np.median(X[sel], axis=0)
    sig_c = np.cov(X[sel].T) + np.diag([1e-3, 1e-3, 1e-4])
    med_f = np.median(X, axis=0)
    cov_f = np.cov(X.T)
    mu = [mu_c, med_f, med_f.copy()]
    sig = [sig_c, cov_f, cov_f * 6]
    frazione = len(sel) / N
    pesi = np.array([max(0.02, min(0.8, frazione)), 0.0, 0.0])
    pesi[1:] = (1 - pesi[0]) * np.array([0.7, 0.3])
    xc, yc = np.median(dra[sel]), np.median(ddec[sel])
    sig_s = max(0.03 * R, min(R, math.sqrt(np.mean((dra[sel] - xc) ** 2 + (ddec[sel] - yc) ** 2) / 2)))
    log_campo_spazio = -math.log(math.pi * R ** 2)
    pavimento = np.diag([0.01 ** 2, 0.01 ** 2, 0.003 ** 2])
    precedente = None
    for it in range(iterazioni):
        logL = np.empty((N, 3))
        for k in range(3):
            logL[:, k] = math.log(max(pesi[k], 1e-12)) + _log_gauss3(X, mu[k], sig[k], S)
        logL[:, 0] += -0.5 * ((dra - xc) ** 2 + (ddec - yc) ** 2) / sig_s ** 2 - math.log(2 * math.pi * sig_s ** 2)
        logL[:, 1:] += log_campo_spazio
        m = logL.max(axis=1, keepdims=True)
        resp = np.exp(logL - m)
        resp /= resp.sum(axis=1, keepdims=True)
        # passo M
        for k in range(3):
            r = resp[:, k]
            sr = r.sum()
            if sr < 1e-6:
                continue
            pesi[k] = sr / N
            mu[k] = (r[:, None] * X).sum(0) / sr
            d = X - mu[k]
            cov = np.einsum("n,ni,nj->ij", r, d, d) / sr
            if k == 0:
                cov = cov - np.diag((r[:, None] * S).sum(0) / sr)   # tolgo gli errori di misura
                w, v = np.linalg.eigh(cov)
                cov = (v * np.maximum(w, 1e-6)) @ v.T + pavimento
            sig[k] = cov
        r0 = resp[:, 0]
        if it >= 3 and r0.sum() > 5:
            xc = float((r0 * dra).sum() / r0.sum())
            yc = float((r0 * ddec).sum() / r0.sum())
            xc, yc = max(-R / 2, min(R / 2, xc)), max(-R / 2, min(R / 2, yc))
            s2 = (r0 * ((dra - xc) ** 2 + (ddec - yc) ** 2)).sum() / (2 * r0.sum())
            sig_s = max(0.01 * R, min(R, math.sqrt(s2)))
        pesi = np.clip(pesi, 1e-4, None)
        pesi /= pesi.sum()
        if precedente is not None and np.max(np.abs(r0 - precedente)) < 1e-4:
            break
        precedente = r0.copy()
    p_tutte[ok] = resp[:, 0]
    info = dict(n_stelle=int(len(p_tutte)), n_membri=int((p_tutte >= 0.5).sum()),
                pmra=float(mu[0][0]), pmdec=float(mu[0][1]), plx=float(mu[0][2]),
                disp_pm=float(math.sqrt(max(sig[0][0, 0], 0) + max(sig[0][1, 1], 0)) / math.sqrt(2)),
                raggio_ammasso=float(sig_s), iterazioni=it + 1, metodo="HDBSCAN + EM" if HA_SKLEARN else "EM")
    return p_tutte, info


# --------------------------------------------------------------------------- isocrone MIST
def _colonna(colonne, candidati):
    for c in candidati:
        if c in colonne:
            return colonne.index(c)
    return None


def leggi_iso_cmd(testo, feh, eta_min=6.0, eta_max=10.2):
    """Legge un file .iso.cmd di MIST. Restituisce una lista di blocchi (feh, log_eta, dict di array)."""
    blocchi = []
    colonne, righe = None, []

    def chiudi():
        nonlocal righe
        if colonne and righe:
            n = len(colonne)
            valori = " ".join(righe).split()
            if len(valori) % n == 0:
                A = np.array(valori, dtype=float).reshape(-1, n)
                _aggiungi(A)
        righe = []

    def _aggiungi(A):
        ie = colonne.index("log10_isochrone_age_yr")
        eta = round(float(A[0, ie]), 2)
        if not (eta_min - 1e-6 <= eta <= eta_max + 1e-6):
            return
        ig = _colonna(colonne, ["Gaia_G_EDR3", "Gaia_G_DR2Rev", "Gaia_G_MAW"])
        ib = _colonna(colonne, ["Gaia_BP_EDR3", "Gaia_BP_DR2Rev", "Gaia_BP_MAWf"])
        ir = _colonna(colonne, ["Gaia_RP_EDR3", "Gaia_RP_DR2Rev", "Gaia_RP_MAW"])
        if None in (ig, ib, ir):
            raise ValueError("nel file MIST mancano le bande Gaia")
        ifase = _colonna(colonne, ["phase"])
        fase = A[:, ifase] if ifase is not None else np.zeros(len(A))
        tieni = (fase >= -1) & (fase <= 5)
        blocchi.append((float(feh), eta, dict(
            massa=A[tieni, colonne.index("initial_mass")], G=A[tieni, ig], BP=A[tieni, ib], RP=A[tieni, ir],
            fase=fase[tieni], logT=A[tieni, colonne.index("log_Teff")], logL=A[tieni, colonne.index("log_L")])))

    for riga in testo.splitlines():
        s = riga.strip()
        if not s:
            chiudi()
            continue
        if s.startswith("#"):
            chiudi()
            if "EEP" in s and "log10_isochrone_age_yr" in s:
                colonne = s.lstrip("#").split()
            continue
        if colonne:
            righe.append(s)
    chiudi()
    return blocchi


def feh_da_nome(nome):
    m = re.search(r"feh_([mp])(\d+\.\d+)", nome)
    if not m:
        return None
    return (-1 if m.group(1) == "m" else 1) * float(m.group(2))


class GrigliaIsocrone:
    """Isocrone in memoria: (feh, log_eta) -> dict di array."""

    def __init__(self, blocchi, origine=""):
        self.origine = origine
        self.dati = {}
        for feh, eta, d in blocchi:
            self.dati[(round(feh, 2), round(eta, 2))] = d
        self.feh_valori = sorted({k[0] for k in self.dati})
        self._eta = {f: sorted(k[1] for k in self.dati if k[0] == f) for f in self.feh_valori}

    def eta_valori(self, feh):
        return self._eta[self.feh_vicina(feh)]

    def feh_vicina(self, feh):
        return min(self.feh_valori, key=lambda f: abs(f - feh))

    def eta_vicina(self, feh, eta):
        return min(self.eta_valori(feh), key=lambda e: abs(e - eta))

    def isocrona(self, feh, eta):
        f = self.feh_vicina(feh)
        e = self.eta_vicina(f, eta)
        d = self.dati[(f, e)]
        return dict(colore=d["BP"] - d["RP"], G=d["G"], massa=d["massa"], fase=d["fase"], feh=f, eta=e)

    # salvataggio compatto
    def salva(self, percorso):
        chiavi = sorted(self.dati)
        parti = {k: [] for k in ("massa", "G", "BP", "RP", "fase", "logT", "logL")}
        inizio, fine, n = [], [], 0
        for c in chiavi:
            d = self.dati[c]
            for k in parti:
                parti[k].append(d[k].astype(np.float32))
            inizio.append(n)
            n += len(d["G"])
            fine.append(n)
        np.savez_compressed(percorso, feh=np.array([c[0] for c in chiavi]), eta=np.array([c[1] for c in chiavi]),
                            inizio=np.array(inizio), fine=np.array(fine), origine=np.array(self.origine),
                            **{k: np.concatenate(v) for k, v in parti.items()})

    @classmethod
    def carica(cls, percorso):
        with np.load(percorso) as z:
            a = {k: z[k] for k in z.files}   # ogni array letto UNA volta (prima: migliaia di volte, minuti)
        chiavi = ("massa", "G", "BP", "RP", "fase", "logT", "logL")
        blocchi = [(float(f), float(e), {k: a[k][i:j].astype(float) for k in chiavi})
                   for f, e, i, j in zip(a["feh"], a["eta"], a["inizio"], a["fine"])]
        return cls(blocchi, str(a["origine"]))


def file_griglia():
    return CARTELLA_MIST / f"{NOME_MIST}_gaia.npz"


def griglia_pronta():
    return file_griglia().exists()


def carica_griglia():
    return GrigliaIsocrone.carica(file_griglia())


def scarica_e_prepara_mist(avviso=print, progresso=None):
    """Scarica l'archivio MIST (riprende se interrotto) e prepara la griglia compatta."""
    CARTELLA_MIST.mkdir(parents=True, exist_ok=True)
    archivio = CARTELLA_MIST / f"{NOME_MIST}.txz"
    parziale = archivio.with_suffix(".txz.part")
    if not archivio.exists():
        gia = parziale.stat().st_size if parziale.exists() else 0
        h = {"User-Agent": UA}
        if gia:
            h["Range"] = f"bytes={gia}-"
        req = urllib.request.Request(URL_MIST, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if gia and r.status != 206:
                    gia = 0
                totale = gia + int(r.headers.get("Content-Length", "0") or 0)
                with open(parziale, "ab" if gia else "wb") as f:
                    fatto = gia
                    while True:
                        blocco = r.read(1 << 20)
                        if not blocco:
                            break
                        f.write(blocco)
                        fatto += len(blocco)
                        if progresso and totale:
                            progresso(fatto / totale, fatto / 1e6, totale / 1e6)
        except Exception as e:
            raise ErroreRete(f"download MIST interrotto ({e}); riprova: riprende da dove si era fermato")
        if totale and parziale.stat().st_size < totale:
            raise ErroreRete("download MIST incompleto; riprova: riprende da dove si era fermato")
        parziale.rename(archivio)
    avviso("  leggo l'archivio MIST…")
    blocchi = []
    with tarfile.open(archivio, "r:xz") as tar:
        membri = [m for m in tar.getmembers() if m.isfile() and m.name.endswith(".iso.cmd")]
        for i, m in enumerate(sorted(membri, key=lambda m: m.name)):
            feh = feh_da_nome(m.name)
            if feh is None:
                continue
            testo = tar.extractfile(m).read().decode("ascii", "replace")
            blocchi += leggi_iso_cmd(testo, feh)
            avviso(f"  [Fe/H] = {feh:+.2f} ({i + 1}/{len(membri)})")
            if progresso:
                progresso((i + 1) / len(membri), None, None)
    if not blocchi:
        raise ValueError("nell'archivio MIST non ho trovato isocrone leggibili")
    g = GrigliaIsocrone(blocchi, NOME_MIST)
    g.salva(file_griglia())
    return g


# --------------------------------------------------------------------------- adattamento
ETA_MAX = 10.15    # log età massima cercata: 14,1 miliardi di anni (l'Universo ha 13,8)
SC_COLORE = 0.04   # mag: incertezza tipica in colore usata per pesare le distanze
SC_G = 0.12        # mag: in G (la sequenza principale è ripida)
TETTO_CHI2 = 9.0   # stelle lontane dall'isocrona (campo) contano al massimo così
TETTO_BINARIE = 1.5  # stelle sopra l'isocrona (binarie): tetto più basso, tirano poco


def _punti_densi(colore, G, passo=0.4):
    """Isocrona campionata fitta nello spazio scalato (per la ricerca del punto più vicino)."""
    x, y = colore / SC_COLORE, G / SC_G
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 2:
        return np.column_stack([x, y])
    dx, dy = np.diff(x), np.diff(y)
    lung = np.hypot(dx, dy)
    pezzi = [np.column_stack([x[:1], y[:1]])]
    for i in range(len(dx)):
        if lung[i] > 60:   # salto (es. tra fasi): non lo riempio
            pezzi.append(np.array([[x[i + 1], y[i + 1]]]))
            continue
        n = max(1, int(math.ceil(lung[i] / passo)))
        t = np.arange(1, n + 1) / n
        pezzi.append(np.column_stack([x[i] + t * dx[i], y[i] + t * dy[i]]))
    return np.vstack(pezzi)


def colori_buoni(dati):
    """Vero per le stelle con colore BP−RP affidabile: eccesso di flusso BP+RP corretto C* entro 3 sigma
    (Riello et al. 2021). Nei campi affollati (globulari) il flusso delle stelle vicine contamina BP e RP."""
    n = len(dati["phot_g_mean_mag"])
    C = dati.get("phot_bp_rp_excess_factor")
    if C is None:
        return np.ones(n, bool)
    x = dati["bp_rp"]
    G = dati["phot_g_mean_mag"]
    f = np.where(x < 0.5, 1.154360 + 0.033772 * x + 0.032277 * x ** 2,
                 np.where(x < 4.0, 1.162004 + 0.011464 * x + 0.049255 * x ** 2 - 0.005879 * x ** 3,
                          1.057572 + 0.140537 * x))
    c_star = C - f
    sigma = 0.0059898 + 8.817481e-12 * G ** 7.618399
    buono = np.abs(c_star) < 3 * sigma
    return np.where(np.isfinite(C) & np.isfinite(x), buono, False) | ~np.isfinite(C)


MG_MAX = 6.0   # sotto (più deboli di) M_G = 6 i colori MIST delle stelle K e M non combaciano con Gaia


def stelle_per_adattamento(dati, p, soglia=0.7, tipo="aperto", dm=None):
    """Membri con fotometria buona; se si conosce il modulo di distanza, solo le stelle con M_G < MG_MAX."""
    c = dati["bp_rp"]
    G = dati["phot_g_mean_mag"]
    ruwe = dati.get("ruwe", np.ones(len(G)))
    gmax = 19.0 if tipo == "globulare" else 18.0
    if dm is not None:
        gmax = min(gmax, dm + MG_MAX)
    ok = (p >= soglia) & np.isfinite(c) & np.isfinite(G) & (G < gmax) & (G > 4.0)
    ok &= ~(np.isfinite(ruwe) & (ruwe > 1.4))
    ok &= colori_buoni(dati)
    return c[ok], G[ok], p[ok]


def distanza_da_parallasse(dati, p, soglia=0.7):
    plx, e = dati["parallax"], dati["parallax_error"]
    ruwe = dati.get("ruwe", np.ones(len(plx)))
    ok = (p >= soglia) & np.isfinite(plx) & np.isfinite(e) & (e > 0) & ~(np.isfinite(ruwe) & (ruwe > 1.4))
    if ok.sum() < 5:
        return None
    w = 1 / e[ok] ** 2
    media = float((plx[ok] * w).sum() / w.sum()) + ZP_PARALLASSE
    sig = math.sqrt(1 / w.sum() + 0.015 ** 2)
    # dispersione reale (ammassi vicini e profondi come le Iadi)
    sig = max(sig, float(np.std(plx[ok])) / math.sqrt(ok.sum()))
    res = dict(plx=media, plx_err=sig, n=int(ok.sum()))
    if media / sig > 4:
        res["dm"] = 5 * math.log10(1000 / media) - 5
        res["dm_err"] = 5 / math.log(10) * sig / media
    return res


def _costo(albero, c, G, w, av_val, dm_val, gruppi=None):
    """Costi per tutte le combinazioni (av, dm) con un'isocrona.
    Con gruppi (indici di inizio dei gruppi di stelle) restituisce anche le somme per gruppo."""
    n = len(c)
    out = np.empty((len(av_val), len(dm_val)) if gruppi is None else (len(av_val), len(dm_val), len(gruppi)))
    for i, av in enumerate(av_val):
        x = (c - COEFF_EBR * av) / SC_COLORE
        yb = (G - COEFF_AG * av) / SC_G
        Y = (yb[None, :] - np.asarray(dm_val)[:, None] / SC_G).ravel()
        P = np.column_stack([np.tile(x, len(dm_val)), Y])
        d, idx = albero.query(P, distance_upper_bound=math.sqrt(TETTO_CHI2))
        vicino = np.isfinite(d)
        d2 = np.full(len(d), TETTO_CHI2)
        d2[vicino] = d[vicino] ** 2
        # le stelle più brillanti dell'isocrona (binarie) contano al massimo TETTO_BINARIE
        sopra = np.zeros(len(d), bool)
        sopra[vicino] = P[vicino, 1] < albero.data[idx[vicino], 1]
        d2 = np.where(sopra, np.minimum(d2, TETTO_BINARIE), d2).reshape(len(dm_val), n)
        if gruppi is None:
            out[i] = (d2 * w[None, :]).sum(axis=1)
        else:
            out[i] = np.add.reduceat(d2 * w[None, :], gruppi, axis=1)
    return out


def adatta_isocrone(c, G, w, griglia, feh, dist=None, tipo="aperto", avviso=print, av_max=None, peso_prior=0.1):
    """Cerca log età, modulo di distanza e A_V che avvicinano di più le stelle all'isocrona."""
    if len(c) < 15:
        raise ValueError("troppi pochi membri con fotometria buona per l'adattamento")
    rng = np.random.default_rng(2)
    feh_g = griglia.feh_vicina(feh)
    eta_tutte = np.array([e for e in griglia.eta_valori(feh_g) if e <= ETA_MAX + 1e-6])
    if av_max is None:
        av_max = 3.0
    if dist and "dm" in dist:
        dm_c, dm_s = dist["dm"], dist["dm_err"]
        mezzo = max(4 * dm_s, 0.3)
        dm_lo, dm_hi = dm_c - mezzo, dm_c + mezzo
    else:
        dm_c, dm_s = None, None
        dm_lo, dm_hi = 8.0, 19.5

    def prior(dm_val, pesi=None):
        """Vincolo della parallasse: 1 sigma di scarto costa quanto il peso_prior delle stelle usate."""
        if dm_c is None:
            return np.zeros(len(dm_val))
        tot = (w if pesi is None else pesi).sum()
        return peso_prior * tot * ((np.asarray(dm_val) - dm_c) / dm_s) ** 2

    # passo grosso
    if len(c) > 1200:
        k = rng.choice(len(c), 1200, replace=False)
        cg, Gg, wg = c[k], G[k], w[k]
    else:
        cg, Gg, wg = c, G, w
    eta_g = eta_tutte[::2]
    av_g = np.arange(0.0, av_max + 1e-9, 0.1)
    passo_dm = 0.1 if (dm_hi - dm_lo) / 0.1 <= 40 else 0.25
    dm_g = np.arange(dm_lo, dm_hi + 1e-9, passo_dm)
    migliore = (np.inf, None)
    alberi = {}
    for j, e in enumerate(eta_g):
        iso = griglia.isocrona(feh_g, e)
        alberi[e] = cKDTree(_punti_densi(iso["colore"], iso["G"]))
        C = _costo(alberi[e], cg, Gg, wg, av_g, dm_g) + prior(dm_g, wg)[None, :]
        i, k2 = np.unravel_index(np.argmin(C), C.shape)
        if C[i, k2] < migliore[0]:
            migliore = (C[i, k2], (e, av_g[i], dm_g[k2]))
        if j % 10 == 0:
            avviso(f"  isocrone: {j + 1}/{len(eta_g)}")
    e0, av0, dm0 = migliore[1]

    # passo fine attorno al minimo, con tutte le stelle (al massimo 3000), divise in 20 gruppi
    # casuali: ricampionando i gruppi (bootstrap) si stima l'incertezza dei parametri
    k = rng.permutation(len(c))[:3000]
    cf, Gf, wf = c[k], G[k], w[k]
    n_gruppi = int(min(20, max(5, len(cf) // 15)))
    gruppi = np.linspace(0, len(cf), n_gruppi + 1).astype(int)[:-1]
    eta_f = eta_tutte[(eta_tutte >= e0 - 0.25 - 1e-6) & (eta_tutte <= e0 + 0.25 + 1e-6)]
    av_f = np.arange(max(0.0, av0 - 0.3), min(av_max, av0 + 0.3) + 1e-9, 0.02)
    dm_f = np.arange(max(dm_lo, dm0 - 0.4), min(dm_hi, dm0 + 0.4) + 1e-9, 0.02)
    cubo_g = np.empty((len(eta_f), len(av_f), len(dm_f), n_gruppi))
    for j, e in enumerate(eta_f):
        if e not in alberi:
            iso = griglia.isocrona(feh_g, e)
            alberi[e] = cKDTree(_punti_densi(iso["colore"], iso["G"]))
        cubo_g[j] = _costo(alberi[e], cf, Gf, wf, av_f, dm_f, gruppi)
    pr = prior(dm_f, wf)[None, None, :]
    cubo = cubo_g.sum(axis=3) + pr
    j, i, k2 = np.unravel_index(np.argmin(cubo), cubo.shape)
    cmin = cubo[j, i, k2]
    neff = max(wf.sum() - 3, 1.0)
    # bootstrap sui gruppi
    campioni = []
    for _ in range(300):
        conteggi = rng.multinomial(n_gruppi, np.full(n_gruppi, 1 / n_gruppi)).astype(float)
        cb = cubo_g @ conteggi + pr
        jb, ib, kb = np.unravel_index(np.argmin(cb), cb.shape)
        campioni.append((eta_f[jb], av_f[ib], dm_f[kb]))
    campioni = np.array(campioni)

    def errore(colonna, sistematico):
        # statistico (bootstrap) e sistematico del metodo (binarie, modelli), sommati in quadratura;
        # i sistematici vengono dalle prove con ammassi simulati con il 20-30 % di binarie
        lo, hi = np.percentile(campioni[:, colonna], [16, 84])
        return float(math.hypot((hi - lo) / 2, sistematico))

    res = dict(feh=float(feh_g), log_eta=float(eta_f[j]), av=float(av_f[i]), dm=float(dm_f[k2]),
               err_log_eta=errore(0, 0.10), err_av=errore(1, 0.05), err_dm=errore(2, 0.03),
               chi2_ridotto=float(cmin / neff), n_stelle=int(len(cf)),
               bordo_eta=bool(j in (0, len(eta_f) - 1) and eta_f[j] not in (eta_tutte[0], eta_tutte[-1])))
    res["dist_pc"] = 10 ** (res["dm"] / 5 + 1)
    res["err_dist_pc"] = res["dist_pc"] * math.log(10) / 5 * res["err_dm"]
    dubbi = []
    if res["log_eta"] >= eta_tutte[-1] - 1e-6:
        dubbi.append("eta_max")
    if res["log_eta"] <= eta_tutte[0] + 1e-6:
        dubbi.append("eta_min")
    if res["av"] >= av_max - 0.021 or (res["av"] <= 0.0 + 1e-6 and False):
        dubbi.append("av_max")
    if abs(res["dm"] - dm_lo) < 0.021 or abs(res["dm"] - dm_hi) < 0.021:
        dubbi.append("dm_bordo")
    res["dubbi"] = dubbi
    return res


def isocrona_spostata(griglia, feh, log_eta, dm, av):
    iso = griglia.isocrona(feh, log_eta)
    return dict(colore=iso["colore"] + COEFF_EBR * av, G=iso["G"] + dm + COEFF_AG * av,
                massa=iso["massa"], fase=iso["fase"], feh=iso["feh"], eta=iso["eta"])


# --------------------------------------------------------------------------- analisi completa
def analizza(amm, griglia=None, forza=False, avviso=print, soglia=0.7):
    an = dict(ammasso=amm, versione=VERSIONE, data=time.strftime("%Y-%m-%d %H:%M"), soglia=soglia)
    avviso("Letteratura…")
    an["lett"] = letteratura(amm, avviso)
    avviso("Gaia DR3…")
    an["dati"], an["fonte"] = scarica_gaia(amm, forza, avviso)
    avviso("Membri…")
    an["p"], an["membri"] = calcola_membri(an["dati"], amm, an["lett"], avviso)
    avviso(f"  {an['membri']['n_membri']} membri (p ≥ 0,5) su {an['membri']['n_stelle']} stelle")
    an["dist_plx"] = distanza_da_parallasse(an["dati"], an["p"], soglia)
    an["adattamento"] = None
    if griglia is not None:
        avviso("Isocrone…")
        an["adattamento"] = riadatta(an, griglia, amm.get("feh", 0.0), avviso)
    return an


def riadatta(an, griglia, feh, avviso=print):
    dp = an.get("dist_plx") or {}
    c, G, w = stelle_per_adattamento(an["dati"], an["p"], an["soglia"], an["ammasso"].get("tipo", "aperto"),
                                     dp.get("dm"))
    av_max = 3.0 if an["ammasso"].get("tipo") == "aperto" else 2.0
    res = adatta_isocrone(c, G, w, griglia, feh, an.get("dist_plx"), an["ammasso"].get("tipo"), avviso, av_max)
    avviso(f"  log età {res['log_eta']:.2f} ± {res['err_log_eta']:.2f}, m−M {res['dm']:.2f} ± {res['err_dm']:.2f}, "
           f"A_V {res['av']:.2f} ± {res['err_av']:.2f}, [Fe/H] {res['feh']:+.2f}")
    return res


# --------------------------------------------------------------------------- testi dei risultati
def testo_eta(log_eta, lingua, err=None):
    anni = 10 ** log_eta
    if anni >= 1e9:
        v, u = anni / 1e9, T(lingua, "miliardi")
    else:
        v, u = anni / 1e6, T(lingua, "milioni")
    s = f"{v:.2f}" if v < 10 else f"{v:.0f}" if v >= 100 else f"{v:.1f}"
    if err:
        lo, hi = 10 ** (log_eta - err), 10 ** (log_eta + err)
        div = 1e9 if anni >= 1e9 else 1e6

        def corto(x):
            return f"{x:.2f}" if x < 10 else f"{x:.1f}" if x < 100 else f"{x:.0f}"
        s += f" ({corto(lo / div)}–{corto(hi / div)})"
    if lingua == "it":
        s = s.replace(".", ",")
    return f"{s} {u}"


def num(v, cifre=2, lingua="it"):
    if v is None:
        return "–"
    s = f"{v:.{cifre}f}"
    return s.replace(".", ",") if lingua == "it" else s


def intero(v, lingua="it"):
    s = f"{v:,.0f}"
    return s.replace(",", ".") if lingua == "it" else s


def righe_risultati(an, lingua, valori=None):
    """Righe di testo (etichetta, valore) per la scheda."""
    v = valori or an.get("adattamento") or {}
    r = []
    if v:
        r.append((T(lingua, "eta"), testo_eta(v["log_eta"], lingua, v.get("err_log_eta"))))
        r.append((T(lingua, "log_eta"), f"{num(v['log_eta'], 2, lingua)} ± {num(v.get('err_log_eta'), 2, lingua)}"))
        d = 10 ** (v["dm"] / 5 + 1)
        ed = v.get("err_dist_pc")
        testo_d = f"{intero(d, lingua)} {T(lingua, 'pc')}"
        if ed:
            testo_d += f" ± {intero(ed, lingua)}"
        testo_d += f"  ({intero(d * 3.2616, lingua)} {T(lingua, 'anni_luce')})"
        r.append((T(lingua, "distanza"), testo_d))
        r.append((T(lingua, "dm"), f"{num(v['dm'], 2, lingua)} ± {num(v.get('err_dm'), 2, lingua)}"))
        r.append((T(lingua, "av"), f"{num(v['av'], 2, lingua)} ± {num(v.get('err_av'), 2, lingua)}"))
        r.append((T(lingua, "metallicita"), f"{v['feh']:+.2f}".replace(".", "," if lingua == "it" else ".")))
        if v.get("dubbi"):
            r.append(("⚠ " + T(lingua, "dubbio"), "; ".join(T(lingua, d) for d in v["dubbi"])))
    m = an.get("membri") or {}
    if m:
        r.append((T(lingua, "membri"), f"{m['n_membri']} / {m['n_stelle']}"))
        r.append(("μα*, μδ", f"{num(m['pmra'], 2, lingua)}, {num(m['pmdec'], 2, lingua)} mas/a"))
    dp = an.get("dist_plx")
    if dp:
        r.append((T(lingua, "parallasse"), f"{num(dp['plx'], 3, lingua)} ± {num(dp['plx_err'], 3, lingua)}"))
    return r


def righe_letteratura(an, lingua):
    r = []
    for sigla, nome in (("CG20", "Cantat-Gaudin et al. 2020"), ("HR23", "Hunt & Reffert 2023")):
        v = (an.get("lett") or {}).get(sigla)
        if not v or v.get("log_eta") is None:
            r.append((nome, T(lingua, "non_trovato")))
            continue
        testo = f"{testo_eta(v['log_eta'], lingua)}; A_V {num(v.get('av'), 2, lingua)}"
        if v.get("dist_pc"):
            testo += f"; {v['dist_pc']:.0f} {T(lingua, 'pc')}"
        if isinstance(v.get("nome"), str):
            testo += f"  ({v['nome']})"
        r.append((nome, testo))
    return r


# --------------------------------------------------------------------------- grafici
SFONDO = "#0b1020"
SFONDO_ASSI = "#111831"
TESTO = "#e8ecf6"
GRIGIO = "#5b6478"
COLORE_ISO = "#41e0ff"
CMAP = "plasma"
SCALA_TESTO = 1.0   # la finestra la cambia con la tendina «Dimensione testo»; PDF e PNG usano sempre 1


def fs(dimensione):
    return dimensione * SCALA_TESTO


def _stile_assi(ax):
    ax.set_facecolor(SFONDO_ASSI)
    for s in ax.spines.values():
        s.set_color("#3a4566")
    ax.tick_params(colors=TESTO, labelsize=fs(11))
    ax.xaxis.label.set_color(TESTO)
    ax.yaxis.label.set_color(TESTO)
    ax.title.set_color(TESTO)
    ax.grid(True, color="#26304d", lw=0.6, alpha=0.7)


def _firma(fig):
    fig.text(0.995, 0.005, f"{FIRMA} · H-R Diagrams {VERSIONE}", ha="right", va="bottom",
             color="#8a93ab", fontsize=fs(9), alpha=0.9)


def disegna_hr(fig, an, lingua, valori=None, griglia=None, soglia=None, evidenzia=None):
    """Disegna il diagramma colore-magnitudine. Restituisce (ax, punti_membri, indici, linea_isocrona)."""
    soglia = an["soglia"] if soglia is None else soglia
    fig.clear()
    fig.patch.set_facecolor(SFONDO)
    ax = fig.add_subplot(111)
    _stile_assi(ax)
    d, p = an["dati"], an["p"]
    c, G = d["bp_rp"], d["phot_g_mean_mag"]
    ok = np.isfinite(c) & np.isfinite(G)
    buoni = colori_buoni(d)
    campo = ok & (p < soglia)
    incerti = ok & (p >= soglia) & ~buoni
    memb = ok & (p >= soglia) & buoni
    ax.scatter(c[campo], G[campo], s=3, c=GRIGIO, alpha=0.35, lw=0, label=T(lingua, "campo"), rasterized=True)
    if incerti.any():
        ax.scatter(c[incerti], G[incerti], s=5, facecolors="none", edgecolors="#8fa3c8", lw=0.4, alpha=0.5,
                   label=T(lingua, "colori_incerti"), rasterized=True)
    ordine = np.where(memb)[0]
    ordine = ordine[np.argsort(p[ordine])]
    punti = ax.scatter(c[ordine], G[ordine], s=14, c=p[ordine], cmap=CMAP, vmin=soglia, vmax=1.0, lw=0,
                       label=T(lingua, "membri"), picker=4, rasterized=True)
    cb = fig.colorbar(punti, ax=ax, pad=0.01, fraction=0.04)
    cb.set_label(T(lingua, "prob"), color=TESTO, fontsize=fs(11))
    cb.ax.tick_params(colors=TESTO, labelsize=fs(10))
    linea, riquadro = None, None
    v = valori or an.get("adattamento")
    if v and griglia is not None:
        (linea,) = ax.plot([], [], color=COLORE_ISO, lw=2.0, alpha=0.9, label=T(lingua, "isocrona"))
        riquadro = ax.text(0.98, 0.98, "", transform=ax.transAxes, ha="right", va="top", fontsize=fs(12), color=TESTO,
                           bbox=dict(boxstyle="round,pad=0.5", fc="#0b1020cc", ec="#3a4566"))
        aggiorna_isocrona(linea, riquadro, griglia, v, lingua)
    if evidenzia is not None and 0 <= evidenzia < len(c) and ok[evidenzia]:
        ax.scatter([c[evidenzia]], [G[evidenzia]], s=160, facecolors="none", edgecolors="#ffffff", lw=1.8, zorder=5)
    # limiti
    cm, Gm = c[memb], G[memb]
    if memb.sum() > 5:
        x0, x1 = np.percentile(cm, [0.5, 99.5])
        y0, y1 = np.nanmin(Gm), np.nanmax(G[ok])
    else:
        x0, x1 = np.nanpercentile(c[ok], [0.5, 99.5])
        y0, y1 = np.nanmin(G[ok]), np.nanmax(G[ok])
    marg = 0.12 * (x1 - x0) + 0.1
    ax.set_xlim(max(-0.8, x0 - marg), min(5.0, x1 + marg))
    ax.set_ylim(y1 + 0.3, y0 - 0.8)
    ax.set_xlabel(T(lingua, "asse_colore"), fontsize=fs(13))
    ax.set_ylabel(T(lingua, "asse_G"), fontsize=fs(13))
    amm = an["ammasso"]
    ax.set_title(f"{nome_ammasso(amm, lingua)} – {T(lingua, 'titolo_hr')}", fontsize=fs(15), pad=10)
    leg = ax.legend(loc="lower left", fontsize=fs(11), facecolor="#0b1020", edgecolor="#3a4566", markerscale=2)
    for t in leg.get_texts():
        t.set_color(TESTO)
    _firma(fig)
    return dict(ax=ax, punti=punti, ordine=ordine, linea=linea, riquadro=riquadro)


def testo_riquadro(v, lingua):
    feh = f"{v['feh']:+.2f}"
    if lingua == "it":
        feh = feh.replace(".", ",")
    return "\n".join([f"{T(lingua, 'eta')}: {testo_eta(v['log_eta'], lingua)}",
                      f"{T(lingua, 'distanza')}: {intero(10 ** (v['dm'] / 5 + 1), lingua)} pc",
                      f"A_V: {num(v['av'], 2, lingua)}", f"[Fe/H]: {feh}"])


def aggiorna_isocrona(linea, riquadro, griglia, v, lingua):
    """Sposta l'isocrona già disegnata (per i cursori) senza ridisegnare le stelle."""
    iso = isocrona_spostata(griglia, v["feh"], v["log_eta"], v["dm"], v["av"])
    linea.set_data(iso["colore"], iso["G"])
    if riquadro is not None:
        riquadro.set_text(testo_riquadro(dict(v, feh=iso["feh"], log_eta=iso["eta"]), lingua))


def disegna_membri(fig, an, lingua, soglia=None):
    soglia = an["soglia"] if soglia is None else soglia
    fig.clear()
    fig.patch.set_facecolor(SFONDO)
    d, p = an["dati"], an["p"]
    memb = p >= soglia
    campo = ~memb
    amm = an["ammasso"]
    c0 = math.cos(math.radians(amm["dec"]))
    dra = ((d["ra"] - amm["ra"] + 180) % 360 - 180) * c0
    ddec = d["dec"] - amm["dec"]
    assi = fig.subplots(2, 2)
    for ax in assi.ravel():
        _stile_assi(ax)
    # moti propri
    ax = assi[0, 0]
    pa, pd = d["pmra"], d["pmdec"]
    if memb.sum() > 3:
        cx, cy = np.median(pa[memb]), np.median(pd[memb])
        lato = max(3.0, 8 * np.std(pa[memb]), 8 * np.std(pd[memb]))
    else:
        cx, cy, lato = np.nanmedian(pa), np.nanmedian(pd), 10
    lato = max(lato, 0.5 * np.nanpercentile(np.abs(pa - cx), 60) * 2)
    ax.scatter(pa[campo], pd[campo], s=2, c=GRIGIO, alpha=0.35, lw=0, rasterized=True)
    ax.scatter(pa[memb], pd[memb], s=6, c=p[memb], cmap=CMAP, vmin=soglia, vmax=1, lw=0, rasterized=True)
    ax.set_xlim(cx - lato, cx + lato)
    ax.set_ylim(cy - lato, cy + lato)
    ax.set_xlabel(T(lingua, "pmra"), fontsize=fs(11))
    ax.set_ylabel(T(lingua, "pmdec"), fontsize=fs(11))
    ax.set_title(T(lingua, "moti_propri"), fontsize=fs(13))
    # parallassi
    ax = assi[0, 1]
    plx = d["parallax"]
    okp = np.isfinite(plx)
    if memb.sum() > 3:
        mp, sp = np.median(plx[memb]), max(np.std(plx[memb]), 0.05)
        lo, hi = mp - 6 * sp, mp + 6 * sp
    else:
        lo, hi = np.nanpercentile(plx, [1, 99])
    bins = np.linspace(lo, hi, 60)
    ax.hist(plx[okp & campo], bins=bins, color=GRIGIO, alpha=0.7, label=T(lingua, "campo"))
    ax.hist(plx[okp & memb], bins=bins, color="#f4a340", alpha=0.85, label=T(lingua, "membri"))
    ax.set_xlabel(T(lingua, "parallasse"), fontsize=fs(11))
    ax.set_ylabel(T(lingua, "conteggi"), fontsize=fs(11))
    ax.set_title(T(lingua, "parallasse").split(" [")[0], fontsize=fs(13))
    leg = ax.legend(fontsize=fs(10), facecolor="#0b1020", edgecolor="#3a4566")
    for t in leg.get_texts():
        t.set_color(TESTO)
    # cielo
    ax = assi[1, 0]
    ax.scatter(dra[campo], ddec[campo], s=2, c=GRIGIO, alpha=0.3, lw=0, rasterized=True)
    ax.scatter(dra[memb], ddec[memb], s=6, c=p[memb], cmap=CMAP, vmin=soglia, vmax=1, lw=0, rasterized=True)
    ax.set_aspect("equal", adjustable="datalim")
    ax.invert_xaxis()
    ax.set_xlabel(T(lingua, "dra"), fontsize=fs(11))
    ax.set_ylabel(T(lingua, "ddec"), fontsize=fs(11))
    ax.set_title(T(lingua, "cielo"), fontsize=fs(13))
    # parallasse e G
    ax = assi[1, 1]
    G = d["phot_g_mean_mag"]
    ax.scatter(G[campo], plx[campo], s=2, c=GRIGIO, alpha=0.3, lw=0, rasterized=True)
    ax.scatter(G[memb], plx[memb], s=6, c=p[memb], cmap=CMAP, vmin=soglia, vmax=1, lw=0, rasterized=True)
    ax.set_ylim(lo, hi)
    ax.set_xlabel(T(lingua, "asse_G"), fontsize=fs(11))
    ax.set_ylabel(T(lingua, "parallasse"), fontsize=fs(11))
    ax.set_title(T(lingua, "par_vs_G"), fontsize=fs(13))
    fig.suptitle(f"{nome_ammasso(amm, lingua)} – {T(lingua, 'membri')}: {int(memb.sum())}", color=TESTO, fontsize=fs(15),
                 y=0.995)
    fig.subplots_adjust(left=0.05 + 0.04 * SCALA_TESTO, right=0.98, bottom=0.06 + 0.06 * SCALA_TESTO,
                        top=0.95 - 0.06 * SCALA_TESTO, wspace=0.15 + 0.12 * SCALA_TESTO, hspace=0.2 + 0.25 * SCALA_TESTO)
    _firma(fig)


def _pagina_testo(ax, an, lingua, valori):
    ax.set_axis_off()
    amm = an["ammasso"]
    y = 0.98

    import textwrap

    def riga(testo, dim=11, peso="normal", colore=TESTO, passo=None):
        """Scrive il testo andando a capo a mano (circa 52 caratteri a 11 pt) e scende di conseguenza."""
        nonlocal y
        larghezza = max(20, int(46 * 11 / dim))
        pezzi = textwrap.wrap(testo, larghezza, subsequent_indent="   " if testo.startswith("   ") else "") or [""]
        altezza = dim / 11 * 0.034
        for pezzo in pezzi:
            ax.text(0.0, y, pezzo, transform=ax.transAxes, fontsize=dim, weight=peso, color=colore, va="top")
            y -= altezza
        y -= (passo if passo is not None else altezza * 0.35)

    tipo = T(lingua, "tipo_globulare" if amm.get("tipo") == "globulare" else "tipo_aperto")
    riga(nome_ammasso(amm, lingua), 17, "bold", passo=0.01)
    riga(f"{tipo} · AR {amm['ra']:.3f}°  Dec {amm['dec']:+.3f}° · r {amm['raggio']}°", 11, colore="#a9b3cc")
    y -= 0.02
    riga(T(lingua, "adattamento"), 13, "bold", "#41e0ff", 0.008)
    for a, b in righe_risultati(an, lingua, valori):
        riga(f"{a}: {b}", 10.5, colore="#ffb454" if a.startswith("⚠") else TESTO, passo=0.002)
    y -= 0.02
    riga(T(lingua, "letteratura"), 13, "bold", "#f4a340", 0.008)
    for a, b in righe_letteratura(an, lingua):
        riga(f"{a}:", 10.5, "bold", passo=0.0)
        riga(f"   {b}", 10, passo=0.006)
    y -= 0.02
    riga(f"{T(lingua, 'fonte_dati')}: {an.get('fonte', '')} · {an['membri']['metodo']}", 8.5, colore="#a9b3cc")
    for nota in (T(lingua, "nota_isocrone"), T(lingua, "nota_estinzione"), T(lingua, "nota_colori"), T(lingua, "nota_lett")):
        riga(nota, 8.5, colore="#a9b3cc")
    riga(f"{T(lingua, 'data_analisi')} {an['data']} · H-R Diagrams {VERSIONE}", 8.5, colore="#a9b3cc")


class _ScalaUno:
    def __enter__(self):
        global SCALA_TESTO
        self.prima, SCALA_TESTO = SCALA_TESTO, 1.0

    def __exit__(self, *a):
        global SCALA_TESTO
        SCALA_TESTO = self.prima


def salva_pdf(percorso, an, lingua, griglia=None, valori=None):
    with _ScalaUno():
        return _salva_pdf(percorso, an, lingua, griglia, valori)


def _salva_pdf(percorso, an, lingua, griglia=None, valori=None):
    if not HA_MPL:
        raise RuntimeError(T(lingua, "senza_mpl"))
    percorso = Path(percorso)
    with PdfPages(percorso) as pdf:
        fig = Figure(figsize=(11.69, 8.27))
        FigureCanvasAgg(fig)
        sotto = fig.subfigures(1, 2, width_ratios=[1.75, 1])
        disegna_hr(sotto[0], an, lingua, valori, griglia)
        sotto[1].patch.set_facecolor(SFONDO)
        ax = sotto[1].add_subplot(111)
        _pagina_testo(ax, an, lingua, valori)
        pdf.savefig(fig, facecolor=SFONDO)
        fig2 = Figure(figsize=(11.69, 8.27))
        FigureCanvasAgg(fig2)
        disegna_membri(fig2, an, lingua)
        pdf.savefig(fig2, facecolor=SFONDO)
        info = pdf.infodict()
        info["Title"] = (f"Diagramma H-R – {nome_ammasso(an['ammasso'], lingua)}" if lingua == "it"
                         else f"H-R diagram – {nome_ammasso(an['ammasso'], lingua)}")
        info["Author"] = "Edoardo Battistel"
        info["Creator"] = f"Diagrammi H-R {VERSIONE}"
    return percorso


def salva_png(percorso, an, lingua, griglia=None, valori=None):
    with _ScalaUno():
        return _salva_png(percorso, an, lingua, griglia, valori)


def _salva_png(percorso, an, lingua, griglia=None, valori=None):
    fig = Figure(figsize=(12, 9), dpi=150)
    FigureCanvasAgg(fig)
    disegna_hr(fig, an, lingua, valori, griglia)
    fig.savefig(percorso, facecolor=SFONDO)
    return Path(percorso)


def salva_csv(percorso, an, soglia=None):
    soglia = an["soglia"] if soglia is None else soglia
    d, p = an["dati"], an["p"]
    sel = np.where(p >= soglia)[0]
    sel = sel[np.argsort(d["phot_g_mean_mag"][sel])]
    with open(percorso, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CAMPI_GAIA + ["prob_membro"])
        for i in sel:
            riga = []
            for k in CAMPI_GAIA:
                v = d[k][i] if k in d else np.nan   # copie vecchie senza tutte le colonne
                if k == "source_id":
                    riga.append(int(v))
                else:
                    riga.append("" if not np.isfinite(v) else f"{v:.6f}" if k in ("ra", "dec") else f"{v:.5g}")
            riga.append(f"{p[i]:.4f}")
            w.writerow(riga)
    return Path(percorso), len(sel)


def salva_json(percorso, an, valori=None):
    amm = an["ammasso"]
    r = dict(programma=f"Diagrammi H-R {VERSIONE}", firma=FIRMA, data=an["data"], ammasso=amm,
             fonte=an.get("fonte"), membri=an.get("membri"), parallasse=an.get("dist_plx"),
             adattamento=valori or an.get("adattamento"), letteratura=an.get("lett"))
    Path(percorso).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return Path(percorso)


LINGUA_RISULTATI = "en"   # PDF e PNG sempre in inglese (decisione di Edoardo, 15/11/2026)


def salva_tutto(an, lingua=None, griglia=None, cartella=None, valori=None):
    """Salva CSV, JSON, PDF e PNG. I file di risultato sono sempre in inglese (LINGUA_RISULTATI)."""
    cartella = Path(cartella or cartella_risultati()) / an["ammasso"]["chiave"]
    cartella.mkdir(parents=True, exist_ok=True)
    base = an["ammasso"]["chiave"]
    fatti = [salva_csv(cartella / f"{base}_members.csv", an)[0], salva_json(cartella / f"{base}_results.json", an, valori)]
    if HA_MPL:
        fatti.append(salva_pdf(cartella / f"{base}_sheet.pdf", an, LINGUA_RISULTATI, griglia, valori))
        fatti.append(salva_png(cartella / f"{base}_hr.png", an, LINGUA_RISULTATI, griglia, valori))
    return cartella, fatti


def info_stella(an, i, lingua):
    d, p = an["dati"], an["p"]

    def f(k, cifre=3):
        v = d[k][i]
        return "–" if not np.isfinite(v) else num(float(v), cifre, lingua)

    plx = d["parallax"][i]
    dist = f"{1000 / (plx + ZP_PARALLASSE):.0f} pc" if np.isfinite(plx) and plx > 0.05 else "–"
    stato = T(lingua, "membro_si") if p[i] >= an["soglia"] else T(lingua, "membro_no")
    return "\n".join([
        f"{T(lingua, 'stella')}: Gaia DR3 {int(d['source_id'][i])}",
        f"AR {d['ra'][i]:.5f}°  Dec {d['dec'][i]:+.5f}°",
        f"G = {f('phot_g_mean_mag')}   BP−RP = {f('bp_rp')}",
        f"{T(lingua, 'parallasse')}: {f('parallax')} ± {f('parallax_error')}  (≈ {dist})",
        f"μα* = {f('pmra', 2)}  μδ = {f('pmdec', 2)} mas/a",
        f"RUWE = {f('ruwe', 2)}",
        f"{T(lingua, 'prob')}: {num(float(p[i]), 2, lingua)} ({stato})",
    ])
