#!/usr/bin/env bash
# USKMaker - setup do ambiente de IA no Linux (rode uma vez depois de instalar o app)
#
# Equivalente Linux do scripts/setup-sidecar.ps1. As LIÇÕES daquele script
# (pin do torch, --upgrade com constraints, validação import a import) valem
# aqui igual - os comentários abaixo apontam para lá quando a história é a
# mesma, e só contam por extenso o que é específico do Linux.
#
# Pode ser iniciado de dois jeitos:
#   - pelo BOTÃO "Configurar ambiente de IA" dentro do app (passa --unattended;
#     ver platform::setup_command em src-tauri/src/platform.rs);
#   - à mão, num terminal:  bash scripts/setup-sidecar.sh
#
# O que ele faz (NÃO precisa de Python instalado - o uv cuida disso):
#   1. Localiza o código do sidecar (python-sidecar/requirements.txt)
#   2. Checagens prévias do sistema: compilador C, ffmpeg com libvorbis, git
#   3. Acha ou baixa o uv (gerenciador de Python/pacotes da Astral) em bin
#   4. Detecta a GPU (NVIDIA -> CUDA, AMD -> ROCm, senão CPU) para escolher o torch
#   5. Cria o venv em <pasta de dados>/venv com Python 3.12 (o uv baixa um
#      Python gerenciado se não houver 3.12 na máquina - o da distro costuma
#      ser mais novo, e o pipeline exige 3.12)
#   6. Instala o Deno (runtime JavaScript que o yt-dlp usa no YouTube)
#   7. Instala as dependências (torch primeiro, depois o requirements) via uv
#   8. Valida a instalação
#
# <pasta de dados> = $XDG_DATA_HOME/USKMaker (só se XDG_DATA_HOME for caminho
# absoluto) ou ~/.local/share/USKMaker - EXATAMENTE a regra de
# platform::data_dir no app. Se as duas divergirem, o app não acha o venv.
#
# Nada aqui usa sudo: o que falta no sistema é DETECTADO e explicado, com o
# comando da distro para instalar, mas quem instala é o usuário.
#
# Variável opcional:
#   USKMAKER_TORCH_INDEX=<url>  força o índice do torch (ex.: o de CPU,
#                               https://download.pytorch.org/whl/cpu, numa
#                               máquina cuja GPU a detecção escolheu errado).

set -euo pipefail

UNATTENDED=0
for arg in "$@"; do
    case "$arg" in
        --unattended|-Unattended|-y|--yes) UNATTENDED=1 ;;
        -h|--help)
            sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'
            echo "Uso: bash setup-sidecar.sh [--unattended]"
            exit 0
            ;;
        *) echo "Opção desconhecida: $arg (use --help)" >&2; exit 2 ;;
    esac
done

# Cores SÓ em terminal. Quando o app roda o setup, a saída vai para um log que
# a UI mostra linha a linha - códigos ANSI lá viram lixo na tela.
if [[ -t 1 ]]; then
    C_CYAN=$'\e[36m'; C_GREEN=$'\e[32m'; C_YELLOW=$'\e[33m'; C_RED=$'\e[31m'
    C_GRAY=$'\e[90m'; C_RESET=$'\e[0m'
else
    C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_GRAY=""; C_RESET=""
fi

