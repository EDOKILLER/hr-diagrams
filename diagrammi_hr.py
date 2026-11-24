#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagrammi H-R (versione 1.4) – finestra e comandi
© 2026 Edoardo Battistel

Uso:
  diagrammi_hr.py                      apre la finestra
  diagrammi_hr.py --autotest           prova senza rete (ammasso simulato), per la compilazione
  diagrammi_hr.py --diagnostica        prova gli archivi in rete (Gaia, VizieR, MIST)
  diagrammi_hr.py --prepara-mist       scarica e prepara le isocrone MIST (160 MB, una volta sola)
  diagrammi_hr.py --analizza "M 67" [--lingua en] [--riscarica]
  diagrammi_hr.py --tutti [--lingua en]    tutti gli ammassi famosi, uno dopo l'altro
I risultati vanno in ~/Scrivania/Astronomia/Diagrammi_HR/<ammasso>/ (PDF, PNG, CSV, JSON).
"""
import argparse
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import numpy as np

import motore_hr as M

VERSIONE = M.VERSIONE
FILE_IMPOSTAZIONI = M.CARTELLA_DATI / "impostazioni.json"
LINGUE = [("it", "Italiano"), ("en", "English")]
SCALE = [1.0, 1.25, 1.5, 1.75, 2.0]          # tendina «Dimensione testo»
SCALA_PREDEFINITA = 1.5
URL_GUIDA = "https://claude.ai/artifact/NjaYbQ7neeRGSBEYDgfXdt"   # documento «come leggere i risultati»


def risorsa(nome):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nome)


def ambiente_esterno():
    """Ambiente per i programmi esterni (zenity, xdg-open) senza le librerie di PyInstaller."""
    env = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def leggi_impostazioni():
    try:
        return json.loads(FILE_IMPOSTAZIONI.read_text(encoding="utf-8"))
    except Exception:
        return {}


def scrivi_impostazioni(d):
    try:
        M.CARTELLA_DATI.mkdir(parents=True, exist_ok=True)
        FILE_IMPOSTAZIONI.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


# =========================================================================== finestra
def avvia_finestra():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    import tkinter.font as tkfont
    if not M.HA_MPL:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Diagrammi H-R", M.T("it", "senza_mpl"))
        return
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

    class App:
        def __init__(self, root):
            self.root = root
            self.imp = leggi_impostazioni()
            self.lingua = self.imp.get("lingua", "it")
            self.scala = float(self.imp.get("scala", SCALA_PREDEFINITA))
            if self.scala not in SCALE:
                self.scala = SCALA_PREDEFINITA
            M.SCALA_TESTO = self.scala
            self.griglia = None
            self.an = None
            self.valori = None
            self.disegno = None
            self.cerchio = None
            self.coda = queue.Queue()
            self.occupato = False
            self._timer = None
            self.etichette = {}   # widget -> chiave di testo
            self._stile()
            self._costruisci()
            self.applica_lingua()
            root.after(100, self._leggi_coda)
            self._griglia_evento = threading.Event()
            if M.griglia_pronta():
                threading.Thread(target=self._lavoro_carica_griglia, daemon=True).start()
            else:
                self._griglia_evento.set()

        # ---------------------------------------------------------- aspetto
        def _stile(self):
            self.font_titolo = tkfont.Font(name="FontTitoloHR", weight="bold")
            self.font_grande = tkfont.Font(name="FontGrandeHR", weight="bold")
            self.font_mono = tkfont.Font(name="FontMonoHR", family="DejaVu Sans Mono")
            st = ttk.Style()
            try:
                st.theme_use("clam")
            except tk.TclError:
                pass
            st.configure(".", font="TkDefaultFont")
            st.configure("Grande.TButton", font=self.font_grande)
            st.configure("Ripristina.TButton", font=self.font_grande, foreground="#ffffff", background="#1d4ed8")
            st.map("Ripristina.TButton", background=[("active", "#2563eb"), ("disabled", "#9ca3af")])
            st.configure("TNotebook.Tab", font="TkDefaultFont")
            st.configure("Titolo.TLabel", font=self.font_titolo)
            self.root.option_add("*TCombobox*Listbox.font", "TkDefaultFont")
            self._applica_dimensioni()

        def _applica_dimensioni(self):
            """Dimensione dei testi della finestra (tendina «Dimensione testo»)."""
            base = int(round(11 * self.scala))
            for nome in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkFixedFont",
                         "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
                try:
                    tkfont.nametofont(nome).configure(size=base)
                except tk.TclError:
                    pass
            self.font_titolo.configure(size=int(round(base * 1.2)))
            self.font_grande.configure(size=int(round(base * 1.1)))
            self.font_mono.configure(size=base - 1)
            st = ttk.Style()
            p = int(round(6 * self.scala))
            st.configure("TButton", padding=(p + 4, p))
            st.configure("Grande.TButton", padding=(p + 8, p + 2))
            st.configure("Ripristina.TButton", padding=(p + 8, p + 2))
            st.configure("TNotebook.Tab", padding=(p * 2 + 4, p))
            st.configure("TCombobox", arrowsize=int(14 * self.scala))
            st.configure("TCheckbutton", indicatorsize=int(14 * self.scala))
            for _, sc, _ in getattr(self, "cursori", {}).values():
                sc.configure(width=int(16 * self.scala), sliderlength=int(26 * self.scala))
            if getattr(self, "sc_soglia", None) is not None:
                self.sc_soglia.configure(width=int(16 * self.scala), sliderlength=int(26 * self.scala))
            M.SCALA_TESTO = self.scala

        def _t(self, chiave):
            return M.T(self.lingua, chiave)

        def _lab(self, w, chiave):
            self.etichette[w] = chiave
            return w

        # ---------------------------------------------------------- costruzione
        def _costruisci(self):
            r = self.root
            alto = ttk.Frame(r, padding=(10, 8))
            alto.pack(side="top", fill="x")
            self._lab(ttk.Label(alto), "ammasso").pack(side="left")
            self.var_ammasso = tk.StringVar()
            self.cb_ammasso = ttk.Combobox(alto, textvariable=self.var_ammasso, state="readonly", width=30)
            self.cb_ammasso.pack(side="left", padx=(6, 12))
            self.cb_ammasso.bind("<<ComboboxSelected>>", lambda e: self._mostra_altro())
            self.var_riscarica = tk.BooleanVar(value=False)
            self._lab(ttk.Checkbutton(alto, variable=self.var_riscarica), "riscarica").pack(side="left", padx=6)
            self.bt_analizza = self._lab(ttk.Button(alto, style="Grande.TButton", command=self.analizza), "analizza")
            self.bt_analizza.pack(side="left", padx=12)
            self.var_lingua = tk.StringVar()
            self.cb_lingua = ttk.Combobox(alto, textvariable=self.var_lingua, state="readonly", width=10,
                                          values=[n for _, n in LINGUE])
            self.cb_lingua.pack(side="right")
            self.cb_lingua.bind("<<ComboboxSelected>>", lambda e: self._cambia_lingua())
            self._lab(ttk.Label(alto), "lingua").pack(side="right", padx=6)
            self.var_scala = tk.StringVar(value=f"{int(self.scala * 100)} %")
            self.cb_scala = ttk.Combobox(alto, textvariable=self.var_scala, state="readonly", width=6,
                                         values=[f"{int(v * 100)} %" for v in SCALE])
            self.cb_scala.pack(side="right", padx=(0, 16))
            self.cb_scala.bind("<<ComboboxSelected>>", lambda e: self._cambia_scala())
            self._lab(ttk.Label(alto), "dim_testo").pack(side="right", padx=6)
            self._lab(ttk.Button(alto, command=self.apri_guida), "guida").pack(side="right", padx=(0, 16))

            # riga per un ammasso qualunque (coordinate)
            self.fr_altro = ttk.Frame(r, padding=(10, 0, 10, 6))
            self.var_nome = tk.StringVar(value="NGC 2516")
            self.var_ra = tk.StringVar(value="119.53")
            self.var_dec = tk.StringVar(value="-60.75")
            self.var_raggio = tk.StringVar(value="0.8")
            self.var_tipo = tk.StringVar()
            for chiave, var, larg in (("nome", self.var_nome, 16), ("ra", self.var_ra, 9), ("dec", self.var_dec, 9),
                                      ("raggio", self.var_raggio, 6)):
                self._lab(ttk.Label(self.fr_altro), chiave).pack(side="left", padx=(8, 4))
                ttk.Entry(self.fr_altro, textvariable=var, width=larg, font="TkDefaultFont").pack(side="left")
            self.cb_tipo = ttk.Combobox(self.fr_altro, textvariable=self.var_tipo, state="readonly", width=18)
            self.cb_tipo.pack(side="left", padx=10)

            # stato in basso
            basso = ttk.Frame(r, padding=(10, 4))
            basso.pack(side="bottom", fill="x")
            self.var_stato = tk.StringVar()
            ttk.Label(basso, textvariable=self.var_stato).pack(side="left", fill="x", expand=True)
            self.barra = ttk.Progressbar(basso, length=260, mode="determinate", maximum=1.0)
            self.barra.pack(side="right")
            ttk.Label(basso, text=f"{M.FIRMA} · v{VERSIONE}", foreground="#666").pack(side="right", padx=12)

            corpo = ttk.PanedWindow(r, orient="horizontal")
            corpo.pack(side="top", fill="both", expand=True, padx=8, pady=4)
            self.corpo = corpo

            # schede con i grafici
            self.nb = ttk.Notebook(corpo)
            corpo.add(self.nb, weight=4)
            self.tab_hr = ttk.Frame(self.nb)
            self.tab_m = ttk.Frame(self.nb)
            self.tab_r = ttk.Frame(self.nb)
            self.nb.add(self.tab_hr)
            self.nb.add(self.tab_m)
            self.nb.add(self.tab_r)
            self.fig_hr = Figure(figsize=(9, 7), dpi=100)
            self.can_hr = FigureCanvasTkAgg(self.fig_hr, master=self.tab_hr)
            bar = NavigationToolbar2Tk(self.can_hr, self.tab_hr, pack_toolbar=False)
            bar.update()
            bar.pack(side="bottom", fill="x")
            self.can_hr.get_tk_widget().pack(fill="both", expand=True)
            self.can_hr.mpl_connect("pick_event", self._clic_stella)
            self.fig_m = Figure(figsize=(9, 7), dpi=100)
            self.can_m = FigureCanvasTkAgg(self.fig_m, master=self.tab_m)
            bar2 = NavigationToolbar2Tk(self.can_m, self.tab_m, pack_toolbar=False)
            bar2.update()
            bar2.pack(side="bottom", fill="x")
            self.can_m.get_tk_widget().pack(fill="both", expand=True)
            self.txt_ris = tk.Text(self.tab_r, wrap="word", font=self.font_mono, padx=14, pady=12)
            self.txt_ris.pack(fill="both", expand=True)
            self._schermo_vuoto()

            # pannello di destra
            dx = ttk.Frame(corpo, padding=(10, 4))
            corpo.add(dx, weight=1)
            self._lab(ttk.Label(dx, style="Titolo.TLabel"), "isocrona").pack(anchor="w", pady=(0, 6))
            self.cursori = {}
            for chiave, da, a, passo in (("log_eta", 6.0, 10.15, 0.05), ("dm", 0.0, 20.0, 0.01), ("av", 0.0, 3.0, 0.01)):
                fr = ttk.Frame(dx)
                fr.pack(fill="x", pady=4)
                testa = ttk.Frame(fr)
                testa.pack(fill="x")
                self._lab(ttk.Label(testa), chiave).pack(side="left")
                val = ttk.Label(testa, foreground="#1d4ed8")
                val.pack(side="right")
                var = tk.DoubleVar(value=da)
                sc = tk.Scale(fr, from_=da, to=a, resolution=passo, orient="horizontal", variable=var, showvalue=False,
                              width=int(16 * self.scala), sliderlength=int(26 * self.scala),
                              command=lambda _v, k=chiave: self._cursore_mosso(k))
                sc.pack(fill="x")
                self.cursori[chiave] = (var, sc, val)
            fr = ttk.Frame(dx)
            fr.pack(fill="x", pady=4)
            self._lab(ttk.Label(fr), "feh").pack(side="left")
            self.var_feh = tk.StringVar()
            self.cb_feh = ttk.Combobox(fr, textvariable=self.var_feh, state="readonly", width=8)
            self.cb_feh.pack(side="right")
            self.cb_feh.bind("<<ComboboxSelected>>", lambda e: self._cursore_mosso("feh"))
            self.bt_ripristina = self._lab(ttk.Button(dx, command=self.torna, style="Ripristina.TButton"), "torna")
            self.bt_ripristina.pack(fill="x", pady=(10, 4))
            self.bt_riadatta = self._lab(ttk.Button(dx, command=self.riadatta), "riadatta")
            self.bt_riadatta.pack(fill="x", pady=(0, 4))

            ttk.Separator(dx).pack(fill="x", pady=10)
            testa = ttk.Frame(dx)
            testa.pack(fill="x")
            self._lab(ttk.Label(testa), "soglia").pack(side="left")
            self.lab_soglia = ttk.Label(testa, foreground="#1d4ed8")
            self.lab_soglia.pack(side="right")
            self.var_soglia = tk.DoubleVar(value=0.7)
            self.sc_soglia = tk.Scale(dx, from_=0.5, to=0.95, resolution=0.05, orient="horizontal",
                                      variable=self.var_soglia, showvalue=False, width=int(16 * self.scala),
                                      sliderlength=int(26 * self.scala), command=lambda _v: self._soglia_mossa())
            self.sc_soglia.pack(fill="x")
            self.lab_soglia.configure(text="0,70")

            ttk.Separator(dx).pack(fill="x", pady=10)
            for chiave, cmd in (("salva_pdf", self.salva_pdf), ("salva_csv", self.salva_csv),
                                ("apri_cartella", self.apri_cartella)):
                self._lab(ttk.Button(dx, command=cmd), chiave).pack(fill="x", pady=3)

            ttk.Separator(dx).pack(fill="x", pady=10)
            self.txt_stella = tk.Text(dx, height=9, width=34, wrap="word", font="TkDefaultFont",
                                      relief="flat", background="#f3f4f6", padx=8, pady=6)
            self.txt_stella.pack(fill="both", expand=True)
            self._abilita_cursori(False)

        def _schermo_vuoto(self):
            for fig in (self.fig_hr, self.fig_m):
                fig.clear()
                fig.patch.set_facecolor(M.SFONDO)
                fig.text(0.5, 0.5, "Diagrammi H-R", ha="center", va="center", color="#41e0ff", fontsize=24 * self.scala, alpha=0.5)
                fig.text(0.995, 0.005, M.FIRMA, ha="right", va="bottom", color="#8a93ab", fontsize=9)
            self.can_hr.draw_idle()
            self.can_m.draw_idle()

        def _abilita_cursori(self, si):
            stato = "normal" if si else "disabled"
            for var, sc, val in self.cursori.values():
                sc.configure(state=stato)
            self.cb_feh.configure(state="readonly" if si else "disabled")
            self.bt_riadatta.configure(state=stato)
            self.bt_ripristina.configure(state=stato)

        # ---------------------------------------------------------- lingua
        def applica_lingua(self):
            L = self.lingua
            self.root.title(f"{self._t('titolo')} {VERSIONE}")
            for w, k in self.etichette.items():
                try:
                    w.configure(text=self._t(k))
                except tk.TclError:
                    pass
            self.var_lingua.set(dict(LINGUE)[L])
            indice = self.cb_ammasso.current() if self.cb_ammasso.get() else self.imp.get("ammasso", 0)
            self.cb_ammasso.configure(values=[M.nome_ammasso(a, L) for a in M.AMMASSI] + [self._t("altro")])
            self.cb_ammasso.current(max(0, min(indice, len(M.AMMASSI))))
            tipo = 1 if self.var_tipo.get() in (M.T("it", "tipo_globulare"), M.T("en", "tipo_globulare")) else 0
            self.cb_tipo.configure(values=[self._t("tipo_aperto"), self._t("tipo_globulare")])
            self.cb_tipo.current(tipo)
            self.nb.tab(0, text=self._t("tab_hr"))
            self.nb.tab(1, text=self._t("tab_membri"))
            self.nb.tab(2, text=self._t("tab_risultati"))
            self._mostra_altro()
            if not self.var_stato.get() or self.an is None:
                self.var_stato.set(self._t("stato_pronto"))
            self._scrivi_stella(self._t("clic_stella"))
            self._aggiorna_etichette_cursori()
            if self.an is not None:
                self._disegna_tutto()

        def _cambia_lingua(self):
            nome = self.var_lingua.get()
            self.lingua = next(c for c, n in LINGUE if n == nome)
            self.imp["lingua"] = self.lingua
            scrivi_impostazioni(self.imp)
            self.applica_lingua()

        def _cambia_scala(self):
            try:
                self.scala = float(self.var_scala.get().replace("%", "").strip()) / 100
            except ValueError:
                return
            self.imp["scala"] = self.scala
            scrivi_impostazioni(self.imp)
            self._applica_dimensioni()
            if self.an is not None:
                self._disegna_tutto()

        def apri_guida(self):
            try:
                subprocess.Popen(["xdg-open", URL_GUIDA], env=ambiente_esterno())
            except Exception:
                import webbrowser
                webbrowser.open(URL_GUIDA)

        def _mostra_altro(self):
            if self.cb_ammasso.current() == len(M.AMMASSI):
                self.fr_altro.pack(side="top", fill="x", after=self.root.winfo_children()[0])
            else:
                self.fr_altro.pack_forget()

        # ---------------------------------------------------------- ammasso scelto
        def ammasso_scelto(self):
            i = self.cb_ammasso.current()
            if 0 <= i < len(M.AMMASSI):
                return M.AMMASSI[i]
            try:
                ra = float(self.var_ra.get().replace(",", "."))
                dec = float(self.var_dec.get().replace(",", "."))
                r = float(self.var_raggio.get().replace(",", "."))
            except ValueError:
                raise ValueError("AR, Dec e raggio devono essere numeri (gradi)")
            if not (0 <= ra < 360 and -90 <= dec <= 90 and 0.02 <= r <= 10):
                raise ValueError("AR 0–360°, Dec −90…+90°, raggio 0,02–10°")
            tipo = "globulare" if self.cb_tipo.current() == 1 else "aperto"
            feh = -1.5 if tipo == "globulare" else 0.0
            return M.ammasso_personalizzato(self.var_nome.get().strip() or "Ammasso", ra, dec, r, tipo, feh)

        # ---------------------------------------------------------- lavori in sottofondo
        def _in_thread(self, funzione, *arg):
            def corpo():
                try:
                    funzione(*arg)
                except Exception as e:
                    self.coda.put(("errore", f"{e}", traceback.format_exc()))
                finally:
                    self.coda.put(("libero", None))
            self.occupato = True
            self.bt_analizza.configure(state="disabled")
            threading.Thread(target=corpo, daemon=True).start()

        def _avviso(self, testo):
            self.coda.put(("stato", testo.strip()))

        def _progresso(self, frazione, mb=None, totale=None):
            self.coda.put(("progresso", frazione))
            if mb is not None:
                self.coda.put(("stato", f"{self._t('stato_mist')}  {mb:.0f} / {totale:.0f} MB"))

        def _lavoro_carica_griglia(self):
            """All'avvio, in sottofondo: non blocca i pulsanti (dura meno di un secondo)."""
            try:
                self._griglia_caricata = M.carica_griglia()
                self.coda.put(("griglia", self._griglia_caricata))
            except Exception as e:
                self.coda.put(("stato", f"MIST: {e}"))
            finally:
                self._griglia_evento.set()

        def _lavoro_analisi(self, amm, scarica_mist, forza, soglia):
            self._griglia_evento.wait(120)
            griglia = self.griglia or getattr(self, "_griglia_caricata", None)
            if scarica_mist:
                self._avviso(self._t("stato_mist"))
                griglia = M.scarica_e_prepara_mist(self._avviso, self._progresso)
                self.coda.put(("griglia", griglia))
            elif griglia is None and M.griglia_pronta():
                griglia = M.carica_griglia()
                self.coda.put(("griglia", griglia))
            self.coda.put(("progresso", 0.1))
            self._avviso(self._t("stato_lett"))
            an = dict(ammasso=amm, versione=VERSIONE, data=time.strftime("%Y-%m-%d %H:%M"), soglia=soglia)
            an["lett"] = M.letteratura(amm, self._avviso)
            self.coda.put(("progresso", 0.2))
            self._avviso(self._t("stato_gaia"))
            an["dati"], an["fonte"] = M.scarica_gaia(amm, forza, self._avviso)
            n = len(an["dati"]["source_id"])
            self._avviso(f"{n} {self._t('stelle_scaricate')} {self._t('da')} {an['fonte']}")
            self.coda.put(("progresso", 0.45))
            self._avviso(self._t("stato_membri"))
            an["p"], an["membri"] = M.calcola_membri(an["dati"], amm, an["lett"], self._avviso)
            an["dist_plx"] = M.distanza_da_parallasse(an["dati"], an["p"], soglia)
            an["adattamento"] = None
            self.coda.put(("progresso", 0.6))
            if griglia is not None:
                self._avviso(self._t("stato_adatta"))
                an["adattamento"] = M.riadatta(an, griglia, amm.get("feh", 0.0), self._avviso)
            self.coda.put(("progresso", 0.9))
            M.salva_tutto(an, self.lingua, griglia)
            self.coda.put(("analisi", an))
            self.coda.put(("progresso", 1.0))

        def _lavoro_riadatta(self, feh):
            self._avviso(self._t("stato_adatta"))
            res = M.riadatta(self.an, self.griglia, feh, self._avviso)
            self.coda.put(("riadattato", res))

        def _leggi_coda(self):
            try:
                while True:
                    tipo, *dati = self.coda.get_nowait()
                    if tipo == "stato":
                        self.var_stato.set(dati[0][:160])
                    elif tipo == "progresso":
                        self.barra.configure(value=dati[0])
                    elif tipo == "griglia":
                        self.griglia = dati[0]
                        self.cb_feh.configure(values=[f"{f:+.2f}" for f in self.griglia.feh_valori])
                    elif tipo == "analisi":
                        self._analisi_arrivata(dati[0])
                    elif tipo == "riadattato":
                        self.an["adattamento"] = dati[0]
                        self.valori = dict(dati[0])
                        self._metti_cursori()
                        self._disegna_tutto()
                        self.var_stato.set(self._t("stato_fatto"))
                    elif tipo == "errore":
                        print(dati[1], file=sys.stderr)
                        self.var_stato.set(f"{self._t('errore')}: {dati[0][:150]}")
                        messagebox.showerror(self._t("errore"), dati[0][:800])
                    elif tipo == "libero":
                        self.occupato = False
                        self.bt_analizza.configure(state="normal")
            except queue.Empty:
                pass
            self.root.after(100, self._leggi_coda)

        # ---------------------------------------------------------- comandi
        def analizza(self):
            if self.occupato:
                return
            try:
                amm = self.ammasso_scelto()
            except ValueError as e:
                messagebox.showerror(self._t("errore"), str(e))
                return
            self.imp["ammasso"] = self.cb_ammasso.current()
            scrivi_impostazioni(self.imp)
            scarica_mist = False
            if not M.griglia_pronta():
                scarica_mist = messagebox.askyesno(self._t("titolo"), self._t("chiedi_mist"))
            self.barra.configure(value=0)
            self._in_thread(self._lavoro_analisi, amm, scarica_mist, bool(self.var_riscarica.get()),
                            round(float(self.var_soglia.get()), 2))

        def riadatta(self):
            if self.occupato or self.an is None or self.griglia is None:
                return
            self._in_thread(self._lavoro_riadatta, self._feh_scelta())

        def torna(self):
            if self.an is None or not self.an.get("adattamento"):
                return
            self.valori = dict(self.an["adattamento"])
            self._metti_cursori()
            self._disegna_tutto()

        def _feh_scelta(self):
            try:
                return float(self.var_feh.get().replace(",", "."))
            except ValueError:
                return self.an["ammasso"].get("feh", 0.0) if self.an else 0.0

        # ---------------------------------------------------------- risultati
        def _analisi_arrivata(self, an):
            self.an = an
            self.var_soglia.set(an["soglia"])
            if an.get("adattamento"):
                self.valori = dict(an["adattamento"])
            elif self.griglia is not None:
                dp = an.get("dist_plx") or {}
                feh = self.griglia.feh_vicina(an["ammasso"].get("feh", 0.0))
                self.valori = dict(log_eta=9.0, dm=dp.get("dm", 10.0), av=0.3, feh=feh)
            else:
                self.valori = None
            self._abilita_cursori(self.valori is not None and self.griglia is not None)
            self._metti_cursori()
            self._disegna_tutto()
            m = an["membri"]
            self.var_stato.set(f"{self._t('stato_fatto')}  {m['n_membri']} {self._t('n_membri')} · "
                               f"{M.cartella_risultati() / an['ammasso']['chiave']}")

        def _metti_cursori(self):
            v = self.valori
            if not v:
                return
            salvati = dict(v)
            if self.griglia is not None:
                self.cb_feh.configure(values=[f"{f:+.2f}" for f in self.griglia.feh_valori])
                self.var_feh.set(f"{self.griglia.feh_vicina(v['feh']):+.2f}")
            var, sc, _ = self.cursori["dm"]
            sc.configure(from_=round(v["dm"] - 2.0, 2), to=round(v["dm"] + 2.0, 2))
            for k in ("log_eta", "dm", "av"):
                self.cursori[k][0].set(round(v[k], 2))
            self.valori.update(salvati)   # i cursori possono aver scritto valori intermedi
            self._aggiorna_etichette_cursori()

        def _aggiorna_etichette_cursori(self):
            L = self.lingua
            if not self.valori:
                for _, _, lab in self.cursori.values():
                    lab.configure(text="–")
                return
            v = self.valori
            self.cursori["log_eta"][2].configure(text=f"{M.num(v['log_eta'], 2, L)} · {M.testo_eta(v['log_eta'], L)}")
            self.cursori["dm"][2].configure(text=f"{M.num(v['dm'], 2, L)} · {M.intero(10 ** (v['dm'] / 5 + 1), L)} pc")
            self.cursori["av"][2].configure(text=M.num(v["av"], 2, L))

        def _cursore_mosso(self, chiave):
            if not self.valori or self.griglia is None:
                return
            for k in ("log_eta", "dm", "av"):
                self.valori[k] = float(self.cursori[k][0].get())
            self.valori["feh"] = self.griglia.feh_vicina(self._feh_scelta())
            self.valori["log_eta"] = self.griglia.eta_vicina(self.valori["feh"], self.valori["log_eta"])
            self._aggiorna_etichette_cursori()
            if self._timer:
                self.root.after_cancel(self._timer)
            self._timer = self.root.after(60, self._sposta_isocrona)

        def _sposta_isocrona(self):
            self._timer = None
            d = self.disegno
            if d and d.get("linea") is not None:
                M.aggiorna_isocrona(d["linea"], d["riquadro"], self.griglia, self.valori, self.lingua)
                self.can_hr.draw_idle()
                self._scrivi_risultati()
            else:
                self._disegna_tutto()

        def _soglia_mossa(self):
            s = round(float(self.var_soglia.get()), 2)
            self.lab_soglia.configure(text=M.num(s, 2, self.lingua))
            if self.an is None or abs(s - self.an["soglia"]) < 1e-6:
                return
            self.an["soglia"] = s
            self.an["dist_plx"] = M.distanza_da_parallasse(self.an["dati"], self.an["p"], s)
            if self._timer:
                self.root.after_cancel(self._timer)
            self._timer = self.root.after(250, self._disegna_tutto)

        def _disegna_tutto(self):
            self._timer = None
            if self.an is None:
                return
            self.disegno = M.disegna_hr(self.fig_hr, self.an, self.lingua, self.valori, self.griglia)
            self.cerchio = None
            self.can_hr.draw_idle()
            M.disegna_membri(self.fig_m, self.an, self.lingua)
            self.can_m.draw_idle()
            self._scrivi_risultati()

        def _scrivi_risultati(self):
            if self.an is None:
                return
            L, an = self.lingua, self.an
            amm = an["ammasso"]
            righe = [M.nome_ammasso(amm, L), "=" * len(M.nome_ammasso(amm, L)), ""]
            if an.get("adattamento"):
                righe.append(self._t("adattamento"))
                righe += [f"  {a:<26} {b}" for a, b in M.righe_risultati(an, L, an["adattamento"])]
                if self.valori and any(abs(self.valori[k] - an["adattamento"][k]) > 1e-6 for k in ("log_eta", "dm", "av")):
                    righe += ["", "Cursori" if L == "it" else "Sliders"]
                    righe += [f"  {a:<26} {b}" for a, b in M.righe_risultati(dict(an, membri=None, dist_plx=None), L,
                                                                              dict(self.valori, err_log_eta=None,
                                                                                   err_dm=None, err_av=None,
                                                                                   err_dist_pc=None))]
            else:
                righe += [f"  {a:<26} {b}" for a, b in M.righe_risultati(an, L)]
            righe += ["", self._t("letteratura")]
            righe += [f"  {a}:\n      {b}" for a, b in M.righe_letteratura(an, L)]
            righe += ["", f"{self._t('fonte_dati')}: {an.get('fonte')} · {an['membri']['metodo']}",
                      self._t("nota_isocrone"), self._t("nota_estinzione"),
                      "", f"{self._t('data_analisi')} {an['data']} · Diagrammi H-R {VERSIONE} · {M.FIRMA}"]
            self.txt_ris.configure(state="normal")
            self.txt_ris.delete("1.0", "end")
            self.txt_ris.insert("1.0", "\n".join(righe))
            self.txt_ris.configure(state="disabled")

        def _scrivi_stella(self, testo):
            self.txt_stella.configure(state="normal")
            self.txt_stella.delete("1.0", "end")
            self.txt_stella.insert("1.0", testo)
            self.txt_stella.configure(state="disabled")

        def _clic_stella(self, evento):
            if self.an is None or not self.disegno or evento.artist is not self.disegno["punti"]:
                return
            if not len(evento.ind):
                return
            # la stella più vicina al clic tra quelle selezionate
            ordine = self.disegno["ordine"]
            c = self.an["dati"]["bp_rp"][ordine[evento.ind]]
            G = self.an["dati"]["phot_g_mean_mag"][ordine[evento.ind]]
            mx, my = evento.mouseevent.xdata, evento.mouseevent.ydata
            j = 0
            if mx is not None and my is not None:
                j = int(np.argmin((c - mx) ** 2 + ((G - my) / 4) ** 2))
            i = int(ordine[evento.ind[j]])
            self._scrivi_stella(M.info_stella(self.an, i, self.lingua))
            ax = self.disegno["ax"]
            x, y = self.an["dati"]["bp_rp"][i], self.an["dati"]["phot_g_mean_mag"][i]
            if self.cerchio is None:
                self.cerchio = ax.scatter([x], [y], s=220, facecolors="none", edgecolors="#ffffff", lw=2, zorder=6)
            else:
                self.cerchio.set_offsets([[x, y]])
            self.can_hr.draw_idle()

        # ---------------------------------------------------------- salvataggi
        def _chiedi_file(self, titolo, nome, estensione):
            cartella = M.cartella_risultati() / (self.an["ammasso"]["chiave"] if self.an else "")
            cartella.mkdir(parents=True, exist_ok=True)
            iniziale = str(cartella / nome)
            if shutil.which("zenity"):
                try:
                    r = subprocess.run(["zenity", "--file-selection", "--save", "--confirm-overwrite",
                                        f"--title={titolo}", f"--filename={iniziale}",
                                        f"--file-filter=*{estensione}"],
                                       capture_output=True, text=True, env=ambiente_esterno(), timeout=600)
                    if r.returncode == 0 and r.stdout.strip():
                        p = r.stdout.strip()
                        return p if p.lower().endswith(estensione) else p + estensione
                    if r.returncode == 1:
                        return None
                except Exception:
                    pass
            p = filedialog.asksaveasfilename(title=titolo, initialdir=str(cartella), initialfile=nome,
                                             defaultextension=estensione)
            return p or None

        def salva_pdf(self):
            if self.an is None:
                return
            nome = f"{self.an['ammasso']['chiave']}_sheet.pdf"
            p = self._chiedi_file(self._t("salva_pdf"), nome, ".pdf")
            if not p:
                return
            try:
                M.salva_pdf(p, self.an, M.LINGUA_RISULTATI, self.griglia, self.valori)   # sempre in inglese
                self.var_stato.set(f"{self._t('salvato')}: {p}")
            except Exception as e:
                messagebox.showerror(self._t("errore"), str(e))

        def salva_csv(self):
            if self.an is None:
                return
            p = self._chiedi_file(self._t("salva_csv"), f"{self.an['ammasso']['chiave']}_members.csv", ".csv")
            if not p:
                return
            try:
                _, n = M.salva_csv(p, self.an)
                self.var_stato.set(f"{self._t('salvato')}: {p} ({n} {self._t('n_membri')})")
            except Exception as e:
                messagebox.showerror(self._t("errore"), str(e))

        def apri_cartella(self):
            c = M.cartella_risultati()
            if self.an is not None:
                c = c / self.an["ammasso"]["chiave"]
            c.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.Popen(["xdg-open", str(c)], env=ambiente_esterno())
            except Exception as e:
                messagebox.showerror(self._t("errore"), str(e))

    root = tk.Tk(className="DiagrammiHR")   # classe della finestra: "Diagrammihr"
    try:
        icona = tk.PhotoImage(file=risorsa("diagrammi_hr.png"))
        root.iconphoto(True, icona)
    except Exception:
        pass
    root.geometry("1500x950")
    try:
        root.attributes("-zoomed", True)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


