# -*- coding: utf-8 -*-
"""A/B of alternative aligners against the baseline of a library_replay run.

Reuses a finished replay run dir (same --lib/--n/--seed[/--lang]): its CACHED
Demucs stems and gold charts, so every variant is scored on the exact same
audio as the baseline (Demucs is nondeterministic - see README). Nothing is
re-separated; songs without a cached stem are skipped.

  python eval/ab_align.py --lib ~/Documents/songs --n 8 --seed 0 --variant ctc

Per-variant alignments are cached as cache/<slug>/align_<variant>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[0]))
sys.path.insert(0, str(_HERE))

import library_replay as lr  # noqa: E402
import usdx_parse  # noqa: E402


def _emissions(cache: Path, audio, device: str):
    """MMS_FA emissions over the whole stem, cached (deterministic per stem)."""
    import torch
    from pipeline.ctc_align import _get_mms_model, compute_emissions
    p = cache / "emissions_mms.pt"
    if p.exists():
        d = torch.load(p)
        return d["em"], d["frame_s"]
    model, _ = _get_mms_model(device)
    em, frame_s = compute_emissions(model, audio, device)
    torch.save({"em": em, "frame_s": frame_s}, p)
    return em, frame_s


def _whisper_words(cache: Path, audio, language: str, device: str) -> list[dict]:
    """Raw whisper transcription (+ its own wav2vec2 alignment), cached."""
    p = cache / "whisper_words.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    from pipeline.align import transcribe_words
    ww = transcribe_words(audio, language, device)
    ww = [{k: w.get(k) for k in ("word", "start", "end", "score")} for w in ww
          if w.get("start") is not None]
    p.write_text(json.dumps(ww, ensure_ascii=False), encoding="utf-8")
    return ww


def _run_variant(variant: str, cache: Path, lyrics: Path, language: str, device: str):
    import whisperx
    from pipeline.align import _load_lyrics_words_with_line_ends, compute_anchors
    from pipeline.ctc_align import align_lyrics_ctc, to_word_timings

    # "...+snap" = pós-processo: início de linha puxado pro ataque vocal
    snap = variant.endswith("+snap")
    variant = variant.removesuffix("+snap")
    audio = whisperx.load_audio(str(cache / "vocals.wav"))
    words, line_ends, singers = _load_lyrics_words_with_line_ends(lyrics)
    em = _emissions(cache, audio, device)
    anchors = None
    if variant.startswith("hybrid"):
        anchors = compute_anchors(_whisper_words(cache, audio, language, device), words)
    # "...-starN" = custo do coringa -N (log-prob por quadro), ex. ctc-star2
    m = re.search(r"-star([\d.]+)$", variant)
    star_logp = -float(m.group(1)) if m else 0.0
    res = align_lyrics_ctc(audio, words, line_ends, language, device,
                           use_star=not variant.endswith("nostar"),
                           anchors=anchors, emissions=em, star_logp=star_logp)
    if res is None:
        return None
    if snap:
        from pipeline.align import vocal_onsets
        from pipeline.ctc_align import snap_line_starts
        starts, ends = [r[0] for r in res], [r[1] for r in res]
        snap_line_starts(starts, ends, line_ends, vocal_onsets(audio))
        res = [(s, e, sc, m) for s, e, (_, _, sc, m) in zip(starts, ends, res)]
    return to_word_timings(words, line_ends, singers, res)


def _variant_words(variant, cache: Path, gwords, language, device) -> list[dict] | None:
    out = cache / f"align_{variant}.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    lyrics = cache / "lyrics.txt"
    if not lyrics.exists():
        lyrics.write_text(lr.gold_lyrics_text(gwords), encoding="utf-8")
    timings = _run_variant(variant, cache, lyrics, language, device)
    if timings is None:
        return None
    words = lr._timings_to_dicts(timings)
    out.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    return words


def _fmt(v, w=7):
    return f"{v:>{w}}" if v is not None else f"{'-':>{w}}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lang", default=None)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()
    lr._prefer_safe_ct2_allocator_on_hip()

    runs_root = str(_HERE.parents[0] / "eval_runs")
    manifest = lr.build_manifest(os.path.expanduser(args.lib), runs_root)
    if args.lang:
        manifest = [s for s in manifest if s["lang_group"] == args.lang]
    sample, _ = lr.stratified_sample(manifest, args.n, args.seed)
    suffix = f"-{args.lang}" if args.lang else ""
    run_dir = Path(runs_root) / f"replay-n{args.n}-seed{args.seed}{suffix}"

    rows = []
    for song in sample:
        slug = lr.slugify(song["name"])
        cache = run_dir / "cache" / slug
        base_json = run_dir / "results" / f"{slug}.json"
        if not (cache / "vocals.wav").exists() or not base_json.exists():
            lr.log(f"[skip] {song['name']} (no cached baseline)")
            continue
        base = json.loads(base_json.read_text(encoding="utf-8"))["align"]
        chart = usdx_parse.read_file(song["gold"])
        gwords = lr.gold_words(chart)
        language = lr.normalize_language(chart.language)
        lr.log(f"[run ] {song['name']} ({args.variant})")
        hyp = _variant_words(args.variant, cache, gwords, language, args.device)
        if hyp is None:
            lr.log("       variant declined (vocabulary)")
            continue
        st = lr.timing_stats(gwords, hyp, lr.match_words(gwords, hyp))
        rows.append((song["name"], base, st))

    print(f"\n{'song':<42} {'w1s base':>8} {'w1s new':>8}  {'med base':>8} {'med new':>8}"
          f"  {'p90 base':>8} {'p90 new':>8}  {'end base':>8} {'end new':>8}")
    for name, b, n in rows:
        bo, no = b.get("onset") or {}, n.get("onset") or {}
        be, ne = b.get("end") or {}, n.get("end") or {}
        print(f"{name[:42]:<42} {_fmt(b['within_1s'], 8)} {_fmt(n['within_1s'], 8)}  "
              f"{_fmt(bo.get('median_ms'), 8)} {_fmt(no.get('median_ms'), 8)}  "
              f"{_fmt(bo.get('p90_ms'), 8)} {_fmt(no.get('p90_ms'), 8)}  "
              f"{_fmt(be.get('median_ms'), 8)} {_fmt(ne.get('median_ms'), 8)}")
    if rows:
        def med(get):
            vals = [get(r) for r in rows]
            vals = [v for v in vals if v is not None]
            return round(statistics.median(vals), 3) if vals else None
        print(f"{'MEDIAN':<42} {_fmt(med(lambda r: r[1]['within_1s']), 8)} "
              f"{_fmt(med(lambda r: r[2]['within_1s']), 8)}  "
              f"{_fmt(med(lambda r: (r[1].get('onset') or {}).get('median_ms')), 8)} "
              f"{_fmt(med(lambda r: (r[2].get('onset') or {}).get('median_ms')), 8)}  "
              f"{_fmt(med(lambda r: (r[1].get('onset') or {}).get('p90_ms')), 8)} "
              f"{_fmt(med(lambda r: (r[2].get('onset') or {}).get('p90_ms')), 8)}  "
              f"{_fmt(med(lambda r: (r[1].get('end') or {}).get('median_ms')), 8)} "
              f"{_fmt(med(lambda r: (r[2].get('end') or {}).get('median_ms')), 8)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