step() { printf '\n%s==> %s%s\n' "$C_CYAN" "$1" "$C_RESET"; }
ok()   { printf '    %s[OK]%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }
warn() { printf '    %s[WARNING]%s %s\n' "$C_YELLOW" "$C_RESET" "$1"; }
info() { printf '    %s\n' "$1"; }
fail() {
    printf '\n%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1"
    exit 1
}
# Mostra o FIM de uma saída capturada (é onde mora a exceção de verdade num
# traceback), recuada e em cinza.
show_tail() {
    printf '%s\n' "$1" | tail -n 15 | while IFS= read -r line; do
        printf '      %s%s%s\n' "$C_GRAY" "$line" "$C_RESET"
    done
}

echo "${C_CYAN}=============================================${C_RESET}"
echo "${C_CYAN} USKMaker - AI environment setup (Linux) ${C_RESET}"
echo "${C_CYAN}=============================================${C_RESET}"

if [[ "$(uname -s)" != "Linux" ]]; then
    fail "This script is for Linux (detected: $(uname -s)). On Windows use setup-sidecar.ps1."
fi

# ---------------------------------------------------------------------------
# Pasta de dados - espelho fiel de platform::data_dir (src-tauri/src/platform.rs)
# ---------------------------------------------------------------------------
if [[ -n "${XDG_DATA_HOME:-}" && "${XDG_DATA_HOME}" == /* ]]; then
    USK_DIR="${XDG_DATA_HOME}/USKMaker"
elif [[ -n "${HOME:-}" ]]; then
    USK_DIR="${HOME}/.local/share/USKMaker"
else
    fail "Neither XDG_DATA_HOME (absolute) nor HOME is set - cannot decide where to install."
fi
BIN_DIR="$USK_DIR/bin"
VENV_DIR="$USK_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"

# Pasta temporária para downloads DENTRO da pasta de dados, não em /tmp: em
# muitas distros o /tmp é tmpfs (memória RAM), e o uv/Deno somam dezenas de MB.
TMP_DIR=""
# shellcheck disable=SC2329  # chamada pelo trap abaixo
cleanup() { if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then rm -rf "$TMP_DIR"; fi; }
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Localizar o código do sidecar (instalado junto do app, como resource)
# ---------------------------------------------------------------------------
step "Locating the sidecar code"

# Este script mora em .../scripts/ e o python-sidecar é a pasta IRMÃ (no app
# instalado as duas ficam sob resources/_up_/, igual no Windows).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDECAR_DIR=""
for cand in "$(dirname "$SCRIPT_DIR")/python-sidecar" "$SCRIPT_DIR/python-sidecar"; do
    if [[ -f "$cand/requirements.txt" ]]; then
        SIDECAR_DIR="$cand"
        break
    fi
done
[[ -n "$SIDECAR_DIR" ]] || fail "Could not find the python-sidecar folder next to this script ($SCRIPT_DIR). Run it from the USKMaker installation or repository."
REQ_FILE="$SIDECAR_DIR/requirements.txt"
ok "Sidecar at: $SIDECAR_DIR"

# ---------------------------------------------------------------------------
# 2. Checagens prévias do sistema (Linux)
# ---------------------------------------------------------------------------
# No Windows o setup baixa tudo que precisa (ffmpeg incluso). No Linux o
# normal é o ffmpeg e o compilador virem da DISTRO - e instalar pacote de
# sistema exige root, que um setup rodado por um botão do app não deve pedir.
# Então só detectamos, ANTES de baixar gigabytes, e explicamos o comando.
step "Checking system requirements"

PKG_MGR=""
for pm in dnf apt-get pacman zypper; do
    if command -v "$pm" >/dev/null 2>&1; then PKG_MGR="$pm"; break; fi
done

# Dica de instalação por gerenciador. Argumentos: dnf apt pacman zypper.
pkg_hint() {
    case "$PKG_MGR" in
        dnf)     info "  Install it with:  sudo dnf install $1" ;;
        apt-get) info "  Install it with:  sudo apt install $2" ;;
        pacman)  info "  Install it with:  sudo pacman -S $3" ;;
        zypper)  info "  Install it with:  sudo zypper install $4" ;;
        *)
            info "  Install it with your distribution's package manager, e.g.:"
            info "    Fedora:        sudo dnf install $1"
            info "    Debian/Ubuntu: sudo apt install $2"
            info "    Arch:          sudo pacman -S $3"
            ;;
    esac
}

PREFLIGHT_FAILED=0

# Compilador C: OBRIGATÓRIO. O `diffq` (dependência do audio-separator, via
# modelos do UVR) não publica wheel para Python 3.12 no Linux - só sdist, que
# o uv compila na hora. Sem compilador a instalação morre lá no meio do passo
# de dependências, com um erro de build que não diz "falta o gcc" de um jeito
# óbvio - depois de já ter baixado o torch inteiro. Melhor barrar aqui.
if command -v cc >/dev/null 2>&1; then
    ok "C compiler: $(command -v cc)"
elif command -v gcc >/dev/null 2>&1; then
    # Sem `cc` no PATH, o setuptools não acharia o compilador sozinho.
    export CC=gcc
    ok "C compiler: $(command -v gcc) (using CC=gcc)"
else
    printf '    %s[MISSING]%s A C compiler (cc/gcc) - required to build diffq (used by audio-separator).\n' "$C_RED" "$C_RESET"
    pkg_hint "gcc" "build-essential" "base-devel" "gcc"
    PREFLIGHT_FAILED=1
fi

# ffmpeg com libvorbis: o pipeline gera o áudio do pacote em .ogg (Vorbis).
# O app prefere <pasta de dados>/bin/ffmpeg (ver resolve_ffmpeg no main.rs);
# no Linux o normal é NÃO haver esse e usar o da distro no PATH.
#
# ARMADILHA DO FEDORA: o pacote `ffmpeg-free` (repositório oficial) TEM o
# libvorbis - o que ele corta são codecs patenteados, não o Vorbis. Não é
# preciso o ffmpeg do RPM Fusion para o USKMaker.
#
# NÃO é fatal para o setup: o ambiente Python fica pronto sem ele, e o
# usuário pode instalar o ffmpeg depois. Mas sem ele nenhuma música é gerada,
# então o aviso é grande e se repete no fim.
FFMPEG_BIN=""
if [[ -x "$BIN_DIR/ffmpeg" ]]; then
    FFMPEG_BIN="$BIN_DIR/ffmpeg"
elif command -v ffmpeg >/dev/null 2>&1; then
    FFMPEG_BIN="$(command -v ffmpeg)"
fi
FFMPEG_PROBLEM=""
if [[ -z "$FFMPEG_BIN" ]]; then
    FFMPEG_PROBLEM="ffmpeg was not found on PATH - USKMaker needs it to process audio."
elif ! "$FFMPEG_BIN" -hide_banner -encoders 2>/dev/null | grep -q libvorbis; then
    FFMPEG_PROBLEM="$FFMPEG_BIN has no libvorbis encoder - USKMaker writes the song audio as .ogg (Vorbis)."
fi
if [[ -z "$FFMPEG_PROBLEM" ]]; then
    ok "ffmpeg with libvorbis: $FFMPEG_BIN"
else
    warn "$FFMPEG_PROBLEM"
    pkg_hint "ffmpeg-free" "ffmpeg" "ffmpeg" "ffmpeg"
    info "  (Not fatal for this setup - but no song can be generated until it is fixed.)"
fi

# git: só se o requirements voltar a ter alguma linha "git+https://...". Hoje
# não tem (o whisperx vem do PyPI desde 16/07/2026 justamente para não exigir
# Git - ver requirements.txt), mas se alguém reintroduzir, a falha tem que ser
# aqui, clara, e não no meio da instalação.
if grep -Eq '^[[:space:]]*[^#[:space:]].*git\+' "$REQ_FILE"; then
    if command -v git >/dev/null 2>&1; then
        ok "git: $(command -v git) (requirements.txt has git+ dependencies)"
    else
        printf '    %s[MISSING]%s git - requirements.txt installs packages straight from git.\n' "$C_RED" "$C_RESET"
        pkg_hint "git" "git" "git" "git"
        PREFLIGHT_FAILED=1
    fi
fi

# Ferramenta de download: só é necessária se precisarmos baixar o uv/Deno.
DOWNLOADER=""
if command -v curl >/dev/null 2>&1; then
    DOWNLOADER="curl"
elif command -v wget >/dev/null 2>&1; then
    DOWNLOADER="wget"
fi

if [[ "$PREFLIGHT_FAILED" -ne 0 ]]; then
    fail "Required system packages are missing (see above). Install them and run this setup again."
fi

# Baixa $1 para o arquivo $2. Devolve não-zero em falha (não aborta sozinho).
download() {
    case "$DOWNLOADER" in
        curl) curl -fL --retry 3 --silent --show-error -o "$2" "$1" ;;
        wget) wget -q -O "$2" "$1" ;;
        *) echo "neither curl nor wget is installed" >&2; return 1 ;;
    esac
}

case "$(uname -m)" in
    x86_64|amd64)  ARCH_TRIPLE="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) ARCH_TRIPLE="aarch64-unknown-linux-gnu" ;;
    *) ARCH_TRIPLE="" ;;
esac

# ---------------------------------------------------------------------------
# Confirmação (só no modo interativo, num terminal)
# ---------------------------------------------------------------------------
if [[ "$UNATTENDED" -eq 0 && -t 0 ]]; then
    echo
    info "This will install the USKMaker AI environment into:"
    info "  $USK_DIR"
    info "It downloads several GB (PyTorch + AI libraries) and can take a while."
    read -r -p "    Continue? [Y/n] " answer
    case "${answer:-y}" in
        [yY]*) ;;
        *) echo "    Cancelled."; exit 1 ;;
    esac
fi

mkdir -p "$BIN_DIR" || fail "Could not create $BIN_DIR"
TMP_DIR="$(mktemp -d "$USK_DIR/.setup-tmp.XXXXXX")" || fail "Could not create a temporary folder in $USK_DIR"

# ---------------------------------------------------------------------------
# 3. uv (não precisa de Python pré-existente - ele instala o Python 3.12)
# ---------------------------------------------------------------------------
# Ordem de busca: PATH, depois <pasta de dados>/bin/uv, e só então baixa para
# lá. O update_ytdlp.py procura o uv nos mesmos dois lugares (bin primeiro),
# então o uv baixado aqui é o mesmo que o app usa para atualizar o yt-dlp.
step "Setting up uv (Python/package manager)"

UV=""
if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
    ok "Using uv from PATH: $UV ($("$UV" --version 2>/dev/null || echo '?'))"
elif [[ -x "$BIN_DIR/uv" ]]; then
    UV="$BIN_DIR/uv"
    ok "uv already exists in $BIN_DIR"
else
    [[ -n "$ARCH_TRIPLE" ]] || fail "No uv download for this CPU architecture ($(uname -m)). Install uv yourself (https://docs.astral.sh/uv/) and run again."
    [[ -n "$DOWNLOADER" ]] || fail "Neither curl nor wget is installed, so uv cannot be downloaded. Install curl (or uv itself) and run again."
    info "Downloading uv..."
    uv_tgz="$TMP_DIR/uv.tar.gz"
    download "https://github.com/astral-sh/uv/releases/latest/download/uv-$ARCH_TRIPLE.tar.gz" "$uv_tgz" \
        || fail "Failed to download uv."
    tar -xzf "$uv_tgz" -C "$TMP_DIR" || fail "Failed to extract uv."
    src_uv="$(find "$TMP_DIR" -type f -name uv | head -n 1)"
    [[ -n "$src_uv" ]] || fail "uv was not found inside the downloaded archive."
    install -m 755 "$src_uv" "$BIN_DIR/uv" || fail "Failed to copy uv into $BIN_DIR."
    UV="$BIN_DIR/uv"
    ok "uv installed in $BIN_DIR ($("$UV" --version 2>/dev/null || echo '?'))"
fi

# ---------------------------------------------------------------------------
# 4. Detectar a GPU (escolhe o build do torch: CUDA cu128/cu126, ROCm ou CPU)
# ---------------------------------------------------------------------------
# NVIDIA: mesma regra do setup-sidecar.ps1 (ver a história do RTX 5080 lá).
# Não basta saber que HÁ uma NVIDIA - o build do torch precisa ter os kernels
# da GERAÇÃO da placa. Blackwell (RTX 50, compute 12.0) só tem kernels a
# partir do CUDA 12.8; o resto segue no cu126 de sempre. Regra conservadora
# de propósito: só >= 12.0 muda de caminho.
#
# AMD: o torch tem build ROCm, que se apresenta ao código como "cuda" (o
# torch.cuda.is_available() dá True e o resolve_device do main.py funciona
# sem mudança). Exigimos DUAS coisas:
#   - um dispositivo de vídeo PCI da AMD (vendor 0x1002) em /sys/class/drm;
#   - o /dev/kfd, a interface de computação do driver amdgpu. Sem ele o ROCm
#     não enxerga a placa e seriam gigabytes baixados à toa.
# VERIFICADO (09/2026): torch 2.8.0+rocm6.4 funciona numa RX 7800 XT (gfx1101).
#
# O extra do audio-separator na AMD é [cpu], NÃO [gpu]: o [gpu] puxa o
# onnxruntime-gpu, que é build CUDA - numa máquina sem NVIDIA ele só atrapalha.
step "Detecting the GPU"

HAS_NVIDIA=0
HAS_AMD=0
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    HAS_NVIDIA=1
fi
if [[ "$HAS_NVIDIA" -eq 0 && -e /dev/kfd ]]; then
    for vendor_file in /sys/class/drm/card*/device/vendor; do
        [[ -r "$vendor_file" ]] || continue
        if [[ "$(cat "$vendor_file" 2>/dev/null)" == "0x1002" ]]; then
            HAS_AMD=1
            break
        fi
    done
fi

if [[ "$HAS_NVIDIA" -eq 1 ]]; then
    # Driver antigo pode não conhecer o campo compute_cap; aí fica vazio e
    # caímos no cu126 histórico e seguro.
    compute_cap="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n 1 | tr -d '[:space:]' || true)"
    cap_major=0
    if [[ "$compute_cap" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
        cap_major="${BASH_REMATCH[1]}"
    fi
    if [[ "$cap_major" -ge 12 ]]; then
        ok "NVIDIA Blackwell GPU or newer detected (compute $compute_cap) - torch with CUDA (cu128)."
        TORCH_INDEX="https://download.pytorch.org/whl/cu128"
    elif [[ "$cap_major" -gt 0 ]]; then
        ok "NVIDIA GPU detected (compute $compute_cap) - torch with CUDA (cu126)."
        TORCH_INDEX="https://download.pytorch.org/whl/cu126"
    else
        warn "NVIDIA GPU detected, but the compute capability could not be read - using cu126 (default)."
        TORCH_INDEX="https://download.pytorch.org/whl/cu126"
    fi
    SEP_EXTRA="gpu"
elif [[ "$HAS_AMD" -eq 1 ]]; then
    # Só informativo: o gfx da placa ajuda a diagnosticar se o ROCm falhar.
    gfx=""
    for props in /sys/class/kfd/kfd/topology/nodes/*/properties; do
        v="$(awk '$1=="gfx_target_version"{print $2}' "$props" 2>/dev/null || true)"
        if [[ -n "$v" && "$v" != "0" ]]; then gfx="$v"; break; fi
    done
    ok "AMD GPU detected (/dev/kfd present${gfx:+, gfx_target_version $gfx}) - torch with ROCm (rocm6.4)."
    TORCH_INDEX="https://download.pytorch.org/whl/rocm6.4"
    SEP_EXTRA="cpu"
else
    warn "No NVIDIA GPU (nvidia-smi) or AMD GPU with ROCm (/dev/kfd) detected - CPU torch (it works, but ~10 min per song)."
    TORCH_INDEX="https://download.pytorch.org/whl/cpu"
    SEP_EXTRA="cpu"
fi

if [[ -n "${USKMAKER_TORCH_INDEX:-}" ]]; then
    TORCH_INDEX="$USKMAKER_TORCH_INDEX"
    warn "USKMAKER_TORCH_INDEX is set - using $TORCH_INDEX instead of the detected index."
    # Índice CUDA forçado à mão implica NVIDIA; qualquer outro, extra [cpu].
    case "$TORCH_INDEX" in */cu[0-9]*) SEP_EXTRA="gpu" ;; *) SEP_EXTRA="cpu" ;; esac
fi

# O rótulo mostrado vem do ÍNDICE ESCOLHIDO, não de texto fixo (lição do
# ps1: a mensagem dizia "cu126" enquanto instalava cu128).
TORCH_CHANNEL="${TORCH_INDEX%/}"
TORCH_CHANNEL="${TORCH_CHANNEL##*/}"   # cu128 | cu126 | rocm6.4 | cpu
case "$TORCH_CHANNEL" in
    cpu)    TORCH_LABEL="CPU" ;;
    rocm*)  TORCH_LABEL="ROCm ${TORCH_CHANNEL#rocm}" ;;
    *)      TORCH_LABEL="CUDA $TORCH_CHANNEL" ;;