# =========================================================================== autotest e righe di comando
def griglia_finta():
    """Isocrone semplificate nel formato MIST, per l'autotest senza rete."""
    colonne = ("EEP log10_isochrone_age_yr initial_mass star_mass log_Teff log_g log_L [Fe/H]_init [Fe/H] "
               "Gaia_G_EDR3 Gaia_BP_EDR3 Gaia_RP_EDR3 phase").split()
    blocchi = []
    for feh in (0.0, -1.5):
        righe = ["# MIST version number  = 1.2"]
        for eta in np.round(np.arange(6.0, 10.16, 0.05), 2):
            cto = -0.3 + 0.25 * (eta - 6) + 0.1 * feh
            c_ms = np.linspace(3.0, cto, 300)
            g_ms = 1.0 + 4.0 * (c_ms + 0.3) + 0.5 * (c_ms + 0.3) ** 2 - 0.6 * (eta - 6) / 4
            t = np.linspace(0, 1, 150)
            c = np.concatenate([c_ms, cto + 1.2 * t])
            g = np.concatenate([g_ms, g_ms[-1] - 0.5 - np.minimum(t / 0.5, 1) - 4 * np.clip((t - 0.5) / 0.5, 0, 1)])
            fase = np.concatenate([np.zeros(300), 2 * np.ones(150)])
            righe += ["", f"# number of EEPs, cols = {len(c)} {len(colonne)}", "# " + "  ".join(colonne)]
            for i in range(len(c)):
                bp = g[i] + 0.4 * c[i]
                righe.append(" ".join(f"{v:.5f}" for v in (i + 1, eta, 0.1 + i * 0.01, 0.1, 3.7, 4.4, 0, feh, feh,
                                                            g[i], bp, bp - c[i], fase[i])))
        blocchi += M.leggi_iso_cmd("\n".join(righe), feh)
    return M.GrigliaIsocrone(blocchi, "finta")


