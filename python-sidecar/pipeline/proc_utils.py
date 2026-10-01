"""
proc_utils.py
Helper compartilhado para rodar subprocessos (Demucs, ffmpeg, yt-dlp) de um
jeito seguro para ser chamado de dentro do Tauri.

HISTÓRICO DE BUGS (06/07/2026) - dois problemas diferentes encontrados na
mesma área de código, em sequência:

1. Primeira suspeita (parcialmente correta, mas não era a causa raiz do
   travamento investigado): subprocessos herdando o mesmo pipe do processo
   pai quando rodado via Tauri. Corrigido usando capture_output=True em vez
   de deixar o Demucs/ffmpeg escrever direto no stdout/stderr herdado.

2. CAUSA RAIZ REAL do travamento (só descoberta depois de adicionar log em
   disco + stdout/stderr com line_buffering=True para conseguir ver o
   traceback completo, que antes se perdia): `OSError: [Errno 22] Invalid
   argument` ao imprimir um bloco de texto MUITO GRANDE de uma vez via
   print(), quando o stdout está conectado a um pipe (não um terminal) no
   Windows. Isso é uma limitação conhecida do Python/Windows para escritas
   únicas muito grandes em pipes. Aconteceu especificamente com um arquivo
   FLAC que tinha uma tag LYRICS enorme embutida nos metadados, que o
   ffmpeg ecoa (duas vezes) no stderr - um bloco de texto grande o
   suficiente para estourar o limite.

CORREÇÃO: em vez de imprimir stdout/stderr como um bloco único, imprime
linha por linha - cada escrita individual fica pequena o suficiente para
nunca esbarrar nesse limite do Windows.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def ffmpeg_exe() -> str:
    """
    Caminho do ffmpeg a usar. Prefere o ffmpeg EMBUTIDO do USKMaker (env var
    USKMAKER_FFMPEG, apontando para o ffmpeg.exe em
    %LOCALAPPDATA%\\USKMaker\\bin, obtido pelo setup), e cai para "ffmpeg" do
    PATH quando a variável não está definida. Isso remove a exigência de ter
    o ffmpeg no PATH do sistema, mantendo compatibilidade com instalações
    antigas que dependiam dele.
    """
    return os.environ.get("USKMAKER_FFMPEG") or "ffmpeg"


# Handle devolvido pelo os.add_dll_directory. PRECISA continuar vivo: quando
# esse objeto e coletado pelo garbage collector, o Windows TIRA a pasta da
# busca de DLLs de novo. Por isso ele mora aqui, no modulo, e nao numa
# variavel local que morreria no fim da funcao.
_dll_dir_handle = None


def _expose_ffmpeg_dlls(ff_dir: str) -> None:
    """
    Deixa as DLLs do ffmpeg embutido visiveis para o carregador de DLLs do
    Windows.

    POR QUE O PATH NAO BASTA: desde o Python 3.8 o Windows NAO procura mais no
    PATH as dependencias de uma DLL. O torchcodec (vem junto do torch 2.8, e e
    o que o torchaudio/pyannote usam para decodificar audio) precisa das libs
    COMPARTILHADAS do ffmpeg - avcodec, avfilter, avformat, avutil, postproc,
    swresample, swscale. E o torchcodec 0.7.0 nao faz nada para acha-las: a
    busca por "ffmpeg" no PATH so apareceu na versao 0.10.0. Resultado: as libs
    podiam estar na MESMA pasta do ffmpeg.exe e ele falhava do mesmo jeito, com
    "Could not find module ... (or one of its dependencies)" - mensagem que nao
    diz qual dependencia faltou, o que custou horas de diagnostico.

    MEDIDO (2026-09-05, maquina real com RTX 5080): com as 7 libs do ffmpeg
    7.1.1 ja na pasta bin e apenas o PATH ajustado, o torchcodec falhava nas
    quatro versoes que tenta (7, 6, 5, 4); acrescentando esta pasta com
    os.add_dll_directory, ele carregou na primeira tentativa.

    Inofensivo quando as libs nao estao la - so registra a pasta.
    """
    global _dll_dir_handle
    if _dll_dir_handle is not None:
        return
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    try:
        _dll_dir_handle = os.add_dll_directory(ff_dir)
    except OSError:
        # Pasta inexistente ou sem permissao. O app funciona sem isto - o
        # torchcodec so continua no estado em que ja estava.
        pass


def data_dir(env=None, windows: bool | None = None) -> Path | None:
    """Pasta de dados do USKMaker - espelho do platform::data_dir do Rust.

    Windows: %LOCALAPPDATA%\\USKMaker. Linux/macOS: $XDG_DATA_HOME/USKMaker
    (só caminho absoluto, como manda a especificação XDG) ou
    ~/.local/share/USKMaker. `env`/`windows` existem só para os testes.
    """
    env = os.environ if env is None else env
    windows = (os.name == "nt") if windows is None else windows
    if windows:
        base = env.get("LOCALAPPDATA")
        return Path(base) / "USKMaker" if base else None
    xdg = env.get("XDG_DATA_HOME")
    if xdg and xdg.startswith("/"):  # XDG é POSIX: absoluto = começa com /
        return Path(xdg) / "USKMaker"
    home = env.get("HOME")
    return Path(home) / ".local" / "share" / "USKMaker" if home else None


def _prepend_path(folder: str) -> None:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if folder and folder not in parts:
        os.environ["PATH"] = os.pathsep.join([folder, *parts])


def ensure_ffmpeg_on_path() -> None:
    """
    Coloca a PASTA do ffmpeg embutido no PATH do processo.

    Nossas chamadas usam ffmpeg_exe() (caminho absoluto), e o yt-dlp recebe
    --ffmpeg-location - mas algumas bibliotecas chamam "ffmpeg"/"ffprobe" CRU
    por subprocess, sem passar por nós: o `whisperx.load_audio` (Etapa 4) e o
    `pyannote` (VAD). Quem não tem ffmpeg no PATH do sistema - a maioria, já que
    o ponto do ffmpeg embutido é justamente não exigir isso - quebrava ali com
    `FileNotFoundError: [WinError 2]`, MESMO com o ffmpeg embutido presente e
    tudo antes (download, separação) funcionando.

    Também põe no PATH a pasta <pasta de dados>/bin quando ela existe, COM ou
    SEM ffmpeg embutido: é onde o setup instala o Deno, o runtime JavaScript
    que o yt-dlp procura no PATH para o YouTube. Antes isso só acontecia de
    carona no ffmpeg embutido - e no Linux o ffmpeg vem da distro, então o
    Deno instalado pelo setup-sidecar.sh nunca era achado (limitação anotada
    no próprio script). Sem runtime JS o YouTube recusa mais pedidos.

    Idempotente. Sem USKMAKER_FFMPEG e sem a pasta bin, não faz nada.
    """
    base = data_dir()
    if base is not None and (base / "bin").is_dir():
        _prepend_path(str(base / "bin"))
    ff = os.environ.get("USKMAKER_FFMPEG")
    if not ff:
        return
    ff_dir = os.path.dirname(ff)
    if not ff_dir:
        return
    _prepend_path(ff_dir)
    _expose_ffmpeg_dlls(ff_dir)


# Navegadores de onde o yt-dlp pode ler os cookies do YouTube (opção
# --cookies-from-browser). O valor vem da interface (configuração "cookies do
# YouTube"), POR CHAMADA, na env USKMAKER_YT_COOKIES_BROWSER.
YT_COOKIE_BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge")


def yt_cookies_browser() -> str | None:
    """
    Navegador escolhido para os cookies do YouTube, ou None (desligado).

    POR QUE (01/10/2026): o YouTube passou a exigir sessão logada para alguns
    vídeos ("Sign in to confirm you're not a bot") - nenhum runtime JS resolve
    isso, só cookies de um navegador em que o usuário está logado. Valor fora
    da lista vale como desligado, para uma configuração corrompida nunca
    virar um argumento estranho na linha de comando do yt-dlp.
    """
    value = (os.environ.get("USKMAKER_YT_COOKIES_BROWSER") or "").strip().lower()
    return value if value in YT_COOKIE_BROWSERS else None


def _print_captured(text: str) -> None:
    """
    Imprime um texto capturado de um subprocesso LINHA POR LINHA, nunca
    como um bloco único - ver nota do módulo sobre o OSError [Errno 22]
    que acontece no Windows ao escrever blocos grandes de uma vez num
    stdout conectado a um pipe (não um terminal).
    """
    if not text:
        return
    for line in text.splitlines():
        print(line)


def run_subprocess(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """
    Substituto seguro para `subprocess.run(cmd, check=True)` quando este
    código pode ser invocado de dentro de um processo pai que já está com
    seu próprio stdout/stderr conectado a um pipe (como o Tauri faz).

    Captura a saída do subprocesso e a imprime linha por linha via print()
    normal (que passa pelo stdout do processo Python principal, não por um
    canal compartilhado com o processo pai), evitando tanto o cenário de
    dois processos escrevendo no mesmo pipe do Windows simultaneamente
    quanto o OSError de escrita única grande demais (ver notas do módulo).

    text=True SEM encoding explícito usa o codec de locale (cp1252 no
    Windows), que estoura UnicodeDecodeError quando a saída do subprocesso
    tem bytes fora do cp1252 - ex.: yt-dlp/ffmpeg ecoando um título de vídeo
    ou uma tag de metadados com emoji/CJK. Fixar utf-8 + errors="replace"
    garante que a decodificação da saída nunca derrube o pipeline. Usamos
    setdefault para um eventual chamador ainda poder sobrescrever.
    """
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)

    _print_captured(result.stdout)
    _print_captured(result.stderr)

    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=result.stderr
        )

    return result
