# -*- coding: utf-8 -*-
"""
Testes da tabela de mensagens do log (pipeline/i18n.py) - lógica pura, sem
GPU, sem rede, sem modelos.

CONTEXTO (29/09/2026): a interface do app é bilíngue, mas o log da pipeline
era só português. Agora cada mensagem existe em pt e en. O que estes testes
guardam:
  - t() no idioma pedido, com os {campos} formatados;
  - fallback que NUNCA derruba a pipeline (idioma desconhecido, chave que não
    existe, campo faltando);
  - a tabela em si: toda chave com pt E en, o mesmo conjunto de {campos} nos
    dois, e a marcação do rich idêntica (só a palavra muda, não a cor);
  - a régua de etapa no formato que a interface lê (regex do src/App.tsx).

Rodar:  python -m pytest tests/ -v     ou     python tests/test_i18n_logic.py
"""
import re
import string
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import i18n
from pipeline.i18n import MESSAGES, get_ui_lang, normalize_lang, set_ui_lang, t


@pytest.fixture(autouse=True)
def _idioma_limpo():
    """
    O idioma é estado GLOBAL do processo. Sem restaurar, um teste que liga o
    inglês vazaria para os outros arquivos de teste (ex.: os de download, que
    conferem as frases em português).
    """
    set_ui_lang("pt")
    yield
    set_ui_lang("pt")


# ---------------------------------------------------------------------------
# t() e o idioma corrente
# ---------------------------------------------------------------------------

def test_default_e_portugues():
    """Sem ninguém pedir nada, o log é o de sempre (CLI e app antigo)."""
    assert i18n.DEFAULT_LANG == "pt"
    assert get_ui_lang() == "pt"
    assert t("main.step3") == "[bold cyan]Etapa 3/6 — Detectando BPM"


def test_ingles_quando_pedido():
    set_ui_lang("en")
    assert t("main.step3") == "[bold cyan]Step 3/6 — Detecting BPM"
    assert t("main.words_done", n=42) == "[green]OK[/green] 42 words processed."


def test_campos_formatados_com_especificador():
    set_ui_lang("pt")
    assert t("main.bpm_ok", bpm=123.456).startswith("[green]OK[/green] BPM: 123.46 ")
    set_ui_lang("en")
    assert t("main.audio_tag_tone", n=2) == "(KEY +2) "
    assert t("main.audio_tag_tone", n=-3) == "(KEY -3) "


@pytest.mark.parametrize("entrada,esperado", [
    ("pt", "pt"), ("en", "en"), ("EN", "en"), ("pt-BR", "pt"), ("en_US", "en"),
    ("es", "pt"), ("", "pt"), (None, "pt"), ("  en  ", "en"),
])
def test_normaliza_idioma(entrada, esperado):
    assert normalize_lang(entrada) == esperado
    assert set_ui_lang(entrada) == esperado
    assert get_ui_lang() == esperado


def test_idioma_desconhecido_cai_no_portugues():
    set_ui_lang("fr")
    assert t("main.step1") == MESSAGES["main.step1"]["pt"]


def test_chave_inexistente_devolve_a_chave_sem_quebrar():
    assert t("nao.existe") == "nao.existe"
    assert t("nao.existe", x=1) == "nao.existe"


def test_chave_sem_o_idioma_cai_no_portugues(monkeypatch):
    monkeypatch.setitem(MESSAGES, "teste.so_pt", {"pt": "só pt {n}"})
    set_ui_lang("en")
    assert t("teste.so_pt", n=1) == "só pt 1"


def test_campo_faltando_nao_derruba():
    """Uma mensagem de log com campo esquecido não pode matar a geração."""
    set_ui_lang("en")
    out = t("main.words_done")          # falta n=
    assert isinstance(out, str) and out


# ---------------------------------------------------------------------------
# A tabela
# ---------------------------------------------------------------------------

def _campos(template: str) -> set[str]:
    return {nome for _, nome, _, _ in string.Formatter().parse(template) if nome}


