# -*- coding: utf-8 -*-
"""
Testes da lógica PURA do ctc_align.py (tokenização, alvo do CTC, blocos entre
âncoras duras) - nada aqui carrega modelo; o Viterbi é testado com emissões
sintéticas pequenas.

Rodar:  python -m pytest tests/ -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.align import SOURCE_ANCHOR, SOURCE_FUZZY
from pipeline.ctc_align import (
    build_targets,
    fill_missing,
    hard_anchor_runs,
    plan_blocks,
    spans_to_word_times,
    tokenize_words,
)

DICT = {"-": 0, "a": 1, "b": 2, "o": 3, "s": 4, "'": 5, "*": 6, "c": 7, "e": 8,
        "f": 9, "i": 10, "l": 11, "n": 12, "r": 13, "t": 14, "u": 15, "v": 16,
        "w": 17, "y": 18, "g": 19, "h": 20, "d": 21, "m": 22, "k": 23}


def test_tokenize_folds_accents_and_punctuation():
    toks, unmapped = tokenize_words(["Såb,", "“ab”"], DICT, "sv")
    assert toks == [[4, 1, 2], [1, 2]]
    assert unmapped == 0.0


def test_tokenize_never_emits_blank_or_star():
    toks, _ = tokenize_words(["rock-n-roll", "a*b"], DICT, "en")
    assert all(t not in (0, 6) for w in toks for t in w)


def test_tokenize_expands_numerals():
    toks, _ = tokenize_words(["20"], DICT, "en")
    assert toks == [[14, 17, 8, 12, 14, 18]]  # "twenty"


def test_tokenize_reports_other_scripts():
    _, unmapped = tokenize_words(["привет"], DICT, "ru")
    assert unmapped == 1.0


def test_build_targets_star_at_edges_and_line_ends():
    targets, ranges = build_targets([[1], [2, 3], [4]], [False, True, True], star_id=6)
    assert targets == [6, 1, 2, 3, 6, 4, 6]
    assert ranges == [(1, 2), (2, 4), (5, 6)]


def test_build_targets_without_star():
    targets, ranges = build_targets([[1], [], [4]], [False, False, True], star_id=None)
    assert targets == [1, 4]
    assert ranges == [(0, 1), (1, 1), (1, 2)]


def test_spans_to_word_times_and_fill_missing():
    spans = [(10, 12, 0.5), (20, 21, 1.0)]
    times = spans_to_word_times(spans, [(0, 1), (1, 1), (1, 2)], frame_s=0.02)
    assert times[0] == pytest.approx((0.2, 0.24, 0.5))
    assert times[1] is None
    filled = fill_missing(times)
    assert filled[1][3] is False
    assert filled[0][1] <= filled[1][0] <= filled[2][0]


def _a(start, src=SOURCE_ANCHOR):
    return (start, start + 0.3, 0.9, src)


def test_hard_runs_need_min_length_and_exact_source():
    anchors = [_a(1), _a(2), None, _a(3), _a(4), _a(5), _a(6, SOURCE_FUZZY)]
    assert hard_anchor_runs(anchors) == [(3, 6)]


def test_hard_runs_reject_repeated_short_phrases():
    words = "never cared for what they do never cared for what they know".split()
    anchors = [None] * len(words)
    for k in (3, 4, 5):  # "what they do" occurs once -> kept
        anchors[k] = _a(float(k))
    assert hard_anchor_runs(anchors, words) == [(3, 6)]
    anchors = [None] * len(words)
    for k in (0, 1, 2):  # "never cared for" occurs twice -> ambiguous
        anchors[k] = _a(float(k))
    assert hard_anchor_runs(anchors, words) == []


def test_hard_runs_drop_runs_going_back_in_time():
    anchors = [_a(10), _a(11), _a(12), None, _a(1), _a(2), _a(3)]
    assert hard_anchor_runs(anchors) == [(0, 3)]


def test_plan_blocks_cover_all_words_in_order():
    anchors = [None, _a(10), _a(11), _a(12), None, None]
    blocks = plan_blocks(6, [(1, 4)], anchors, audio_dur=100.0)
    assert [(b[0], b[1]) for b in blocks] == [(0, 1), (1, 4), (4, 6)]
    assert blocks[0][2:] == (0.0, 10.0)
    assert blocks[1][2] == pytest.approx(9.75)
    assert blocks[2][2:] == (pytest.approx(12.3), 100.0)


def test_forced_align_synthetic():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchaudio")
    from pipeline.ctc_align import align_blocks

    # 3 tokens (blank=0, a=1, b=2): "a" nos quadros 3-4, "b" nos 8-9
    T = 12
    em = torch.full((T, 3), -10.0)
    em[:, 0] = -0.01
    for f in (3, 4):
        em[f] = torch.tensor([-10.0, -0.01, -10.0])
    for f in (8, 9):
        em[f] = torch.tensor([-10.0, -10.0, -0.01])
    times = align_blocks(em, 0.1, [(0, 2, 0.0, 1.2)], [[1], [2]], [False, True],
                         star_id=None, blank=0)
    assert times[0][0] == pytest.approx(0.3)
    assert times[1][0] == pytest.approx(0.8)


def test_snap_line_starts_only_moves_line_first_words_within_reach():
    import numpy as np
    from pipeline.ctc_align import snap_line_starts

    starts = [1.00, 1.50, 3.00, 6.00]
    ends = [1.40, 2.00, 3.50, 6.50]
    line_ends = [False, True, False, True]
    onsets = np.array([0.90, 1.45, 3.12, 5.50])
    moved = snap_line_starts(starts, ends, line_ends, onsets)
    assert moved == 2
    assert starts == [0.90, 1.50, 3.12, 6.00]  # 1.45 is mid-line; 5.50 too far