esac
GPU_EXPECTED=0
[[ "$TORCH_CHANNEL" != "cpu" ]] && GPU_EXPECTED=1

# ---------------------------------------------------------------------------
# 5. Criar o venv com Python 3.12 (o uv baixa o Python se precisar)
# ---------------------------------------------------------------------------
# O Python da distro costuma ser MAIS NOVO que 3.12 (Fedora 44: 3.14), e as
# libs do pipeline não têm wheel para ele - é o mesmo "Python 3.14
# incompatível" do histórico do verify_setup.py. Por isso o 3.12 é pedido
# explicitamente ao uv, e um venv reaproveitado é conferido: um venv de outra
# versão é inútil e seria recriado de qualquer jeito na primeira falha.
step "Creating the virtual environment (Python 3.12 via uv)"

venv_version=""
if [[ -x "$VENV_PYTHON" ]]; then
    venv_version="$("$VENV_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
fi
if [[ "$venv_version" == "3.12" ]]; then
    warn "A venv already exists at $VENV_DIR - it will be reused."
else
    if [[ -e "$VENV_DIR" ]]; then
        warn "The existing venv at $VENV_DIR is not a working Python 3.12 (found: ${venv_version:-none}) - recreating it."
    fi
    "$UV" venv --clear --python 3.12 "$VENV_DIR" || fail "Failed to create the venv with uv."
    ok "venv created at: $VENV_DIR"
