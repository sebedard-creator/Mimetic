# Mimetic

Chameleon-like direct-sound IR analysis and regeneration for ADR recordings.

**Give your ADR the room it was shot in.** Mimetic listens to a production recording, estimates the acoustics of the location, and renders a **100 % wet reverb stem** for a dry ADR line — ready to drop on a parallel track, sample-aligned, in any DAW.

It runs locally as a small Python web service: open the page from any computer on your LAN. Nothing is uploaded anywhere; all processing happens on the machine that runs it.

![Mimetic main page](docs/images/screenshot.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)
![Version 1.0](https://img.shields.io/badge/Version-1.0-brightgreen.svg)

---

## Highlights

- **Two files in, finished stems out.** Drop the production take and the ADR take; room analysis, tone matching and rendering all run automatically.
- **Room *and* tone.** Beyond the reverb, Match EQ aligns the ADR's colour — mic, proximity, presence — with the production recording.
- **Wet only, never the voice.** The reverb export contains reflections and tail only — no trace of the production dialogue's words or noise.
- **Sample-accurate placement.** The stem starts exactly where the ADR file starts, so it lines up by simply aligning the file heads.
- **Honest about itself.** Estimated values, warnings and limitations are shown in the page and written to a JSON report next to every export.
- **Noise-aware.** Plateau background is measured away from speech and its decay, then discounted: the tone match no longer copies the boom's hiss, and confidence degrades smoothly instead of falling off a cliff.
- **Full-band output.** The estimator works up to 8 kHz; above that, the highs are reconstructed and clearly flagged as synthesised.
- **Two mics, two matches.** Hand it interleaved two-channel files — boom on A1, lav on A2 — and each channel is analysed, tone-matched and rendered on its own, then written back in the same channel order.
- **No cloud, no account, no phone-home.** Local inference on CPU. No GPU required.
- **Self-contained.** Virtual environments, model weights, working files, exports and logs all live inside the project folder.

## Workflow

1. **SOURCE** — drop the production dialogue. It is used only to analyse the room; its audio never reaches the output.
2. **DESTINATION** — drop the dry ADR line. It receives the reverb and is never altered.
3. **Automatic processing** — room analysis (up to three 6-second passages among the most active parts of the source, about 30 s of CPU time each, cancellable), then tone matching, then the render. Every step is cancellable and nothing needs a second click.
4. **Audition** — original ADR / processed ADR / reverb only / full mix, with a shared playhead, plus separate players for the two imported files. The monitoring level never affects the export.
5. **Adjust** — reverb level, extra pre-delay and Match EQ strength; each re-renders on the fly.
6. **Export** — one click per deliverable; the WAV downloads immediately and stays in the project's `data/exports`.

Changing the destination or a setting re-renders only. Changing the source re-runs the analysis.

**Two-channel files.** If both slots hold a two-channel WAV, Mimetic treats them as **two independent sources**: A1 of the source is paired with A1 of the destination, A2 with A2. Each pair gets its own room profile, its own EQ curve and its own render — a boom and a lavalier do not need the same correction — and every export comes back interleaved in that same order. Keeping the channel order consistent between the two files is up to you. A track selector next to the *Reverb* heading picks which one you audition and inspect — waveforms, analysed passages, room figures and Match EQ all follow it; the exports always carry both. The **analyses are per track, the mix settings are shared**: reverb level, extra pre-delay and Match EQ strength apply to A1 and A2 alike, which the page states next to the sliders. If one track fails, the other stays visible and playable and only the export is blocked, naming the track and the reason. Mono in, mono out, exactly as before; a source and a destination with different channel counts fall back to picking one channel on each side.

**Nouveau duo** (*new pair*), next to the status line, empties both slots so you can chain another couple without the analysis firing on a half-replaced pair. Exports and settings are kept.

**Delete Cache** (top right) resets everything after a confirmation dialog: imported files, current room profile, rendered stem, working files **and the exported files** in `data/exports`. Your own source files on disk are never touched.

### What you get

Three buttons, three deliverables. All are 32-bit float at the destination's sample rate, with no normalisation and no limiter, and they carry as many channels as the input pair (one for mono, two interleaved in input order for a two-channel pair; the shorter track is zero-padded at the end, never at the head). Clicking a button downloads the WAV right away and keeps a copy in the project's `data/exports`.

| Button | File | In the DAW |
|---|---|---|
| **IR_ONLY** | `…_IR_ONLY.wav` — reverb only, complete tail (`N + M − 1` samples) | Parallel track **next to** the original ADR |
| **EQ_IR_MIX** | `…_EQ_IR_MIX.wav` — tone-matched voice + reverb in one file | **Replaces** the original ADR — do not stack both |
| **IR_PROFILE** | `…_IR_PROFILE.wav` — the wet impulse response itself | Load in a convolution reverb; disable its auto-normalisation |

Each WAV comes with a JSON report: estimated RT60 and DRR, analysed passages, engine revision and weights hash, render and EQ settings, the EQ curve, peak levels and warnings. Audio files align to the exact start of the destination file.

### Match EQ

Room tone is only half of a match: an ADR booth and a boom on set do not sound alike even in the same room. Match EQ aligns the ADR's **colour** — mic, proximity, presence — with the production recording. It is **on by default** and computed right after the room analysis, with no second click.

It compares speech spectra between the source and the *already reverberated* ADR, so the correction accounts for the reverb about to be added instead of correcting it twice, then applies one causal minimum-phase FIR to the ADR before rendering. A common gain keeps the ADR at its original speech level, and both branches stay coherent: `dry + wet` always equals the filtered mix.

- **Strength** adjustable from 0 to 100 %; turning Match EQ off restores the previous render bit for bit.
- **Bounded** to −9/+6 dB, tightened only when the measurement itself is unstable — **never** because an ADR line is short, since short lines are the norm.
- **Honest about its floor**: with different sentences on each side, roughly 1 dB RMS of the curve is content bias rather than a real mic difference. A 4–6 dB difference sits well above that; a 1 dB one does not.
- **Never silent about failure**: without enough usable dialogue the page says so with figures, and the reverb is still rendered.
- **Noisy references are handled explicitly**: background is estimated away from speech *and its decay*, subtracted from the statistics, and each band carries an absolute confidence that shrinks the correction where the noise dominates. On a 96-case bench the EQ error drops from 1.46 to 1.28 dB at 20 dB SNR and from 2.18 to 1.87 dB under moving background noise, with no regression on clean material.

Measurements, tuning and what is **not** proven (no listening test, no measured real production/ADR pair): [docs/eq-validation.md](docs/eq-validation.md).

### Getting the best out of it

- Feed the **driest possible ADR**: any reverb already on it will be convolved a second time.
- Give the source **5–20 s of representative dialogue**, pauses and word tails included; the analysis needs the decay, not just the words.
- Prefer a source recorded **in one spot**: a single profile describes one microphone/actor position, not a whole location.
- If the result feels shy, raise the reverb level — the estimator tends to run conservative, and its exact dosage is not measurable automatically (see below).
- **Lay a room-tone bed from the production track.** A dry ADR sits in digital silence between words while the boom carries a continuous background; that gap reads as "too clean" even when reverb and tone match. Mimetic does not fabricate ambience.

## Requirements

- Windows 10/11 (developed and tested there; the service itself is cross-platform)
- Python 3.11 (the `py` launcher) and `git` on the PATH
- About 1.1 GB of free disk space in the project folder; **no GPU needed**

## Installation

```bash
setup.cmd
```

This creates `.venv` (application, [requirements.txt](requirements.txt)) and `.venv-engine` (CPU PyTorch, [engine-requirements.txt](engine-requirements.txt)), clones [Rec-RIR](https://github.com/Audio-WestlakeU/Rec-RIR) at a pinned commit into `third_party/`, runs the test suite and reports whether the engine is available.

Everything stays inside the project folder — nothing is written to your user profile, and the folder can be moved or deleted as a whole. The model weights are verified by SHA-256 at start-up; if the engine is missing, the page says so plainly and never fabricates a result.

## Running

```bash
run.cmd
```

The console prints the addresses to open, for example `http://192.168.x.x:8765`.

| Option | Effect |
|---|---|
| `--port 8765` | Port to listen on |
| `--host 127.0.0.1` | This machine only (default `0.0.0.0`, i.e. the whole LAN) |
| `--data-dir`, `--export-dir` | Move working files and exports elsewhere (default: `data/`) |

From a process manager, point it at the project's own interpreter:

| Field | Value |
|---|---|
| Path | `<project>/.venv/Scripts/python.exe` |
| Arguments | `run.py` |
| Working directory | `<project>` |
| Port | `8765` |

To reach the page from other computers, allow the port through Windows Firewall on private networks (administrator PowerShell):

```powershell
New-NetFirewallRule -DisplayName "Mimetic 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Profile Private -Action Allow
```

## Supported audio

WAV, 16/24-bit PCM or 32-bit float, 44.1 or 48 kHz, mono or two channels, up to 10 minutes per file. Two-channel files are never summed silently: a two-channel source **and** a two-channel destination are processed as two independent tracks in channel order, and anything else lets you choose left, right, or an explicit mono average.

## Under the hood

| Stage | What happens |
|---|---|
| **Room estimation** | [Rec-RIR](https://github.com/Audio-WestlakeU/Rec-RIR) (Audio-WestlakeU, MIT), official code and weights pinned to a commit, running on **CPU** through a pure-PyTorch port of its Mamba block — the official `mamba-ssm` CUDA kernels do not build on Windows. The port loads the published weights unchanged and matches the reference recurrence to 6e-8 on the final IR. |
| **Passage selection** | Up to three non-overlapping 6-second windows, ranked by signal activity; per-window RT60 and DRR are compared and the most representative one (medoid) is kept, never an average of impulse responses. |
| **Direct / reverb split** | The estimated IR is time-aligned on the direct arrival, the direct packet is removed, and the remaining reflections are scaled relative to it — so your ADR stays at 0 dB and the wet stem carries the right amount of room. |
| **High frequencies** | The estimator runs at 16 kHz, so it knows nothing above 8 kHz. A hybrid extension synthesises the bands above 7.8 kHz from the measured 5–7 kHz reflection envelope, with an air-absorption-shaped decay (1/RT = a + b·f²), bounded so the highs are never brighter or longer than the measured band. |
| **Speech statistics** | Three temporal classes — speech, decay, background — so a reverb tail is never counted as noise. Each band gets an absolute confidence (signal-to-noise of speech alone, background stability, statistical support) that is never renormalised away. |
| **Render** | Full linear convolution, exact acoustic zeros before the first reflection, no normalisation and no hidden limiter. Peaks above 0 dBFS are preserved in float and reported. |

## Status and limitations

Version 1.0 does what this page describes, and stays deliberately explicit about what it does **not** know.
Nothing below is a bug report: these are measured boundaries, and the page and the JSON reports name them
as they apply.

- **Highs above 7.8 kHz are synthesised, not measured.** On a synthetic full-band pilot, median level error was 1.5–2.3 dB; very absorptive rooms come out darker than reality.
- **The reverb dosage is not calibrated automatically.** The estimator runs conservative (about 1.5 dB quiet over 18 synthetic cases, and audibly drier than a real boom on the one real pair measured). Two automatic calibrations were built, measured against known truth, and **removed** because they followed speech rhythm instead of reverb: see [docs/eq-validation.md](docs/eq-validation.md). Use the level slider.
- **No room tone.** Ambience is out of scope; take it from the production track.
- **Tail limited to about 1 s.** Longer decays are truncated and flagged in the page and the report.
- **A destination with no high-frequency content yields a reverb with none either.**
- **Each channel is treated as its own mono microphone.** Two-channel files are two independent takes of the same scene, not a stereo image: there is no inter-channel coherence, no stereo room model.
- **No authentication.** Anyone on the network can use the page and download the exports: trusted LAN only.
- **No listening test yet.** Everything measured so far is spectral and mostly synthetic; no blind comparison, no measured real production/ADR pair.
- No de-reverberation of the ADR, no batch mode, no plug-in version.

Measured results, methods and open questions: [docs/model-evaluation.md](docs/model-evaluation.md) · [docs/eq-validation.md](docs/eq-validation.md) · [docs/validation.md](docs/validation.md) · [docs/decisions.md](docs/decisions.md). The design documents — [architecture.md](architecture.md), [docs/architecture-match-eq.md](docs/architecture-match-eq.md) and [docs/architecture-references-difficiles.md](docs/architecture-references-difficiles.md) — are in French.

## Development

```bash
.venv\Scripts\python.exe -m pytest -q
```

53 tests, one skipped (the Mamba equivalence check needs PyTorch, so it runs in `.venv-engine`). They cover the audio invariants (direct removal, gain linearity, timing, convolution accuracy, IR resampling, export round-trip), the automatic workflow and its invalidation rules, the HF extension, the EQ filter and routing, and the noise-robust statistics.

Command-line tools, for rendering with a manual reverb or a known IR without the web page:

```bash
.venv\Scripts\python.exe -m mimetic.cli manual ADR.wav --rt60 0.8 --drr 6 --out exports
```

Benchmarks need dry 16 kHz speech files and print a table each:

| Script | Question it answers |
|---|---|
| `recrir_synthetic.py` + `recrir_evaluate.py` | How close are the estimated RT60 and DRR to known rooms? |
| `recrir_fullband.py` + `hybrid_evaluate.py` | Do the synthesised highs match a room with known absorption? |
| `eq_phoneme_bias.py` | How much EQ does a *different text* invent on its own? |
| `eq_noise_bench.py` | How does the tone match hold up against plateau noise? |

Results: [docs/model-evaluation.md](docs/model-evaluation.md) and [docs/eq-validation.md](docs/eq-validation.md).

```text
src/mimetic/
  audio/        WAV I/O, DSP (direct/wet split, convolution, IR resampling),
                hybrid HF extension, EQ filter design
  analysis/     speech statistics, noise-aware confidence, EQ curve estimation
  engines/      recrir/ (worker, CPU Mamba port, adapter), known_ir, parametric_manual
  web/          FastAPI service and the single-page UI
  pipeline.py   render and export, shared by the web service and the CLI
  cli.py        command-line tools
tests/          unit and integration tests
benchmarks/     evaluation scripts for the engine, the HF extension and the tone match
docs/           evaluation, decisions, validation, design documents, screenshot
```

## Credits

Room estimation by [Rec-RIR](https://github.com/Audio-WestlakeU/Rec-RIR) (Audio-WestlakeU). Built on [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), [libsndfile](http://libsndfile.github.io/libsndfile/) via [SoundFile](https://python-soundfile.readthedocs.io/), [PyTorch](https://pytorch.org/), [FastAPI](https://fastapi.tiangolo.com/) and [Uvicorn](https://www.uvicorn.org/).

## License

Mimetic is released under the [MIT License](LICENSE).

Third-party components are **not** included in this repository and keep their own licences, notably [Rec-RIR](https://github.com/Audio-WestlakeU/Rec-RIR) (MIT) and [PyTorch](https://github.com/pytorch/pytorch/blob/main/LICENSE) (BSD-style); they are installed by `setup.cmd`.

---

**Note: the application's user interface and most of the documentation are in French.**

Conçu par Sébastien Bédard
