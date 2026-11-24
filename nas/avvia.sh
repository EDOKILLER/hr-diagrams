#!/bin/bash
# Diagrammi H-R 1.4 - analisi di tutti gli ammassi nel container Docker del NAS
# © 2026 Edoardo Battistel
# Installa le librerie in /dati/programma/_python (2-5 minuti, serve Internet), analizza tutti
# gli ammassi a priorità minima, poi cancella librerie e dati temporanei e TERMINA:
# il container si ferma da solo (restart: "no"). Restano solo le schede in /dati/schede.
umask 000                                   # file leggibili e cancellabili anche dal PC
cd /dati/programma || exit 1
export HOME=/dati/programma/_home PYTHONUNBUFFERED=1 MPLBACKEND=Agg MPLCONFIGDIR=/dati/programma/_home/mpl
mkdir -p "$HOME"
VENV=/dati/programma/_python
if [ -f /dati/schede/00_summary.pdf ] && [ ! -d /dati/_lavoro ]; then
    echo "Lavoro già finito: le schede sono in /dati/schede. Per rifare tutto cancella schede/00_summary.pdf."
    exit 0
fi
if [ ! -f "$VENV/.pronto_1.4" ]; then
    echo "Installo le librerie (2-5 minuti, serve Internet)…"
    until python -m venv "$VENV" && \
          "$VENV/bin/pip" install --no-cache-dir --upgrade pip && \
          "$VENV/bin/pip" install --no-cache-dir -r requisiti.txt; do
        echo "Installazione non riuscita (Internet assente?): riprovo tra 10 minuti"
        sleep 600
    done
    touch "$VENV/.pronto_1.4"
fi
nice -n 19 ionice -c 3 "$VENV/bin/python" servizio_hr.py /dati "$@"
esito=$?
if [ $esito -eq 0 ] && [ -f /dati/schede/00_summary.pdf ] && [ ! -d /dati/_lavoro ]; then
    echo "Pulizia: cancello le librerie (si reinstallano da sole se un giorno lo riavvii)."
    rm -rf "$VENV" "$HOME"
    echo "Fine. Il container si ferma."
fi
exit $esito