fi

# ---------------------------------------------------------------------------
# 6. Deno - o runtime JavaScript que o yt-dlp agora exige para o YouTube
# ---------------------------------------------------------------------------
# Mesma história do ps1: sem runtime JS o yt-dlp avisa "No supported
# JavaScript runtime could be found ... some formats may be missing", e o
# "deprecated" de hoje vira download quebrado amanhã. O Deno é o que o yt-dlp
# habilita por padrão e ele o procura no PATH.
#
# Se já houver `deno` no PATH, não baixamos nada. Senão vai para
# <pasta de dados>/bin, como no Windows.
#
# LIMITAÇÃO CONHECIDA NO LINUX: o sidecar só põe essa pasta bin no PATH
# quando existe o ffmpeg EMBUTIDO (ensure_ffmpeg_on_path em
# pipeline/proc_utils.py depende de USKMAKER_FFMPEG). No Linux o ffmpeg
# normalmente vem da distro, então o deno daqui só é achado pelo yt-dlp
# depois que o sidecar passar a expor a pasta bin independentemente do
# ffmpeg. Quem já tem `deno` no PATH não é afetado.
#
# NÃO é fatal: sem ele os downloads seguem exatamente como hoje.
step "Setting up Deno (the JavaScript runtime yt-dlp uses for YouTube)"