def ammasso_simulato(griglia, vero, n_m=900, n_f=5000, binarie=0.2, seme=5):
    rng = np.random.default_rng(seme)
    iso = M.isocrona_spostata(griglia, 0.0, vero["log_eta"], vero["dm"], vero["av"])
    k = rng.integers(0, len(iso["G"]), n_m)
    cm = iso["colore"][k] + rng.normal(0, 0.02, n_m)
    Gm = iso["G"][k] + rng.normal(0, 0.02, n_m)
    Gm[rng.random(n_m) < binarie] -= 0.5
    cf, Gf = rng.uniform(0.3, 2.8, n_f), rng.uniform(8, 18.9, n_f)
    plx = 1000 / 10 ** (vero["dm"] / 5 + 1) - M.ZP_PARALLASSE
    amm = dict(chiave="autotest", it="Ammasso simulato", en="Simulated cluster", ra=100.0, dec=20.0, raggio=0.5,
               tipo="aperto", feh=0.0)
    c0 = math.cos(math.radians(20))
    rm, tm = np.abs(rng.normal(0, 0.08, n_m)), rng.uniform(0, 2 * np.pi, n_m)
    rf, tf = 0.5 * np.sqrt(rng.random(n_f)), rng.uniform(0, 2 * np.pi, n_f)
    cat = np.concatenate
    N = n_m + n_f
    d = dict(source_id=np.arange(N, dtype=np.int64),
             ra=100 + cat([rm * np.cos(tm), rf * np.cos(tf)]) / c0, dec=20 + cat([rm * np.sin(tm), rf * np.sin(tf)]),
             parallax=cat([plx + rng.normal(0, 0.06, n_m), rng.exponential(0.8, n_f)]), parallax_error=np.full(N, 0.06),
             pmra=cat([rng.normal(-10, 0.15, n_m), rng.normal(-1, 6, n_f)]),
             pmdec=cat([rng.normal(5, 0.15, n_m), rng.normal(-3, 6, n_f)]),
             pmra_error=np.full(N, 0.05), pmdec_error=np.full(N, 0.05),
             phot_g_mean_mag=cat([Gm, Gf]), bp_rp=cat([cm, cf]), ruwe=np.ones(N))
    d["phot_bp_mean_mag"] = d["phot_g_mean_mag"] + 0.4 * d["bp_rp"]
    d["phot_rp_mean_mag"] = d["phot_bp_mean_mag"] - d["bp_rp"]
    return amm, d, np.arange(N) < n_m


