# -*- coding: utf-8 -*-
"""
pitch._read_window: lê do disco SÓ o trecho da palavra, em vez do stem de
vocal inteiro a cada palavra. O trecho tem que sair IDÊNTICO ao fatiamento
antigo (`y[int(start*sr):int(end*sr)]` sobre o arquivo inteiro), inclusive
nas bordas: janela passando do fim, começando depois do fim, mono e estéreo.

Rodar:  python -m pytest tests/ -v   (ou python tests/test_pitch_read_window.py)
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.pitch import _read_window


def _check_file(channels: int, sr: int) -> None:
    rng = np.random.default_rng(3)
    dur = 5
    shape = (sr * dur,) if channels == 1 else (sr * dur, channels)
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "stem.wav")
        sf.write(path, rng.standard_normal(shape) * 0.1, sr)
        full, _ = sf.read(path)
        windows = [(0.0, 0.3), (1.2345, 1.9), (4.8, 5.5), (5.0, 5.2), (6.0, 7.0), (2.0, 2.0)]
        windows += [(float(a), float(a) + float(b)) for a, b in zip(rng.uniform(0, 4.9, 50), rng.uniform(0.01, 1.0, 50))]
        for a, b in windows:
            old = full[int(a * sr):int(b * sr)]
            new, got_sr = _read_window(path, a, b)
            assert got_sr == sr
            assert old.shape == new.shape, (a, b, old.shape, new.shape)
            assert np.array_equal(old, new), (a, b)


def test_trecho_identico_ao_fatiamento_antigo_mono():
    _check_file(1, 44100)


def test_trecho_identico_ao_fatiamento_antigo_estereo():
    _check_file(2, 48000)


if __name__ == "__main__":
    test_trecho_identico_ao_fatiamento_antigo_mono()
    test_trecho_identico_ao_fatiamento_antigo_estereo()
    print("ok")