if command -v deno >/dev/null 2>&1; then
    ok "Deno found on PATH: $(command -v deno)"
elif [[ -x "$BIN_DIR/deno" ]]; then
    ok "Deno is already in $BIN_DIR"
elif [[ -z "$ARCH_TRIPLE" || -z "$DOWNLOADER" ]]; then
    warn "Could not install Deno (no download for $(uname -m), or neither curl nor wget available)."
    warn "  Not fatal - downloads still work, but YouTube may offer fewer formats."
else
    info "Downloading Deno (~45 MB)..."
    deno_zip="$TMP_DIR/deno.zip"
    deno_ok=0
    if download "https://github.com/denoland/deno/releases/latest/download/deno-$ARCH_TRIPLE.zip" "$deno_zip"; then
        # O zip é extraído pelo Python do venv recém-criado: o `unzip` não vem
        # instalado em toda distro, e o módulo zipfile vem sempre.
        if "$VENV_PYTHON" -m zipfile -e "$deno_zip" "$TMP_DIR/deno" \
            && [[ -f "$TMP_DIR/deno/deno" ]] \
            && install -m 755 "$TMP_DIR/deno/deno" "$BIN_DIR/deno"; then
            deno_ok=1
        fi
    fi
    if [[ "$deno_ok" -eq 1 ]]; then
        ok "Deno installed in $BIN_DIR"
    else
        warn "Could not install Deno."
        warn "  Not fatal - downloads still work, but YouTube may offer fewer formats."
    fi
