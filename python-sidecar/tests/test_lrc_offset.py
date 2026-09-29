# -*- coding: utf-8 -*-
"""
Testes do deslocamento CONSTANTE do .lrc (estimate_lrc_offset / shift_lrc_lines).

CONTEXTO (29/09/2026, "Mauro Scocco - Till dom ensamma"): o .lrc do LRCLIB
tinha os tempos certos entre si, mas todos 1,3 s adiantados. Com o
reconhecimento baixo (58%), os inícios do .lrc passaram por cima das âncoras
do Whisper - que estavam certas - e 22 das 50 linhas saíram 1,3 s cedo.

O que estes testes trancam:
  - deslocamento claro e consistente -> corrigido (nos dois sentidos);
  - .lrc bom (só o "toque adiantado" normal) ou sem consistência -> intocado;
  - pouca evidência -> intocado;
  - só âncoras EXATAS do Whisper contam (fuzzy/lrc/None ficam de fora).

Rodar:  python -m pytest tests/ -v   (ou python tests/test_lrc_offset.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from pipeline.align import (
    LRC_OFFSET_MIN_LINES,
    lrc_offset_confirmed_by_onsets,
    vocal_onsets,
    SOURCE_ANCHOR,
    SOURCE_FUZZY,
    SOURCE_LRC,
    estimate_lrc_offset,
    shift_lrc_lines,
)


def _anchor(t, source=SOURCE_ANCHOR):
    return (t, t + 0.3, 0.9, source)


def _song(n_lines, lrc_start=10.0, step=5.0):
    """n linhas de 2 palavras cada; o .lrc marca o início de cada uma."""
    lyric_lines = [(f"linha numero {i}", 2 * i) for i in range(n_lines)]
    lrc_lines = [(lrc_start + step * i, f"linha numero {i}") for i in range(n_lines)]
    return lyric_lines, lrc_lines


def _anchors_at(lrc_lines, deltas, source=SOURCE_ANCHOR):
    """Âncora da 1ª palavra de cada linha = tempo do .lrc + delta (2ª palavra sem âncora)."""
    anchors = []
    for (t, _), d in zip(lrc_lines, deltas):
        anchors += [_anchor(t + d, source) if d is not None else None, None]
    return anchors


def test_lrc_adiantado_constante_e_corrigido():
    """O caso real: todas as linhas ~1,3 s depois do que o .lrc diz."""
    lyric, lrc = _song(10)
    deltas = [1.30, 1.25, 1.35, 1.28, 1.40, 1.22, 1.31, 1.29, 1.33, 1.27]
    off = estimate_lrc_offset(_anchors_at(lrc, deltas), lyric, lrc)
    assert off is not None and abs(off - 1.30) < 0.05


# Diferenças REAIS (âncora - .lrc) do caso "Till dom ensamma", 41 linhas: 26
# agrupadas em +1,1..+1,6 s e 15 casamentos errados do refrão repetido.
REAL_DIFFS = [-16.30, -14.44, -13.96, -10.74, -8.47, -7.41, -6.26, -4.78, -4.10, -2.59, -2.51,
              0.14, 0.75, 1.12, 1.13, 1.17, 1.18, 1.19, 1.20, 1.22, 1.22, 1.26, 1.27, 1.30, 1.30,
              1.31, 1.31, 1.32, 1.32, 1.33, 1.34, 1.40, 1.42, 1.47, 1.50, 1.52, 1.59, 1.62, 1.77,
              12.69, 27.55]


def test_caso_real_com_refrao_casado_errado_e_corrigido():
    """Os casamentos errados do refrão não podem vetar o agrupamento claro."""
    lyric, lrc = _song(len(REAL_DIFFS))
    off = estimate_lrc_offset(_anchors_at(lrc, REAL_DIFFS), lyric, lrc)
    assert off is not None and 1.2 <= off <= 1.35, off


def test_caso_real_com_mais_ruido_ainda_e_corrigido():
    """Outra separação do Demucs: menos linhas no agrupamento (57%) - antes oscilava."""
    noisy = REAL_DIFFS[:11] + [-1.9, 3.3, 5.2] + REAL_DIFFS[11:]
    lyric, lrc = _song(len(noisy))
    off = estimate_lrc_offset(_anchors_at(lrc, noisy), lyric, lrc)
    assert off is not None and 1.2 <= off <= 1.35, off


def test_lrc_atrasado_tambem_e_corrigido():
    lyric, lrc = _song(8)
    off = estimate_lrc_offset(_anchors_at(lrc, [-0.9] * 8), lyric, lrc)
    assert off is not None and abs(off + 0.9) < 1e-9


def test_lrc_bom_com_toque_adiantado_normal_fica_intocado():
    """Quem sincroniza à mão toca ~0,1-0,3 s antes: isso não é deslocamento."""
    lyric, lrc = _song(10)
    deltas = [0.15, 0.20, 0.10, 0.25, 0.18, 0.12, 0.30, 0.22, 0.17, 0.14]
    assert estimate_lrc_offset(_anchors_at(lrc, deltas), lyric, lrc) is None


def test_diferencas_sem_consistencia_ficam_intocadas():
    """Mediana alta mas cada linha diz uma coisa (âncoras ruins) -> não mexe."""
    lyric, lrc = _song(10)
    deltas = [1.5, -1.0, 2.5, 0.0, 1.8, -2.0, 3.0, 0.8, -0.5, 2.2]
    assert estimate_lrc_offset(_anchors_at(lrc, deltas), lyric, lrc) is None


def test_poucas_linhas_ancoradas_nao_bastam():
    lyric, lrc = _song(10)
    n = LRC_OFFSET_MIN_LINES - 1
    deltas = [1.3] * n + [None] * (10 - n)
    assert estimate_lrc_offset(_anchors_at(lrc, deltas), lyric, lrc) is None


def test_so_ancoras_exatas_contam():
    """Fuzzy e inícios já vindos do .lrc não são evidência independente."""
    lyric, lrc = _song(8)
    assert estimate_lrc_offset(_anchors_at(lrc, [1.3] * 8, SOURCE_FUZZY), lyric, lrc) is None
    assert estimate_lrc_offset(_anchors_at(lrc, [1.3] * 8, SOURCE_LRC), lyric, lrc) is None


def test_introducao_diferente_de_ate_15_s_e_corrigida():
    """Casos reais do benchmark: -10,6 s (Lorde), +6,7 s, -7,9 s - outra edição com outra introdução."""
    lyric, lrc = _song(8, lrc_start=20.0)
    off = estimate_lrc_offset(_anchors_at(lrc, [-10.6] * 8), lyric, lrc)
    assert off is not None and abs(off + 10.6) < 1e-9


def test_deslocamento_grande_demais_e_outra_montagem():
    """Acima do teto (o mesmo limite de duração da escolha do LRCLIB) não é deslocamento."""
    lyric, lrc = _song(8, lrc_start=30.0)
    assert estimate_lrc_offset(_anchors_at(lrc, [20.0] * 8), lyric, lrc) is None


def test_linhas_do_lrc_que_nao_casam_com_a_letra_sao_ignoradas():
    """Só linhas casadas pelo texto entram - metadado/linha extra não atrapalha."""
    lyric, lrc = _song(8)
    lrc_extra = [(0.5, "Letra enviada por fulano")] + lrc
    off = estimate_lrc_offset(_anchors_at(lrc, [1.3] * 8), lyric, lrc_extra)
    assert off is not None and abs(off - 1.3) < 1e-9


def test_shift_desloca_tudo_e_nao_fica_negativo():
    shifted = shift_lrc_lines([(0.5, "a"), (10.0, "b")], -1.0)
    assert shifted == [(0.0, "a"), (9.0, "b")]
    assert shift_lrc_lines([(3.0, "c")], 1.25) == [(4.25, "c")]


# --- confirmação pelo áudio ---------------------------------------------------

def _vocal(segments, dur=30.0, sr=16000):
    """Áudio sintético: silêncio com trechos de "voz" (seno) em [ini, fim)."""
    y = np.zeros(int(dur * sr))
    t = np.arange(len(y)) / sr
    for a, b in segments:
        m = (t >= a) & (t < b)
        y[m] = 0.5 * np.sin(2 * np.pi * 220 * t[m])
    return y


def test_vocal_onsets_acha_o_inicio_de_cada_trecho_apos_pausa():
    on = vocal_onsets(_vocal([(2.0, 4.0), (6.0, 8.0), (8.1, 9.0), (12.5, 15.0)]))
    # 8.1 vem depois de só 0,1 s de pausa: não conta como novo ataque
    assert np.allclose(on, [2.0, 6.0, 12.5], atol=0.02), on


def test_audio_confirma_deslocamento_real():
    """Linhas do .lrc 1,3 s adiantadas em relação aos ataques da voz -> confirma."""
    onsets = np.array([5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0])
    lrc = [(t - 1.3, f"l{i}") for i, t in enumerate(onsets)]
    assert lrc_offset_confirmed_by_onsets(onsets, lrc, 1.3)


def test_audio_rejeita_deslocamento_falso():
    """.lrc já certo: deslocar 1,1 s tira as linhas dos ataques -> rejeita."""
    onsets = np.array([5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0])
    lrc = [(t + 0.05, f"l{i}") for i, t in enumerate(onsets)]
    assert not lrc_offset_confirmed_by_onsets(onsets, lrc, -1.1)


def test_audio_sem_ataques_suficientes_nao_confirma():
    assert not lrc_offset_confirmed_by_onsets(np.array([5.0, 10.0]), [(3.7, "a"), (8.7, "b")], 1.3)


if __name__ == "__main__":
    import inspect
    falhas = 0
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"[OK]   {nome}")
            except AssertionError as e:
                falhas += 1
                print(f"[FAIL] {nome}: {e}")
    print(f"\nFALHAS: {falhas}")
    sys.exit(1 if falhas else 0)
