# -*- coding: utf-8 -*-
"""
Threshold de confiança do PitchExtractor calibrado para a swift-f0 0.2.x.

Regressão real (v0.21.2): a migração para a 0.2.0 manteve o 0.85 da 0.1.x,
mas a 0.2.0 dá confiança bem mais baixa em canto real - ~60% das notas saíam
freestyle ("F", não pontua). A medição está no topo de pipeline/pitch.py.

Estes testes travam as duas propriedades que importam, sem carregar o modelo
(lê só o default da assinatura):
  1. o default está no platô medido para a 0.2 (bem abaixo de 0.85);
  2. o default fica >= 0.5, o corte de confiança média do build_song.py - se
     ficasse abaixo, notas COM quadros vozeados poderiam virar "F" mesmo assim.

Rodar:  python -m pytest tests/ -v   (ou python tests/test_pitch_threshold.py)
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.pitch import PitchExtractor


def _default_threshold() -> float:
    return inspect.signature(PitchExtractor.__init__).parameters["confidence_threshold"].default


def test_threshold_calibrado_para_swift_f0_0_2():
    t = _default_threshold()
    # platô medido: F1 ~0.82 entre 0.40 e 0.55; 0.85 (valor da 0.1.x) cai para 0.68
    assert 0.40 <= t <= 0.60, t


def test_threshold_nao_fica_abaixo_do_corte_de_freestyle():
    # build_song.py: note_type = ":" if confiança média dos quadros vozeados >= 0.5
    assert _default_threshold() >= 0.5


if __name__ == "__main__":
    test_threshold_calibrado_para_swift_f0_0_2()
    test_threshold_nao_fica_abaixo_do_corte_de_freestyle()
    print("ok")