fi

# ---------------------------------------------------------------------------
# 7. Instalar as dependências via uv (o passo lento - downloads grandes)
# ---------------------------------------------------------------------------
# TORCH FIXO NA SÉRIE QUE O WHISPERX EXIGE (torch~=2.8.0), instalado PRIMEIRO
# e do índice certo. História completa no setup-sidecar.ps1 e no
# requirements.txt: sem o pin, o índice entrega o torch mais novo, o whisperx
# (que exige ~=2.8.0) faz o uv TROCÁ-LO pelo 2.8.0 do PyPI, e a GPU some. No
# Linux o torch do PyPI é CUDA - então numa AMD a troca levaria a um torch
# CUDA inútil, o mesmo estrago com outra cara.
#
# AO ATUALIZAR O WHISPERX: conferir o requires_dist dele e realinhar este pin
# (e o do setup-sidecar.ps1 e do requirements.txt).
TORCH_PIN=("torch~=2.8.0" "torchaudio~=2.8.0" "torchvision~=0.23.0")

step "Installing torch ($TORCH_LABEL) - this may take several minutes"
"$UV" pip install --python "$VENV_PYTHON" "${TORCH_PIN[@]}" --index-url "$TORCH_INDEX" \
    || fail "Failed to install torch from $TORCH_INDEX."