def autotest():
    esiti = []

    def prova(nome, ok, dettaglio=""):
        esiti.append(ok)
        print(f"[{'ok' if ok else 'ERRORE'}] {nome}  {dettaglio}", flush=True)

    print(f"Diagrammi H-R {VERSIONE} – autotest ({time.strftime('%Y-%m-%d %H:%M')})  {M.FIRMA}")
    print(f"Python {sys.version.split()[0]}, numpy {np.__version__}")
    prova("matplotlib", M.HA_MPL)
    prova("scikit-learn (HDBSCAN)", M.HA_SKLEARN)
    try:
        import tkinter
        prova("tkinter", True, tkinter.TkVersion)
        if M.HA_MPL:
            from matplotlib.backends import backend_tkagg  # noqa: F401
            prova("matplotlib TkAgg", True)
            import PIL._tkinter_finder  # noqa: F401  (serve alle icone della barra dei grafici)
            prova("Pillow per Tk (PIL._tkinter_finder)", True)
    except Exception as e:
        prova("tkinter / Pillow per Tk", False, f"{type(e).__name__}: {e}")
    # finestra vera per un istante: grafico + barra degli strumenti (come all'avvio)
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        try:
            import tkinter as tk
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
            r = tk.Tk(className="DiagrammiHR")
            r.withdraw()
            try:
                r.iconphoto(True, tk.PhotoImage(file=risorsa("diagrammi_hr.png")))
            except Exception:
                pass
            can = FigureCanvasTkAgg(Figure(figsize=(3, 2)), master=r)
            bar = NavigationToolbar2Tk(can, r, pack_toolbar=False)
            bar.update()
            can.draw()
            r.update()
            r.destroy()
            prova("finestra di prova con grafico e barra degli strumenti", True)
        except Exception as e:
            prova("finestra di prova con grafico e barra degli strumenti", False, f"{type(e).__name__}: {e}")
    else:
        prova("finestra di prova (saltata: nessuno schermo)", True)
    prova("icona", os.path.exists(risorsa("diagrammi_hr.png")))
    t0 = time.time()
    g = griglia_finta()
    with tempfile.TemporaryDirectory() as tmp:
        g.salva(Path(tmp) / "g.npz")
        g = M.GrigliaIsocrone.carica(Path(tmp) / "g.npz")
        prova("lettura isocrone formato MIST", len(g.eta_valori(0)) == 84 and g.feh_valori == [-1.5, 0.0],
              f"{time.time() - t0:.1f} s")
        if M.griglia_pronta():
            t1 = time.time()
            vera = M.carica_griglia()
            prova("caricamento delle isocrone MIST vere", time.time() - t1 < 10 and len(vera.feh_valori) > 10,
                  f"{time.time() - t1:.1f} s, {len(vera.feh_valori)} metallicità")
        vero = dict(log_eta=9.5, dm=9.0, av=0.3)
        amm, dati, veri = ammasso_simulato(g, vero)
        for sk in ([True, False] if M.HA_SKLEARN else [False]):
            M.HA_SKLEARN, salva = sk, M.HA_SKLEARN
            t0 = time.time()
            p, info = M.calcola_membri(dati, amm, None, avviso=lambda s: None)
            M.HA_SKLEARN = salva
            sel = p >= 0.7
            compl = (sel & veri).sum() / veri.sum()
            cont = (sel & ~veri).sum() / max(sel.sum(), 1)
            prova(f"membri ({'HDBSCAN + EM' if sk else 'solo EM'})", compl > 0.95 and cont < 0.03,
                  f"completezza {compl:.3f}, contaminazione {cont:.3f}, {time.time() - t0:.1f} s")
        an = dict(ammasso=amm, versione=VERSIONE, data=time.strftime("%Y-%m-%d %H:%M"), soglia=0.7, dati=dati, p=p,
                  membri=info, fonte="simulato", lett={"CG20": None, "HR23": None})
        an["dist_plx"] = M.distanza_da_parallasse(dati, p, 0.7)
        prova("distanza dalla parallasse", an["dist_plx"] and abs(an["dist_plx"]["dm"] - 9.0) < 0.1,
              f"m−M {an['dist_plx']['dm']:.3f}" if an["dist_plx"] else "")
        t0 = time.time()
        mg_vero, M.MG_MAX = M.MG_MAX, 99.0   # le isocrone finte vanno bene a tutte le magnitudini
        r = M.riadatta(an, g, 0.0, avviso=lambda s: None)
        M.MG_MAX = mg_vero
        an["adattamento"] = r
        prova("adattamento isocrone", abs(r["log_eta"] - 9.5) <= 0.2 and abs(r["dm"] - 9.0) <= 0.1
              and abs(r["av"] - 0.3) <= 0.15,
              f"log età {r['log_eta']:.2f}±{r['err_log_eta']:.2f} (9,50), m−M {r['dm']:.2f} (9,00), "
              f"A_V {r['av']:.2f} (0,30), {time.time() - t0:.1f} s")
        if M.HA_MPL:
            from matplotlib.figure import Figure
            for scala in (1.0, 2.0):
                M.SCALA_TESTO = scala
                fig = Figure(figsize=(9, 7))
                M.disegna_hr(fig, an, "it", r, g)
                M.disegna_membri(Figure(figsize=(9, 7)), an, "en")
            M.SCALA_TESTO = 2.0
            M.salva_png(Path(tmp) / "scala.png", an, "it", g, r)
            prova("dimensione testo (grafici al 100 % e al 200 %; PNG sempre al 100 %)", M.SCALA_TESTO == 2.0)
            M.SCALA_TESTO = 1.0
        for L in ("it", "en"):
            mancano = [k for k in M.TESTI["it"] if k not in M.TESTI[L]]
            prova(f"testi {L}", not mancano, ", ".join(mancano))
        try:
            cartella, fatti = M.salva_tutto(an, "it", g, Path(tmp) / "ris")
            if M.HA_MPL:
                fatti.append(M.salva_pdf(Path(tmp) / "en.pdf", an, "en", g))
            prova("file dei risultati (CSV, JSON, PDF, PNG)", all(f.stat().st_size > 500 for f in fatti),
                  ", ".join(f"{f.name} {f.stat().st_size // 1024} kB" for f in fatti))
        except Exception as e:
            prova("file dei risultati", False, f"{e}\n{traceback.format_exc()}")
        # filtro dei colori contaminati (C*, Riello et al. 2021)
        prova_c = dict(bp_rp=np.array([1.0, 1.0, 1.0]), phot_g_mean_mag=np.array([15.0, 15.0, 15.0]),
                       phot_bp_rp_excess_factor=np.array([1.2189, 1.40, np.nan]))
        cb = M.colori_buoni(prova_c)
        prova("filtro dei colori contaminati", bool(cb[0]) and not cb[1] and bool(cb[2]), str(cb.tolist()))
        prova("tetto all'età (14 miliardi di anni)", M.ETA_MAX <= 10.15 and r["log_eta"] <= M.ETA_MAX + 1e-6)
        tsv = ("#\n_r\tCluster\tpmRA*\tpmDE\tplx\tAgeNN\tAVNN\tDMNN\tDistPc\n\t\t\t\t\t\t\t\tpc\n"
               "---\t---\t---\t---\t---\t---\t---\t---\t---\n0.01\tMelotte_22\t19.997\t-45.548\t7.36\t7.89\t0.17\t5.63\t135\n")
        v = M._valori_lett(*[x[0] if i else x for i, x in enumerate(M._leggi_tsv_vizier(tsv))])
        prova("lettura tabelle VizieR", v["log_eta"] == 7.89 and v["dist_pc"] == 135 and v["nome"] == "Melotte_22")
    prova("griglia MIST vera già pronta (facoltativo)", True, "sì" if M.griglia_pronta() else
          "no: si scarica alla prima analisi o con --prepara-mist")
    ok = all(esiti)
    print("AUTOTEST " + ("RIUSCITO" if ok else "FALLITO"))
    return 0 if ok else 1


