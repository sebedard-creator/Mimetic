# Mimetic

Chameleon-like direct-sound IR analysis and regeneration for ADR recordings.

**Give your ADR the room it was shot in.** Mimetic listens to a production recording, estimates the acoustics of the location, and renders a **100 % wet reverb stem** for a dry ADR line — ready to drop on a parallel track, sample-aligned, in any DAW.

It runs locally as a small Python web service: open the page from any computer on your LAN. Nothing is uploaded anywhere; all processing happens on the machine that runs it.

![Mimetic main page](docs/images/screenshot.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)
![Status: beta](https://img.shields.io/badge/Status-beta-orange.svg)

---

## Highlights

- **Two files in, one stem out.** Drop the production take and the ADR take; the reverb stem is computed automatically.
- **Wet only, never the voice.** The export contains reflections and tail only — no trace of the production dialogue's words or noise.
- **Sample-accurate placement.** The stem starts exactly where the ADR file starts, so it lines up by simply aligning the file heads.
- **Honest about itself.** Estimated values, warnings and limitations are shown in the page and written to a JSON report next to every export.
- **Full-band output.** The estimator works up to 8 kHz; above that, the highs are reconstructed and clearly flagged as synthesised.
- **No cloud, no account, no phone-home.** Local inference on CPU. No GPU required.
- **Self-contained.** Virtual environments, model weights, working files, exports and logs all live inside the project folder.

## Workflow

1. **SOURCE** — drop the production dialogue. It is used only to analyse the room; its audio never reaches the output.
2. **DESTINATION** — drop the dry ADR line. It receives the reverb and is never altered.
3. **Automatic processing** — the analysis picks up to three 6-second passages among the most active parts of the source (about 30 s of CPU time each, cancellable), then renders the stem in a few seconds.
4. **Audition** — Destination / Reverb only / Destination + reverb, with a shared playhead and a monitoring level that does not affect the export.
5. **Adjust** — reverb level in dB and extra pre-delay; both re-render on the fly.
6. **Export** — `yourfile_ADR_REVERB.wav` plus its JSON report.

Changing the destination or a setting re-renders only. Changing the source re-runs the analysis.

### What you get

| File | Contents |
|---|---|
| `…_ADR_REVERB.wav` | 32-bit float, destination sample rate, mono, wet only, complete tail (`N + M − 1` samples), no normalisation, no limiter |
| `…_ADR_REVERB.json` | Estimated RT60 and DRR, analysed passages, engine revision and weights hash, render settings, peak levels, warnings |
| `…_PROFILE_WET_IR.wav` *(optional)* | The wet impulse response itself, for use in a convolution reverb — disable auto-normalisation there |

In the DAW: put the stem on a track parallel to the ADR, aligned to the same start. Keep the ADR dry and untouched; blend with the stem's fader.

### Getting the best out of it

- Feed the **driest possible ADR**: any reverb already on it will be convolved a second time.
- Give the source **5–20 s of representative dialogue**, pauses and word tails included; the analysis needs the decay, not just the words.
- Prefer a source recorded **in one spot**: a single profile describes one microphone/actor position, not a whole location.
- If the result feels shy, raise the reverb level — the estimator tends to run slightly conservative (see below).

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
| Arguments | `run_beta.py` |
| Working directory | `<project>` |
| Port | `8765` |

To reach the page from other computers, allow the port through Windows Firewall on private networks (administrator PowerShell):

```powershell
New-NetFirewallRule -DisplayName "Mimetic 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Profile Private -Action Allow
```

## Supported audio

WAV, 16/24-bit PCM or 32-bit float, 44.1 or 48 kHz, mono or two channels, up to 10 minutes per file. Two-channel files never get summed silently: you choose left, right, or an explicit mono average. Processing and export are mono.

## Under the hood

| Stage | What happens |
|---|---|
| **Room estimation** | [Rec-RIR](https://github.com/Audio-WestlakeU/Rec-RIR) (Audio-WestlakeU, MIT), official code and weights pinned to a commit, running on **CPU** through a pure-PyTorch port of its Mamba block — the official `mamba-ssm` CUDA kernels do not build on Windows. The port loads the published weights unchanged and matches the reference recurrence to 6e-8 on the final IR. |
| **Passage selection** | Up to three non-overlapping 6-second windows, ranked by signal activity; per-window RT60 and DRR are compared and the most representative one (medoid) is kept, never an average of impulse responses. |
| **Direct / reverb split** | The estimated IR is time-aligned on the direct arrival, the direct packet is removed, and the remaining reflections are scaled relative to it — so your ADR stays at 0 dB and the wet stem carries the right amount of room. |
| **High frequencies** | The estimator runs at 16 kHz, so it knows nothing above 8 kHz. A hybrid extension synthesises the bands above 7.8 kHz from the measured 5–7 kHz reflection envelope, with an air-absorption-shaped decay (1/RT = a + b·f²), bounded so the highs are never brighter or longer than the measured band. |
| **Render** | Full linear convolution, exact acoustic zeros before the first reflection, no normalisation and no hidden limiter. Peaks above 0 dBFS are preserved in float and reported. |

## Status and limitations

Mimetic is a **beta**, and deliberately explicit about what it knows:

- **Highs above 7.8 kHz are synthesised, not measured.** On a synthetic full-band pilot, median level error was 1.5–2.3 dB; very absorptive rooms come out darker than reality.
- **The reverb tends to be about 1.5 dB quiet** (median bias over 18 synthetic cases). The level slider is there for that.
- **Tail limited to about 1 s.** Longer decays are truncated and flagged in the page and the report.
- **A destination with no high-frequency content yields a reverb with none either.**
- **Mono only.** True stereo would need a different model and routing.
- **No authentication.** Anyone on the network can use the page and download the exports: trusted LAN only.
- No EQ matching of the dry voice, no de-reverberation of the ADR, no batch mode, no plug-in version.

Measured results, methods and open questions: [docs/model-evaluation.md](docs/model-evaluation.md) · [docs/validation.md](docs/validation.md) · [docs/decisions.md](docs/decisions.md). The full design document, [architecture.md](architecture.md), is in French.

## Development

```bash
.venv\Scripts\python.exe -m pytest -q
```

The suite covers the audio invariants (direct removal, gain linearity, timing, convolution accuracy, IR resampling, export round-trip), the automatic workflow, and the HF extension.

Command-line tools, for rendering with a manual reverb or a known IR without the web page:

```bash
.venv\Scripts\python.exe -m mimetic.cli manual ADR.wav --rt60 0.8 --drr 6 --out exports
```

Benchmarks (they need dry 16 kHz speech files) live in [benchmarks/](benchmarks/); commands and results are in [docs/model-evaluation.md](docs/model-evaluation.md).

```text
src/mimetic/
  audio/        WAV I/O, DSP (direct/wet split, convolution, IR resampling), hybrid HF extension
  engines/      recrir/ (worker, CPU Mamba port, adapter), known_ir, parametric_manual
  web/          FastAPI service and the single-page UI
  pipeline.py   render and export, shared by the web service and the CLI
  cli.py        command-line tools
tests/          unit and integration tests
benchmarks/     synthetic evaluation scripts for the engine and the HF extension
docs/           evaluation, decisions, validation, screenshot
```

## Credits

Room estimation by [Rec-RIR](https://github.com/Audio-WestlakeU/Rec-RIR) (Audio-WestlakeU). Built on [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), [libsndfile](http://libsndfile.github.io/libsndfile/) via [SoundFile](https://python-soundfile.readthedocs.io/), [PyTorch](https://pytorch.org/), [FastAPI](https://fastapi.tiangolo.com/) and [Uvicorn](https://www.uvicorn.org/).

## License

Mimetic is released under the [MIT License](LICENSE).

Third-party components are **not** included in this repository and keep their own licences, notably [Rec-RIR](https://github.com/Audio-WestlakeU/Rec-RIR) (MIT) and [PyTorch](https://github.com/pytorch/pytorch/blob/main/LICENSE) (BSD-style); they are installed by `setup.cmd`.

---

**Note: the application's user interface and most of the documentation are in French.**

Conçu par Sébastien Bédard