# ---------------------------------------------------------------------------
# --upgrade + constraints: a lição de 05/09/2026 (ver o ps1 para as medições).
#
# Sem --upgrade o `uv pip install` só audita o que já está instalado e o
# usuário fica congelado nas versões do dia da 1ª instalação, PARA SEMPRE.
# Com --upgrade sozinho, o dia em que sair um torch 2.8.1 no PyPI ele troca o
# torch da GPU por aquele. Então congelamos o torch EFETIVAMENTE instalado
# (com a versão local, ex.: 2.8.0+rocm6.4) num arquivo de constraints e
# atualizamos o resto por cima.
#
# Se as versões instaladas não puderem ser lidas, NÃO atualizamos (e ainda
# assim prendemos o torch à série do pin): antes desatualizado que sem GPU.
#
# ESPAÇOS NO CAMINHO: o uv quebra o valor de `-c`/`--constraints` em espaços
# (medido no ps1). Aqui não há nome curto 8.3 para contornar - em vez disso o
# uv roda DE DENTRO da pasta temporária e recebe o nome relativo do arquivo,
# que nunca tem espaço, more a pasta de dados onde morar.
# ---------------------------------------------------------------------------
step "Preparing the update (protecting the installed torch)"

CONSTRAINTS_NAME="uskmaker-keep-torch.txt"
UPGRADE_ARGS=()
# `^torch==` não casa "torchcodec==..." (depois de "torch" vem "c", não
# "=="), então o torchcodec continua livre para atualizar.
torch_frozen="$("$UV" pip freeze --python "$VENV_PYTHON" 2>/dev/null | grep -E '^(torch|torchaudio|torchvision)==' || true)"
if [[ -n "$torch_frozen" ]]; then
    printf '%s\n' "$torch_frozen" > "$TMP_DIR/$CONSTRAINTS_NAME"
    UPGRADE_ARGS=(--upgrade)
    ok "Updating is ON. Frozen: $(printf '%s' "$torch_frozen" | paste -sd ',' - | sed 's/,/, /g')"
else
    printf '%s\n' "${TORCH_PIN[@]}" > "$TMP_DIR/$CONSTRAINTS_NAME"
    warn "Could not read the installed torch version."
    warn "  To be safe, the dependencies will NOT be updated on this run"
    warn "  (updating without that protection could swap the GPU torch for another build)."
fi

# ÍNDICE DO TORCH TAMBÉM NESTE PASSO. O constraints diz "torch==2.8.0+rocm6.4"
# (ou +cu126, +cpu), e essa versão local SÓ existe no índice do PyTorch. Com
# --upgrade o uv precisa achá-la num índice para manter - só com o PyPI ele
# não acha e a resolução falha. O --extra-index-url dá o índice, e o
# --index-strategy unsafe-best-match deixa o uv considerar as versões de
# TODOS os índices (o padrão dele, por segurança, para no primeiro índice que
# tem o pacote - e o índice do PyTorch espelha alguns pacotes do PyPI em
# versões antigas). O constraints impede que essa liberdade troque o torch.
# VERIFICADO (09/2026) com o índice rocm6.4 nesta combinação exata.
#
# O extra do audio-separator vai NO MESMO COMANDO do requirements (lição do
# ps1): num segundo comando ele resolveria sem os tetos do requirements.txt.
step "Installing/updating the pipeline dependencies (audio-separator extra [$SEP_EXTRA])"
(
    cd "$TMP_DIR" && "$UV" pip install --python "$VENV_PYTHON" ${UPGRADE_ARGS[@]+"${UPGRADE_ARGS[@]}"} \
        -c "$CONSTRAINTS_NAME" \
        --extra-index-url "$TORCH_INDEX" --index-strategy unsafe-best-match \
        -r "$REQ_FILE" "audio-separator[$SEP_EXTRA]>=0.44.0"
) || fail "Failed to install the dependencies (requirements.txt + audio-separator[$SEP_EXTRA]). If the error mentions building diffq, check the C compiler."

# ---------------------------------------------------------------------------
# 8. Validação final
# ---------------------------------------------------------------------------
# Regras herdadas do ps1 (caso real de 17/07/2026): cada módulo testado
# SEPARADAMENTE, o veredito vem SÓ do código de saída (um WARNING no stderr
# não reprova um import que funcionou), e na falha mostramos o FIM do
# traceback, onde está a exceção de verdade.
step "Validating the installation"

set +e
torch_out="$("$VENV_PYTHON" -c 'import torch; print("TORCH", torch.__version__, torch.cuda.is_available(), torch.version.hip or torch.version.cuda or "-")' 2>&1)"
torch_exit=$?
set -e
if [[ "$torch_exit" -ne 0 ]]; then
    printf '    %s[FAILED]%s import torch - end of the traceback:\n' "$C_RED" "$C_RESET"
    show_tail "$torch_out"
    fail "torch did not import - the environment is NOT ready. The cause is in the lines above."
