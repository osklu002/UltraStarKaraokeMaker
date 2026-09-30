"""
ctc_align.py
Alinhamento GLOBAL letra<->áudio por CTC (experimental, 30/09/2026).

O align.py parte da transcrição LIVRE do Whisper e só usa a letra real nos
vãos que ele não reconheceu. Aqui é o contrário: a letra inteira é alinhada
de uma vez contra as emissões de um modelo acústico calculadas UMA vez sobre
a música toda (Viterbi de forced alignment, torchaudio.functional.forced_align).
Não existe "palavra que o Whisper não ouviu": toda palavra da letra recebe um
lugar no áudio, e a ordem da letra é garantida pela própria busca.

Modelo: MMS_FA do torchaudio (wav2vec2 multilíngue treinado para forced
alignment em ~1100 idiomas, vocabulário latino a-z + apóstrofo). Idiomas com
outro alfabeto precisariam de romanização (uroman) - fora do escopo por ora:
quando a letra não cabe no vocabulário, `align_lyrics_ctc` devolve None e o
chamador fica com o caminho de sempre.

Token "*" (star): o MMS_FA traz um token coringa que absorve áudio que não
é letra (ad-lib, coro, respiração). Opcionalmente é inserido entre as linhas
da letra, para o que se canta entre uma linha e outra não ter que ser
"engolido" pelas palavras vizinhas.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import numpy as np

from .numerals import expand_numeral

SOURCE_CTC = "ctc"  # forced alignment global (medido)

SAMPLE_RATE = 16000

# Letras que o NFKD não decompõe em base + acento, mas que têm leitura latina
# óbvia pro vocabulário a-z.
_SPECIAL_FOLDS = {"ß": "ss", "æ": "ae", "ø": "o", "œ": "oe", "đ": "d", "ł": "l",
                  "þ": "th", "ð": "d", "ı": "i"}

# Custo (log-prob por quadro) do token coringa "*". O torchaudio usa 0
# (probabilidade 1: o coringa é "de graça", melhor que qualquer letra ou o
# blank), e isso deixa o Viterbi espremer uma linha inteira em poucos quadros
# e cobrir o resto com "*" quando a evidência acústica da linha é fraca.
STAR_LOGP = 0.0

# fração máxima de letras (da letra inteira) fora do vocabulário antes de
# desistir - acima disso não é "um símbolo perdido", é outro alfabeto
MAX_UNMAPPED_FRAC = 0.05


def _word_chars(word: str, language: str) -> str:
    """Palavra -> só os caracteres que o modelo conhece (minúsculas, sem acento,
    números por extenso). Pode devolver "" (palavra só de pontuação)."""
    w = word.lower().replace("’", "'").replace("`", "'")
    digits = re.sub(r"[^\w']", "", w)
    if any(c.isdigit() for c in digits):
        w = " ".join(expand_numeral(digits, language))
    w = "".join(_SPECIAL_FOLDS.get(c, c) for c in w)
    w = "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c))
    return w


_RESERVED = frozenset("-*")


def tokenize_words(
    words: list[str], dictionary: dict[str, int], language: str
) -> tuple[list[list[int]], float]:
    """
    Converte cada palavra em ids do vocabulário. Devolve (tokens por palavra,
    fração de letras descartadas por estarem fora do vocabulário).
    """
    per_word: list[list[int]] = []
    letters = unmapped = 0
    for word in words:
        ids = []
        for c in _word_chars(word, language):
            # "-" é o blank e "*" o coringa no vocabulário do MMS_FA: o hífen
            # de "rock-n-roll" não pode virar o token blank
            if c in dictionary and c not in _RESERVED:
                ids.append(dictionary[c])
                letters += 1
            elif c.isalpha():
                unmapped += 1
                letters += 1
        per_word.append(ids)
    frac = unmapped / letters if letters else 1.0
    return per_word, frac


def build_targets(
    word_tokens: list[list[int]],
    line_ends: list[bool],
    star_id: int | None,
) -> tuple[list[int], list[tuple[int, int]]]:
    """
    Sequência-alvo do CTC + faixa [ini, fim) de cada palavra dentro dela.
    Com `star_id`, um token coringa entra antes da 1ª palavra, depois de cada
    fim de linha e no fim - ele não pertence a palavra nenhuma. O da ponta é
    o que importa mais: sem ele, a introdução instrumental que vaza no stem
    precisa ser "explicada" por letra, e as primeiras palavras grudam no 0 s
    (medido em "Nothing Else Matters": "So close" em 0,0 s, gold 61 s).
    """
    targets: list[int] = [star_id] if star_id is not None else []
    ranges: list[tuple[int, int]] = []
    for k, ids in enumerate(word_tokens):
        start = len(targets)
        targets.extend(ids)
        ranges.append((start, len(targets)))
        if star_id is not None and line_ends[k]:
            targets.append(star_id)
    if star_id is not None and targets[-1] != star_id:
        targets.append(star_id)
    return targets, ranges


def spans_to_word_times(
    token_spans: list[tuple[int, int, float]],
    ranges: list[tuple[int, int]],
    frame_s: float,
) -> list[tuple[float, float, float] | None]:
    """
    (ini_frame, fim_frame, score) por token alvo -> (start_s, end_s, score) por
    palavra. Palavra sem nenhum token (só pontuação) vira None. Score da
    palavra = média dos scores dos seus tokens ponderada pela duração.
    """
    out: list[tuple[float, float, float] | None] = []
    for a, b in ranges:
        if a == b:
            out.append(None)
            continue
        spans = token_spans[a:b]
        dur = sum(e - s for s, e, _ in spans)
        score = sum((e - s) * sc for s, e, sc in spans) / dur if dur else 0.0
        out.append((spans[0][0] * frame_s, spans[-1][1] * frame_s, float(score)))
    return out


def fill_missing(times: list[tuple[float, float, float] | None]) -> list[tuple[float, float, float, bool]]:
    """Palavras sem token herdam um ponto entre as vizinhas medidas (duração
    zero-ish). Devolve (start, end, score, medido)."""
    out: list[tuple[float, float, float, bool]] = []
    last_end = 0.0
    n = len(times)
    for k, t in enumerate(times):
        if t is not None:
            out.append((t[0], t[1], t[2], True))
            last_end = t[1]
            continue
        nxt = next((times[j][0] for j in range(k + 1, n) if times[j] is not None), last_end)
        s = last_end
        e = max(s, min(nxt, s + 0.05))
        out.append((s, e, 0.0, False))
    return out


# ---------------- partes que precisam de torch/modelo ----------------

_MODEL_CACHE: dict = {}


def _get_mms_model(device: str):
    key = device
    if key not in _MODEL_CACHE:
        from torchaudio.pipelines import MMS_FA
        model = MMS_FA.get_model(with_star=True).to(device).eval()
        _MODEL_CACHE[key] = (model, MMS_FA.get_dict(star="*"))
    return _MODEL_CACHE[key]


def compute_emissions(
    model, audio: np.ndarray, device: str,
    chunk_s: float = 30.0, context_s: float = 2.0,
):
    """
    Log-probabilidades por quadro sobre o áudio INTEIRO, em blocos de
    `chunk_s` com `context_s` de contexto de cada lado (descartado na
    costura) - música de 5 min não cabe de uma vez na atenção do wav2vec2.
    Devolve (emissions [T, C] float32 na CPU, segundos por quadro).
    """
    import torch

    n = len(audio)
    wav = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))
    chunk = int(chunk_s * SAMPLE_RATE)
    ctx = int(context_s * SAMPLE_RATE)
    pieces = []
    stride = None
    with torch.inference_mode():
        for s in range(0, n, chunk):
            e = min(n, s + chunk)
            s0, e0 = max(0, s - ctx), min(n, e + ctx)
            x = wav[s0:e0].unsqueeze(0).to(device)
            em, _ = model(x)
            em = em[0].float().cpu()
            per_frame = (e0 - s0) / em.shape[0]  # amostras por quadro (~320)
            stride = stride or per_frame
            a = int(round((s - s0) / per_frame))
            b = int(round((e - s0) / per_frame))
            pieces.append(em[a:b])
    emissions = torch.cat(pieces, dim=0)
    # O modelo "with_star" do torchaudio devolve log-probs das letras + uma
    # coluna final do "*" fixa em 0 (probabilidade 1). Renormaliza só as
    # letras (sem o "*"), pra coluna do coringa poder receber um custo
    # próprio em align (ver STAR_LOGP).
    emissions[:, :-1] = torch.log_softmax(emissions[:, :-1], dim=-1)
    return emissions, (stride or 320.0) / SAMPLE_RATE


def forced_align_spans(emissions, targets: list[int], blank: int = 0) -> list[tuple[int, int, float]]:
    """Viterbi global; um (ini, fim, score) por token alvo, em quadros."""
    import torch
    import torchaudio.functional as F

    tgt = torch.tensor([targets], dtype=torch.int32)
    labels, scores = F.forced_align(emissions.unsqueeze(0), tgt, blank=blank)
    spans = F.merge_tokens(labels[0], scores[0].exp(), blank=blank)
    return [(sp.start, sp.end, float(sp.score)) for sp in spans]


def _occurrences(seq: list[str], hay: list[str]) -> int:
    n = len(seq)
    return sum(1 for i in range(len(hay) - n + 1) if hay[i:i + n] == seq)


def hard_anchor_runs(
    anchors: list, words: list[str] | None = None,
    min_run: int = 3, unambiguous_run: int = 8,
) -> list[tuple[int, int]]:
    """
    Faixas [i, j) de palavras com âncora EXATA do Whisper em sequência de pelo
    menos `min_run` palavras seguidas, com inícios crescentes, e em ordem
    temporal entre si. Uma âncora exata sozinha pode ser a ocorrência errada
    de uma palavra comum; três seguidas casando letra e transcrição quase
    nunca são - é o que torna a faixa segura como limite duro pro CTC.

    Com `words` (a letra), uma faixa curta só vale se a SEQUÊNCIA de palavras
    dela aparece UMA vez na letra: em linha repetida (refrão), o difflib pode
    ter casado a transcrição com a repetição errada, e a faixa inteira vira um
    limite duro no lugar errado (medido em "Nothing Else Matters": "what they
    do" casou 26 s depois do gold). Faixa de `unambiguous_run`+ palavras é
    aceita mesmo repetida.

    `anchors` é a saída de align.compute_anchors: (start, end, score, source)
    ou None por palavra.
    """
    from .align import SOURCE_ANCHOR, _normalize_word

    norm = [_normalize_word(w) for w in words] if words is not None else None

    runs: list[tuple[int, int]] = []
    n = len(anchors)
    k = 0
    while k < n:
        a = anchors[k]
        if a is None or a[3] != SOURCE_ANCHOR:
            k += 1
            continue
        j = k + 1
        while (j < n and anchors[j] is not None and anchors[j][3] == SOURCE_ANCHOR
               and anchors[j][0] >= anchors[j - 1][0]):
            j += 1
        ambiguous = (norm is not None and j - k < unambiguous_run
                     and _occurrences(norm[k:j], norm) > 1)
        if j - k >= min_run and not ambiguous:
            # descarta faixa que volta no tempo em relação à anterior aceita
            if not runs or anchors[k][0] >= anchors[runs[-1][1] - 1][1] - 0.05:
                runs.append((k, j))
        k = j
    return runs


def plan_blocks(
    n_words: int,
    runs: list[tuple[int, int]],
    anchors: list,
    audio_dur: float,
    pad: float = 0.25,
) -> list[tuple[int, int, float, float]]:
    """
    Parte a letra em blocos (i0, i1, t0, t1) que cobrem todas as palavras em
    ordem: cada faixa de âncoras duras vira um bloco com janela = o trecho que
    o Whisper mediu (+ `pad`), e cada vão entre faixas vira um bloco com
    janela = do fim da faixa anterior ao início da seguinte (ou borda do
    áudio). Cada bloco é alinhado sozinho, então um erro não atravessa uma
    faixa dura.
    """
    blocks: list[tuple[int, int, float, float]] = []
    prev_i, prev_t = 0, 0.0
    for i, j in runs:
        run_t0 = max(0.0, anchors[i][0] - pad)
        run_t1 = min(audio_dur, anchors[j - 1][1] + pad)
        if i > prev_i:
            blocks.append((prev_i, i, prev_t, max(prev_t, anchors[i][0])))
        blocks.append((i, j, run_t0, run_t1))
        prev_i, prev_t = j, anchors[j - 1][1]
    if prev_i < n_words:
        blocks.append((prev_i, n_words, prev_t, audio_dur))
    return blocks


def _align_block(emissions, frame_s, t0, t1, word_tokens, line_ends, star_id, blank):
    """Alinha um bloco de palavras dentro de [t0, t1). None se não cabe."""
    targets, ranges = build_targets(word_tokens, line_ends, star_id)
    if not ranges:
        return []
    f0 = max(0, int(t0 / frame_s))
    f1 = min(emissions.shape[0], int(np.ceil(t1 / frame_s)))
    if not targets:
        return [None] * len(ranges)
    repeats = sum(1 for a, b in zip(targets, targets[1:]) if a == b)
    if f1 - f0 < len(targets) + repeats:
        return None
    spans = forced_align_spans(emissions[f0:f1], targets, blank=blank)
    if len(spans) != len(targets):
        return None
    shifted = [(s + f0, e + f0, sc) for s, e, sc in spans]
    return spans_to_word_times(shifted, ranges, frame_s)


def align_blocks(
    emissions, frame_s: float, blocks: list[tuple[int, int, float, float]],
    word_tokens: list[list[int]], line_ends: list[bool],
    star_id: int | None, blank: int,
) -> list[tuple[float, float, float] | None]:
    """
    Alinha cada bloco na sua janela. Bloco que não cabe na janela (mais
    tokens que quadros) é fundido com o vizinho e tentado de novo - no limite
    vira o alinhamento global de antes.
    """
    blocks = list(blocks)
    results: list[list] = []
    k = 0
    while k < len(blocks):
        i0, i1, t0, t1 = blocks[k]
        res = _align_block(emissions, frame_s, t0, t1, word_tokens[i0:i1],
                           line_ends[i0:i1], star_id, blank)
        if res is not None:
            results.append(res)
            k += 1
            continue
        if k + 1 < len(blocks):
            n0, n1, u0, u1 = blocks[k + 1]
            blocks[k:k + 2] = [(i0, n1, t0, max(t1, u1))]
        elif k > 0:
            p0, p1, u0, u1 = blocks[k - 1]
            blocks[k - 1:k + 1] = [(p0, i1, u0, max(t1, u1))]
            results.pop()
            k -= 1
        else:
            return [None] * (i1 - i0)  # nem o global coube
    return [t for r in results for t in r]


def align_lyrics_ctc(
    audio: np.ndarray,
    words: list[str],
    line_ends: list[bool],
    language: str,
    device: str = "cuda",
    use_star: bool = True,
    anchors: list | None = None,
    emissions=None,
    star_logp: float = STAR_LOGP,
) -> list[tuple[float, float, float, bool]] | None:
    """
    Alinha `words` (a letra inteira, na ordem) ao áudio vocal. Devolve um
    (start_s, end_s, score, medido) por palavra, ou None se a letra não cabe
    no vocabulário do modelo (outro alfabeto).

    Sem `anchors`: um único Viterbi global. Com `anchors` (saída de
    align.compute_anchors sobre a transcrição do Whisper): híbrido - faixas de
    âncoras exatas confiáveis viram limites duros e o CTC alinha bloco a bloco
    (ver plan_blocks). `emissions` = (tensor, frame_s) já calculado, pra
    reusar entre variantes.
    """
    model, dictionary = _get_mms_model(device)
    word_tokens, unmapped = tokenize_words(words, dictionary, language)
    if unmapped > MAX_UNMAPPED_FRAC:
        return None
    star_id = dictionary.get("*") if use_star else None

    em, frame_s = emissions if emissions is not None else compute_emissions(model, audio, device)
    if star_id is not None:
        em = em.clone()
        em[:, star_id] = star_logp
    audio_dur = len(audio) / SAMPLE_RATE
    runs = hard_anchor_runs(anchors, words) if anchors is not None else []
    blocks = plan_blocks(len(words), runs, anchors or [], audio_dur)
    times = align_blocks(em, frame_s, blocks, word_tokens, line_ends, star_id, dictionary["-"])
    if all(t is None for t in times):
        return None
    return fill_missing(times)


def snap_line_starts(
    starts: list[float], ends: list[float], line_ends: list[bool],
    onsets: np.ndarray, max_shift: float = 0.15,
) -> int:
    """
    Puxa o início da 1ª palavra de cada linha para o ataque vocal (voz depois
    de pausa, align.vocal_onsets) mais próximo, se estiver a <= `max_shift`.
    Só a 1ª palavra: dentro da frase cantada em legato não há pausa que marque
    a palavra. Muta `starts` (e `ends` se o início passar do fim). Devolve
    quantas palavras mudaram.
    """
    if len(onsets) == 0:
        return 0
    moved = 0
    for k in range(len(starts)):
        if k > 0 and not line_ends[k - 1]:
            continue
        i = int(np.searchsorted(onsets, starts[k]))
        cands = [onsets[j] for j in (i - 1, i) if 0 <= j < len(onsets)]
        best = min(cands, key=lambda o: abs(o - starts[k]))
        if abs(best - starts[k]) <= max_shift and (k == 0 or best >= ends[k - 1]):
            starts[k] = float(best)
            ends[k] = max(ends[k], starts[k] + 0.02)
            moved += 1
    return moved


def to_word_timings(words, line_ends, singers, res):
    from .align import WordTiming, SOURCE_INTERPOLATED
    return [
        WordTiming(word=w, start=s, end=e, score=sc, is_line_end=le, anchored=m,
                   source=SOURCE_CTC if m else SOURCE_INTERPOLATED, singer=sg)
        for w, (s, e, sc, m), le, sg in zip(words, res, line_ends, singers)
    ]


def align_file(vocals_wav: Path, lyrics_path: Path, language: str, device: str = "cuda",
               use_star: bool = True):
    """Atalho: carrega áudio + letra e devolve list[WordTiming] (ou None)."""
    import whisperx

    from .align import _load_lyrics_words_with_line_ends

    audio = whisperx.load_audio(str(vocals_wav))
    words, line_ends, singers = _load_lyrics_words_with_line_ends(lyrics_path)
    res = align_lyrics_ctc(audio, words, line_ends, language, device, use_star)
    return None if res is None else to_word_timings(words, line_ends, singers, res)
