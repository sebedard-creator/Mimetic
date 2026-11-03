# Mimetic 1.5.1 — Portable Mac Intel

Performance update for the standalone Pro Tools ZIP workflow, targeting
**Intel Macs running macOS Monterey 12.7.6**. Portable build **1508**.

> **UNSIGNED / NOT NOTARIZED — MANUAL TERMINAL SETUP REQUIRED.**
> This app is **not signed with an Apple Developer ID certificate** and has
> **not been notarized by Apple**. Its bundled ad hoc signature does not identify
> an Apple-verified developer. You must sign your local copy manually using the
> Terminal commands below before using it.

> **AS IS — NO WARRANTY OR GUARANTEED SUPPORT.**
> This experimental personal build is provided **as is**, without warranty of
> any kind, express or implied, under the MIT License. Compatibility, processing
> accuracy, uninterrupted operation, support and future updates are not guaranteed.
> Keep your original sessions and audio, and verify the exported audio and PTX
> before production use.

## What's new

- **Automatic CPU thread calibration:** the first processing job measures several
  thread counts and saves the selected setting for subsequent jobs on that Mac.
- **Reuse of fixed engine calculations** across analysis windows.
- **Processing duration in the app**, with phase timings in `Mimetic_report.json`.
- App version and exported portable report now identify **1.5.1**.

**No reduced-quality mode:** model weights, FP32 precision, analysis windows,
Match EQ and complete reverb tails are retained. The original Mamba recurrence
is unchanged.

On the Windows validation machine with Torch 2.2.2, median inference time for
the same synthetic input decreased from 9.36 s to 7.73 s, approximately **17 %
less time**. This is an inference benchmark, not a promised speedup on a Mac
or for a complete ZIP. Initial feedback on Intel Monterey reports a slight
improvement; no controlled Mac timings are available yet.

A complete 16-ADR comparison retained all 28 audio placements, lengths, mutes,
clip gains and selected analysis windows. Maximum audio sample difference
from the previous portable pipeline was **1.49 × 10⁻⁸**, consistent with tiny
floating-point rounding differences. Bit-identical output is not promised.

## Download and manual installation

The attached **Mimetic-Mac-Intel-Monterey.zip** contains only **Mimetic.app**,
including Python, the audio model, pt_api and the required PTX template/audio.
No Python installation or compiler is needed on the Mac.

1. Quit the previous app. Extract this ZIP into a **new, writable local folder**,
   such as Desktop or Downloads. `/Applications` is not required.
2. Open **Terminal** and type `MIMETIC_APP=` with no space after `=`.
3. Drag the new **Mimetic.app** from Finder into Terminal and press Return.
4. Run:

```bash
codesign --force --deep --sign - "$MIMETIC_APP" &&
codesign --verify --deep --strict "$MIMETIC_APP" &&
xattr -rd com.apple.quarantine "$MIMETIC_APP" &&
open "$MIMETIC_APP"
```

macOS may initially describe the app as damaged. These commands sign this copy
locally, verify it, remove its quarantine attribute and open it. They do not
compile or notarize it. No `sudo` or global Gatekeeper change is needed in a
folder you own. Repeat the setup for each newly installed copy.

## Usage

Drop a ZIP containing one main `.ptx` and its matching **Audio Files** folder
onto the app icon/window, or use **Choisir un ZIP…**.

- **48 kHz / 23.976**, mono WAV PCM 16/24-bit or float32; up to three track pairs.
- Odd-numbered audio tracks are references; even-numbered tracks are ADR,
  regardless of track names. Paired clips must start at the same sample.
- Consolidate fades/groups first; input plugins, automation and mix gains are
  not printed by Mimetic. Short ADR clips are retained with confidence warnings.

The result is saved in **Downloads** as `SessionName_Mimetic.zip`, containing
the PTX, rendered Audio Files and `Mimetic_report.json`. Existing results are
preserved with numbered suffixes. If Downloads is not writable, the app offers
another destination. Processing is local and offline.

- **IR clips: active, +3.5 dB Clip Gain in the PTX**, without baking gain into WAVs.
- **MIX clips: muted, 0 dB Clip Gain.**
- Original sample positions and full rendered tails are retained.
- Match EQ is 100 %; additional wet gain and pre-delay are 0.

Pro Tools is needed to open the resulting session, not to calculate it.
The standalone app handles session ZIPs; interactive SOURCE/DESTINATION audition
remains in the web version. The app interface is in French.

## Limitations and diagnostics

Support target: **Intel x86_64, macOS Monterey 12.7.6**. Other processors and
OS versions have not been validated for this package. The optimized build 1507
was tried on the target Mac; build 1508 carries the same processing with the
1.5.1 version metadata. Complete native regression testing remains limited.

Room acoustics are estimated. Dirty or highly reverberant references may give
less convincing matches; review the warnings and listen to the result. Mimetic
does not generate production ambience or room tone.

Limits: input ZIP 2 GB, extracted media 4 GB, individual file 2 GB, clip 10 minutes.
One output island cannot exceed 16,777,215 samples (about 349.5 seconds at 48 kHz).

Logs are in `~/Library/Logs/Mimetic`. **Voir le journal** opens the current job
log; the build number appears in `app.log`. For a startup failure:

```bash
tail -n 80 ~/Library/Logs/Mimetic/app.log
```

The first processing job adds a short CPU calibration; its setting is stored
in `~/Library/Caches/Mimetic`. No audio is stored in that calibration cache.

## Integrity and licenses

ZIP SHA256: **143aa2b2774c8b609b13b6ffe368db56604cde222034fde9bf4d6c77270fb9a7**

Mimetic is MIT licensed. Bundled components retain their own licenses,
including Rec-RIR and pt_api **1.6.3** (LGPL-2.1-or-later). Source and license
notices are included in the app.