# Tags de ESTILO do rich ([green], [/yellow], [bold red]...). Colchetes com
# outra coisa dentro ([AVISO], [metadata], [VOC]) são texto, não marcação.
_STYLE_WORDS = r"(?:bold|dim|italic|underline|green|yellow|red|cyan|blue|magenta|white)"
_RICH_TAG = re.compile(rf"\[/?{_STYLE_WORDS}(?:\s+{_STYLE_WORDS})*\]|\[/\]")


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_toda_chave_tem_pt_e_en(key):
    entry = MESSAGES[key]
    assert set(entry) == {"pt", "en"}, key
    assert entry["pt"].strip() and entry["en"].strip(), key


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_mesmos_campos_nos_dois_idiomas(key):
    entry = MESSAGES[key]
    assert _campos(entry["pt"]) == _campos(entry["en"]), key


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_marcacao_rich_identica_nos_dois_idiomas(key):
    """Só a palavra muda: [yellow]AVISO[/yellow] vira [yellow]WARNING[/yellow]."""
    entry = MESSAGES[key]
    assert _RICH_TAG.findall(entry["pt"]) == _RICH_TAG.findall(entry["en"]), key


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_formata_nos_dois_idiomas_com_valores_de_exemplo(key):
    """Todo molde formata sem erro (pega '{' solto e especificador inválido)."""
    for lang in ("pt", "en"):
        template = MESSAGES[key][lang]
        valores = {}
        for _, nome, spec, _ in string.Formatter().parse(template):
            if nome:
                valores[nome] = 1 if spec and spec[-1] in "dfe" else "x"
        template.format(**valores)


def test_prefixos_simples_sao_iguais_ou_traduzidos_de_forma_consistente():
    """
    Prefixos de texto puro ([AVISO]/[WARNING], [OK], [INFO]...) - o en nunca
    pode sair com a palavra portuguesa, e [OK]/[INFO]/[BPM]/[DIAG] ficam iguais.
    """
    mapa = {"[AVISO]": "[WARNING]", "[ATENÇÃO]": "[ATTENTION]"}
    for key, entry in MESSAGES.items():
        pt, en = entry["pt"], entry["en"]
        for pt_word, en_word in mapa.items():
            if pt.startswith(pt_word):
                assert en.startswith(en_word), key
        for same in ("[OK]", "[INFO]", "[BPM]", "[DIAG]", "[metadata]", "[download]"):
            if pt.startswith(same):
                assert en.startswith(same), key
        assert "AVISO" not in en and "ATENÇÃO" not in en, key


# ---------------------------------------------------------------------------
# Contrato com a interface: a régua de etapa
# ---------------------------------------------------------------------------

# Mesma regex do listener "pipeline-log" em src/App.tsx.
_APP_STEP_RE = re.compile(r"(?:Etapa|Step)\s+(\d+)/(\d+)")


@pytest.mark.parametrize("lang", ["pt", "en"])
def test_reguas_de_etapa_batem_com_a_regex_da_interface(lang):
    set_ui_lang(lang)
    for n in range(1, 7):
        m = _APP_STEP_RE.search(t(f"main.step{n}"))
        assert m, (lang, n)
        assert (int(m.group(1)), int(m.group(2))) == (n, 6)


def test_portugues_da_regua_continua_byte_a_byte():
    """App antigo (regex só com 'Etapa') + sidecar novo sem ui_lang = pt."""
    for n in range(1, 7):
        assert re.search(r"Etapa\s+(\d+)\/(\d+)", MESSAGES[f"main.step{n}"]["pt"])


# ---------------------------------------------------------------------------
# Onde o idioma chega de verdade: erro de download que vai para a tela
# ---------------------------------------------------------------------------

def test_erro_de_download_sai_no_idioma_da_interface():
    from pipeline.download import _friendly_download_error
    set_ui_lang("en")
    assert "refused" in _friendly_download_error("HTTP Error 403: Forbidden").lower()
    set_ui_lang("pt")
    assert "recusou" in _friendly_download_error("HTTP Error 403: Forbidden").lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
