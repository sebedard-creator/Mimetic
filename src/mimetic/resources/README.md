# Native Pro Tools template

Keep this session tree together:

```text
Mimetic_Template.ptx
Audio Files/
  Mimetic_Prototype_Float32_TC_60s.wav
```

The PTX has six empty audio tracks (three IR/MIX pairs). Its Clip List contains
one imported native prototype. The WAV is **entirely silent**, mono 48 kHz,
32-bit float, 60 seconds, with BWF origin 01:00:00:00 at 23.976 fps. It is a
template asset, not a production recording. The builder replaces its clip and
media records with the real Mimetic renders; this silence is not exported.

Both assets are included in the Git source, wheel and source distribution.
Only this named silent WAV is exempted from the repository's audio ignore rule.
Mimetic verifies their hashes and the prototype's BWF identity against the PTX.

PTX SHA256: `68b7339b601e9a425aabffe5f5e78c3e7fae93d58fb258a22c32ab6c54e3bfd3`.
WAV SHA256: `26f2a271b36a707d55e6e44cc875885afe1033f4efc505b6d1a1675b494462c9`.

This native template was prepared by the project author and its export was
opened successfully in Pro Tools on 2026-10-12. The supported layout and runtime
dependency are described in `docs/ptx-v1.5.md` in the project source.