fi
read -r _ torch_version gpu_available backend_version <<<"$(printf '%s\n' "$torch_out" | grep '^TORCH ' | tail -n 1)"
if [[ "$GPU_EXPECTED" -eq 1 && "$gpu_available" != "True" ]]; then
    warn "torch $torch_version is installed but does not see the GPU (torch.cuda.is_available() = ${gpu_available:-?})."
    if [[ "$TORCH_CHANNEL" == rocm* ]]; then
        # Suspeitos na ordem em que costumam acontecer numa AMD.
        warn "  Suspects, in this order: (1) your user cannot open /dev/kfd - add it to the"
        warn "  'render' and 'video' groups (sudo usermod -aG render,video \$USER) and log in again;"
        warn "  (2) the GPU is not supported by ROCm (integrated GPUs and older cards often are not;"
        warn "  HSA_OVERRIDE_GFX_VERSION can help); (3) the amdgpu kernel driver."
    else
        warn "  Suspects, in this order: (1) a CPU torch was installed"
        warn "  (run this script again with the GPU visible to nvidia-smi);"
        warn "  (2) the CUDA build does not cover the card's generation; (3) the driver."
    fi
else
    ok "torch $torch_version installed (GPU available: $gpu_available, backend: $backend_version)"
fi

# torchcodec: no Linux ele usa as bibliotecas COMPARTILHADAS do ffmpeg da
# distro (libavcodec.so.N etc.). Só informativo - nada aqui reprova o setup.
if "$VENV_PYTHON" -c 'from torchcodec.decoders import AudioDecoder' >/dev/null 2>&1; then
    ok "torchcodec loads (FFmpeg shared libraries found)"
else
    warn "torchcodec does not load - harmless today, the older decoders still work."
fi

# Módulos ESSENCIAIS: sem qualquer um deles o sidecar morre no import e o app
# não produz nada. Falha aqui = ambiente reprovado. (swift_f0 entrou na lista
# em 31/07/2026 - ver o ps1.)
CORE_MODULES=(whisperx demucs librosa mutagen swift_f0 yt_dlp)
# audio_separator = OPCIONAL: é o resgate da voz principal (2º passe), com
# import lazy dentro de try/except em pipeline/separate.py. Só avisa.
OPTIONAL_MODULES=(audio_separator.separator)
failed_core=()
failed_optional=()
for mod in "${CORE_MODULES[@]}" "${OPTIONAL_MODULES[@]}"; do
    set +e
    import_out="$("$VENV_PYTHON" -c "import $mod" 2>&1)"
    import_exit=$?
    set -e
    if [[ "$import_exit" -eq 0 ]]; then
        ok "import $mod"
    else
        if [[ " ${OPTIONAL_MODULES[*]} " == *" $mod "* ]]; then
            failed_optional+=("$mod")
        else
            failed_core+=("$mod")
        fi
        printf '    %s[FAILED]%s import %s - end of the traceback:\n' "$C_RED" "$C_RESET" "$mod"
        show_tail "$import_out"
    fi
done

if [[ "${#failed_optional[@]}" -gt 0 ]]; then
    warn "An optional module did not import: ${failed_optional[*]}. The app WORKS without it -"
    warn "  the pipeline falls back to the Demucs stem (you lose only the lead-vocal rescue,"
    warn "  which improves separation on some songs). Run this setup again to retry."
fi

if [[ "${#failed_core[@]}" -gt 0 ]]; then
    # FALHA (não aviso): sem essas libs o app NÃO gera nada. Terminar com
    # faixa verde aqui foi exatamente o que confundiu um usuário (16/07/2026).
    fail "These essential libraries did not import: ${failed_core[*]} - the environment is NOT ready.

The cause is in the traceback lines above (the last line is the exception).
Run this setup again. If it persists, open an issue and attach those lines."
fi
ok "The essential pipeline libraries imported successfully."

echo
echo "${C_GREEN}=============================================${C_RESET}"
echo "${C_GREEN} Setup complete! You can now use USKMaker.${C_RESET}"
echo "${C_GREEN}=============================================${C_RESET}"
echo " On the first song, the AI models (Demucs/Whisper)"
echo " will be downloaded automatically (~2 GB, first time only)."
if [[ -n "$FFMPEG_PROBLEM" ]]; then
    echo
    warn "Reminder: $FFMPEG_PROBLEM"
    pkg_hint "ffmpeg-free" "ffmpeg" "ffmpeg" "ffmpeg"
fi
exit 0
