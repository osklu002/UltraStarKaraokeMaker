"""
Testes puros do nome de arquivo seguro (pipeline/filenames.py).

Os três casos de "bug real" abaixo foram reproduzidos de verdade em
16/07/2026 antes da correção - não são hipotéticos.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.filenames import sanitize_filename  # noqa: E402


# --- os três bugs reais -----------------------------------------------------

def test_barra_nao_vira_separador_de_caminho():
    # antes: "AC/DC - T.N.T..ogg" escapava pra uma subpasta "AC\" e o pacote
    # saía sem áudio, sem erro nenhum
    out = sanitize_filename("AC/DC - T.N.T.")
    assert "/" not in out and "\\" not in out
    assert out.startswith("AC-DC"), "a comunidade (usdb_syncer) usa hífen aqui"


def test_interrogacao_nao_quebra_a_escrita():
    # antes: OSError [Errno 22] Invalid argument
    assert sanitize_filename("Rita Lee - Quem?") == "Rita Lee - Quem"


def test_dois_pontos_nao_vira_alternate_data_stream():
    # antes: no NTFS o ":" abria um ADS - arquivo visível com 0 byte e o
    # áudio num stream oculto, SEM erro. O pior dos três.
    out = sanitize_filename("Blur - Song 2: Live")
    assert ":" not in out
    assert out == "Blur - Song 2 Live"


# --- o resto da tabela (mesma do usdb_syncer) -------------------------------

def test_aspas_somem():
    assert sanitize_filename('Nirvana - "Polly"') == "Nirvana - Polly"


def test_maior_menor_viram_parenteses():
    assert sanitize_filename("A <b> C") == "A (b) C"


def test_pipe_e_asterisco_viram_hifen():
    assert sanitize_filename("A|B*C") == "A-B-C"


def test_ponto_final_e_removido():
    # o Windows não aceita ponto no fim; o Explorer o come calado
    assert sanitize_filename("AC-DC - T.N.T.") == "AC-DC - T.N.T"
    assert not sanitize_filename("qualquer coisa.").endswith(".")


def test_espaco_no_fim_removido():
    assert sanitize_filename("Artista - Titulo  ") == "Artista - Titulo"


def test_caractere_de_controle_vira_underscore():
    assert "\n" not in sanitize_filename("Artista -\nTitulo")


def test_nome_normal_passa_intacto():
    # regressão: o caminho comum não pode mudar
    assert sanitize_filename("Rita Lee - Sangue Latino") == "Rita Lee - Sangue Latino"


# Espelho de ACENTOS_PARA_ASCII no main.rs (sanitize_tests): mesmas entradas,
# mesmas saídas - o Rust cria a pasta, o Python os arquivos dentro.
ACENTOS_PARA_ASCII = [
    ("Oskar Linnros - Från och med Du", "Oskar Linnros - Fran och med Du"),
    ("Djavan - Açaí", "Djavan - Acai"),
    ("Björk - Jóga", "Bjork - Joga"),
    ("Die Ärzte - Männer sind Schweine", "Die Arzte - Manner sind Schweine"),
    ("Sigur Rós - Ágætis byrjun", "Sigur Ros - Agaetis byrjun"),
    ("MØ - Lean On", "MO - Lean On"),
    ("Mø - Final Song", "Mo - Final Song"),
    ("Æ - Straße ẞ", "AE - Strasse SS"),
    ("Don’t Stop ‘Me’", "Don't Stop 'Me'"),
    # forma de compatibilidade: NFKD antes da tabela, a barra larga vira hífen
    ("ＡＣ／ＤＣ", "AC-DC"),
]


@pytest.mark.parametrize("raw,expected", ACENTOS_PARA_ASCII)
def test_acentos_viram_ascii(raw, expected):
    # o UltraStar Deluxe (Flatpak 2026.8.1) ignora calado pasta/arquivo com
    # letra fora do ASCII
    out = sanitize_filename(raw)
    assert out == expected
    assert out.isascii()


def test_titulo_so_de_pontuacao_nao_vira_vazio():
    # um nome vazio viraria a própria pasta / um caminho estranho
    assert sanitize_filename("???") == "musica"
