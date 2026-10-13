# Mimetic 1.5 — Portable Mac Intel

Standalone ADR room and tone matching for Pro Tools session ZIPs.

> **UNSIGNED / NOT NOTARIZED — MANUAL TERMINAL SETUP REQUIRED.**
> This app is **not signed with an Apple Developer ID certificate** and has
> **not been notarized by Apple**. Its bundled ad hoc signature does not identify
> an Apple-verified developer. You must sign your local copy manually using the
> Terminal commands below before using it.

> **AS IS — NO WARRANTY OR GUARANTEED SUPPORT.**
> This experimental personal build is provided **as is**, without warranty of
> any kind, express or implied, under the MIT License. There is no guarantee of
> compatibility with your system or sessions, processing accuracy, uninterrupted
> operation, support or future updates. Use it at your own risk. Keep your original
> sessions and audio, and verify the exported audio and PTX before production use.

## Supported target

- **Intel Mac (x86_64), macOS Monterey 12.7.6.**
- Mimetic **1.5.0**, portable build **1506**.
- Model, Python runtime, pt_api and the Pro Tools template are included.
- No Python installation, compiler or GPU is needed on the Mac.
- Pro Tools is needed to open the resulting session, but not to render it.

Other processors and macOS versions have not been validated for this package.

## Download and manual installation

1. Download **Mimetic-Mac-Intel-Monterey.zip** from this release's assets.
2. Extract it on your Mac's local disk. The archive contains only **Mimetic.app**.
3. Keep the app in a writable folder such as Desktop or Downloads. Installing
   in `/Applications` is **not required**; it may require an administrator password.
4. For an update, quit the previous app and extract the new one into a fresh folder.

macOS may initially report that the app is damaged or cannot be opened.
To apply a local signature to this copy:

1. Open **Terminal**.
2. Type `MIMETIC_APP=` with **no space after `=`**.
3. Drag **Mimetic.app** from Finder into Terminal, then press **Return**.
4. Paste and run:

```bash
codesign --force --deep --sign - "$MIMETIC_APP" &&
codesign --verify --deep --strict "$MIMETIC_APP" &&
xattr -rd com.apple.quarantine "$MIMETIC_APP" &&
open "$MIMETIC_APP"
```

These commands sign your copy locally, verify its signature, remove quarantine
from **this app only**, and open it. They do not compile the app or notarize it
with Apple. No `sudo` or global Gatekeeper change is needed when the app is in
a folder you own. Repeat this setup for each newly installed version; subsequent
launches of that copy work normally. If a command fails, retain its output for
diagnosis rather than disabling macOS security globally.

## Usage

Drop a Pro Tools session ZIP onto the app icon or its window, or use
**Choisir un ZIP…**. The input must include one main `.ptx` and its matching
**Audio Files** folder, optionally inside a wrapping session folder.

- **48 kHz / 23.976**, mono WAV PCM 16/24-bit or float32.
- Odd-numbered audio tracks are references; even-numbered tracks are ADR,
  regardless of their names. Up to **three pairs** are supported.
- Paired reference/ADR clips must start at the same sample. Consolidate clips,
  fades and groups first; input plugins, automation and mix gains are not bounced.
- Short ADR clips are retained, with warnings if room or EQ estimation is uncertain.

The result is saved in **Downloads** as `SessionName_Mimetic.zip`. Existing
results are retained with `_02`, `_03`, etc. If Downloads is not writable,
the app offers another destination. Finder selects the completed result.

The output contains the PTX, its rendered Audio Files and `Mimetic_report.json`:

- **IR clips: active, +3.5 dB Clip Gain in the PTX**, without baking that gain into WAVs.
- **MIX clips: muted, 0 dB Clip Gain.**
- Original sample positions and full rendered reverb tails are retained.
- Match EQ is 100 %; additional wet gain and pre-delay are 0.

Processing runs locally and offline. The standalone app handles session ZIPs;
the separate SOURCE/DESTINATION audition interface remains in the web version.

## Limitations and diagnostics

Room acoustics are estimated. Dirty or highly reverberant references may produce
less convincing matches; review the warnings and listen to the result. Mimetic
does not generate production ambience or room tone.

Limits: input ZIP 2 GB, extracted media 4 GB, individual file 2 GB, clip 10 minutes.
One output island cannot exceed 16,777,215 samples (about 349.5 seconds at 48 kHz).

The earlier build 1504's startup was confirmed on Intel Monterey. Windows worker
tests and inference/PTX self-tests pass; **this build 1506 still needs full native
Mac validation**, including rendering, cancellation and Pro Tools import.

Logs are in `~/Library/Logs/Mimetic`. For a startup failure:

```bash
tail -n 80 ~/Library/Logs/Mimetic/app.log
```

The current build number appears in the startup log. **Voir le journal** opens
the current job log. The interface is in French.

## Integrity and licenses

ZIP SHA256:

```text
69ee9eee36c97bc1777aa7463cb02a880f30eed4d92a308d8b9254839f5a5e07
```

Mimetic is MIT licensed. Bundled third-party components retain their own licenses,
including Rec-RIR and pt_api 1.6.3 (LGPL-2.1-or-later). Their source and license
notices are included in the app.