def diagnostica():
    print(f"Diagrammi H-R {VERSIONE} – diagnostica della rete ({time.strftime('%Y-%m-%d %H:%M')})")
    amm = dict(M.AMMASSI[0], chiave="diagnostica", raggio=0.1)
    for nome, url, par, tab in (
            ("Gaia IRSA", M.URL_IRSA, lambda q: {"QUERY": q, "LANG": "ADQL", "FORMAT": "csv"}, "gaia_dr3_source"),
            ("Gaia ESA", M.URL_ESA, lambda q: {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": q},
             "gaiadr3.gaia_source")):
        t0 = time.time()
        try:
            d = M.leggi_csv_gaia(M.richiesta(url, par(M.adql_gaia(amm, tab, 50)), tempo=120))
            print(f"[ok] {nome}: {len(d['source_id'])} stelle in {time.time() - t0:.1f} s")
        except Exception as e:
            print(f"[ERRORE] {nome}: {e}")
    t0 = time.time()
    lett = M.letteratura(M.AMMASSI[0], print, forza=True)
    for k, v in lett.items():
        corto = {a: b for a, b in (v or {}).items() if a != "_colonne"}
        print(f"[{'ok' if v and v.get('log_eta') else 'ERRORE'}] letteratura {k}: {corto}")
        if v:
            print("     colonne: " + ", ".join(f"{a}={b}" for a, b in v.get("_colonne", {}).items())[:1500])
    # risposte grezze dello specchio VizieR per Hunt & Reffert (per trovare le colonne dell'età)
    for colonne in M.VARIANTI_VIZIER:
        try:
            testo = M._vizier_testo(M.CAT_HR23, M.AMMASSI[0], 0.3, colonne)
            utili = [r for r in testo.splitlines() if r.strip() and not r.startswith("#")][:4]
            print(f"[--] HR23 con {colonne}: {len(testo)} caratteri")
            for r in utili:
                print("     | " + r[:1200])
            commenti = [r for r in testo.splitlines() if r.startswith("#") and ("Error" in r or "error" in r or "Table" in r)][:3]
            for r in commenti:
                print("     # " + r[:300])
        except Exception as e:
            print(f"[ERRORE] HR23 con {colonne}: {e}")
    try:
        req = M.urllib.request.Request(M.URL_MIST, headers={"User-Agent": M.UA, "Range": "bytes=0-1023"})
        with M.urllib.request.urlopen(req, timeout=30) as r:
            print(f"[ok] MIST: HTTP {r.status}, {r.headers.get('Content-Range', '')}")
    except Exception as e:
        print(f"[ERRORE] MIST: {e}")
    print(f"[--] isocrone MIST pronte: {'sì' if M.griglia_pronta() else 'no'}")
    return 0


def analisi_da_riga(nomi, lingua, forza):
    griglia = None
    if not M.griglia_pronta():
        print("Preparo le isocrone MIST (una volta sola)…")
        griglia = M.scarica_e_prepara_mist(print, lambda f, mb, tot: print(f"  {mb:.0f}/{tot:.0f} MB", end="\r")
                                           if mb else None)
    else:
        griglia = M.carica_griglia()
    errori = 0
    for nome in nomi:
        amm = M.trova_ammasso(nome)
        if amm is None:
            print(f"Ammasso sconosciuto: {nome}. Nomi validi: " + ", ".join(a["it"] for a in M.AMMASSI))
            errori += 1
            continue
        print(f"\n=== {M.nome_ammasso(amm, lingua)} ===")
        try:
            an = M.analizza(amm, griglia, forza, print)
            cartella, fatti = M.salva_tutto(an, lingua, griglia)
            for a, b in M.righe_risultati(an, lingua):
                print(f"  {a}: {b}")
            for a, b in M.righe_letteratura(an, lingua):
                print(f"  {a}: {b}")
            print(f"  risultati in {cartella}")
        except Exception as e:
            errori += 1
            print(f"  ERRORE: {e}")
            traceback.print_exc()
    return 1 if errori else 0


def main():
    ap = argparse.ArgumentParser(description=f"Diagrammi H-R {VERSIONE} – {M.FIRMA}")
    ap.add_argument("--autotest", action="store_true")
    ap.add_argument("--diagnostica", action="store_true")
    ap.add_argument("--prepara-mist", action="store_true")
    ap.add_argument("--analizza", nargs="+", metavar="AMMASSO")
    ap.add_argument("--tutti", action="store_true")
    ap.add_argument("--lingua", default="it", choices=["it", "en"], help="lingua dei messaggi (i PDF sono sempre in inglese)")
    ap.add_argument("--riscarica", action="store_true")
    a = ap.parse_args()
    if a.autotest:
        sys.exit(autotest())
    if a.diagnostica:
        sys.exit(diagnostica())
    if a.prepara_mist:
        M.scarica_e_prepara_mist(print, lambda f, mb, tot: print(f"  {mb:.0f}/{tot:.0f} MB", end="\r") if mb else None)
        print("Isocrone pronte:", M.file_griglia())
        sys.exit(0)
    if a.analizza or a.tutti:
        nomi = a.analizza or [x["chiave"] for x in M.AMMASSI]
        sys.exit(analisi_da_riga(nomi, a.lingua, a.riscarica))
    avvia_finestra()


if __name__ == "__main__":
    main()
