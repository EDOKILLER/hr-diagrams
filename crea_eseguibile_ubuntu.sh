#!/usr/bin/env bash
# =============================================================================
# Diagrammi H-R 1.4 – crea l'eseguibile per Ubuntu e lo installa (menu + Scrivania)
# © 2026 Edoardo Battistel
#
# Uso:  cd ~/Claude/Astropy/programma_diagrammi_hr/versione_1.4
#       bash crea_eseguibile_ubuntu.sh
# Usa il venv ~/Claude/Astropy/venv_compila (PyInstaller), che vede numpy, scipy,
# matplotlib, scikit-learn e Pillow di sistema (python3-matplotlib, python3-sklearn).
# =============================================================================
set -e
cd "$(dirname "$(readlink -f "$0")")"
PY="$HOME/Claude/Astropy/venv_compila/bin/python"

for f in diagrammi_hr.py motore_hr.py diagrammi_hr.png; do
    [ -f "$f" ] || { echo "ERRORE: manca $f in questa cartella ($(pwd))."; exit 1; }
done
[ -x "$PY" ] || { echo "ERRORE: non trovo $PY"; exit 1; }

echo "=== 1/4  Controllo dei pacchetti (autotest dallo script) ==="
if ! "$PY" diagrammi_hr.py --autotest; then
    echo "L'autotest dello script è FALLITO: mandami il testo qui sopra."
    exit 1
fi

echo "=== 2/4  Creazione dell'eseguibile (qualche minuto) ==="
"$PY" -m PyInstaller --noconfirm --clean --onefile --windowed \
    --name DiagrammiHR \
    --add-data "diagrammi_hr.png:." \
    --hidden-import matplotlib.backends.backend_tkagg \
    --hidden-import matplotlib.backends.backend_pdf \
    --hidden-import motore_hr \
    --hidden-import PIL._tkinter_finder \
    --hidden-import PIL.ImageTk \
    --collect-submodules sklearn.cluster \
    --collect-submodules sklearn.utils \
    --exclude-module astropy --exclude-module IPython --exclude-module pandas \
    diagrammi_hr.py

echo "=== 3/4  Verifica dell'eseguibile (autotest) ==="
if ! ./dist/DiagrammiHR --autotest > autotest.txt 2>&1; then
    cat autotest.txt
    echo
    echo "L'autotest dell'eseguibile è FALLITO: i dettagli sono in $(pwd)/autotest.txt"
    echo "L'eseguibile non è stato installato. Mandami autotest.txt per la correzione."
    rm -rf build DiagrammiHR.spec
    exit 1
fi
cat autotest.txt

echo "=== Prova degli archivi in rete (diagnostica) ==="
./dist/DiagrammiHR --diagnostica > diagnostica.txt 2>&1 || true
grep -E "^\[(ok|ERRORE|--)\]" diagnostica.txt | cut -c1-160 || true
echo "(dettagli in $(pwd)/diagnostica.txt)"

echo "=== 4/4  Installazione nella home e icona nel menu ==="
DEST="$HOME/.local/bin"
ICONE="$HOME/.local/share/icons/hicolor/512x512/apps"
mkdir -p "$DEST" "$HOME/.local/share/applications" "$ICONE"
cp diagrammi_hr.png "$ICONE/diagrammi-hr.png"
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
cp dist/DiagrammiHR "$DEST/DiagrammiHR"
chmod +x "$DEST/DiagrammiHR"

# Tk dichiara la classe della finestra "Diagrammihr" (solo la prima maiuscola): StartupWMClass
# va scritto così e il file .desktop ha lo stesso nome in minuscolo.
SCRIVANIA="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Scrivania")"
cat > "$HOME/.local/share/applications/diagrammihr.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Diagrammi H-R
Comment=Diagrammi colore-magnitudine degli ammassi stellari con Gaia DR3 e isocrone MIST
Exec=$DEST/DiagrammiHR
Icon=$ICONE/diagrammi-hr.png
StartupWMClass=Diagrammihr
Terminal=false
Categories=Science;Astronomy;Education;
EOF
chmod +x "$HOME/.local/share/applications/diagrammihr.desktop"
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
if [ -d "$SCRIVANIA" ]; then
    cp "$HOME/.local/share/applications/diagrammihr.desktop" "$SCRIVANIA/"
    chmod +x "$SCRIVANIA/diagrammihr.desktop"
    gio set "$SCRIVANIA/diagrammihr.desktop" metadata::trusted true 2>/dev/null || true
fi

rm -rf build DiagrammiHR.spec
echo
echo "FATTO."
echo "  Eseguibile:  $DEST/DiagrammiHR   (copia anche in $(pwd)/dist/)"
echo "  Avvio:       'Diagrammi H-R' nel menu delle applicazioni o doppio clic sull'icona sulla Scrivania"
echo "               (la prima volta: tasto destro -> 'Consenti avvio')."
echo "  Il titolo della finestra deve dire 'Diagrammi H-R $(grep -m1 '^VERSIONE' motore_hr.py | cut -d'"' -f2)'."
echo "  Controllo dell'icona nella barra: xwininfo -root -tree | grep -i diagrammi"
