"""Le raccord EQ résiste-t-il à un texte différent ? (architecture-match-eq §12.2, cas « même acteur, phrases différentes »)

Vérité connue : une EQ est appliquée à l'ADR, la source ne la subit pas. La correction idéale est donc
l'inverse exact de cette EQ. On compare deux estimateurs :

- `global`   : un seul spectre moyen de parole par fichier ;
- `classes`  : spectres comparés classe de phonèmes par classe (voyelles / consonnes sourdes).

  python benchmarks/eq_phoneme_bias.py <dossier_parole_seche_16k>

Limite : parole de synthèse, pièce et EQ synthétiques. Mesure la robustesse au **contenu**, pas la
qualité perçue.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy import signal as sps  # noqa: E402

from mimetic.analysis import eq_match  # noqa: E402
from mimetic.audio import eq_filter  # noqa: E402
from mimetic.domain.models import RenderSettings, RoomProfile  # noqa: E402

FS = 48000


def room(rt=0.5, drr_db=6.0, seed=3):
    rng = np.random.default_rng(seed)
    n = int(0.8 * FS)
    t = np.arange(n) / FS
    wet = rng.standard_normal(n) * np.exp(-6.9 * t / rt)
    wet[: int(0.008 * FS)] = 0.0
    wet *= np.sqrt(10 ** (-drr_db / 10) / np.sum(wet ** 2))
    return RoomProfile("p", "known_ir", "validated_dsp", wet, FS, "x", {}, "exact_impulse", None,
                       [0, FS / 2], wet.size / FS, [], {}, {})


def truth_curve(kind: str):
    """EQ appliquée à l'ADR ; la correction attendue est son inverse."""
    f = np.geomspace(40, 20000, 240)
    if kind == "sourd":       # ADR plus sourd : il faudra remonter les aigus
        g = -6.0 / (1 + np.exp(-(np.log(f / 4000)) * 3))
    elif kind == "proximite":  # effet de proximité : graves à retirer
        g = 5.0 * np.exp(-((np.log(f / 180)) ** 2) / 0.6)
    else:                      # bosse de présence
        g = 4.0 * np.exp(-((np.log(f / 2500)) ** 2) / 0.3) - 2.0 * np.exp(-((np.log(f / 500)) ** 2) / 0.5)
    return f, g


def main():
    speech = sorted(Path(sys.argv[1]).glob("*.wav"))
    settings = RenderSettings()
    prof = room()
    rows = []
    for kind in ("aucune", "sourd", "proximite", "presence"):
        # « aucune » : même traitement des deux côtés, seul le texte diffère. La correction idéale
        # est plate : tout ce qui sort est une fausse correction due au contenu.
        f_t, g_t = (np.geomspace(40, 20000, 240), np.zeros(240)) if kind == "aucune" else truth_curve(kind)
        fir = eq_filter.design_fir(f_t, g_t, FS) if kind != "aucune" else {"coefficients": np.array([1.0])}
        for i in range(4):
            a = sps.resample_poly(sf.read(speech[i % len(speech)])[0], 3, 1)          # phrase source
            b = sps.resample_poly(sf.read(speech[(i + 1) % len(speech)])[0], 3, 1)     # autre phrase
            src = eq_match.build_baseline(a, FS, prof, settings)                       # boom = pièce, sans EQ
            dst, _ = eq_filter.apply_filter(b, fir["coefficients"], None, False)       # ADR coloré
            for method, use_classes, lam in (("global λ20", False, 20.0), ("global λ60", False, 60.0),
                                             ("global λ150", False, 150.0), ("classes λ20", True, 20.0)):
                out = eq_match.build_profile(src, FS, dst, FS, prof, settings, use_classes=use_classes,
                                             lambda_smooth=lam)
                c = out["curve"]
                centers = np.asarray(c["frequencies_hz"])
                got = np.asarray(c["gain_db"])
                want = -np.interp(np.log(centers), np.log(f_t), g_t)   # correction idéale = inverse
                band = (centers >= 200) & (centers <= 12000)
                err = got[band] - want[band]
                err -= err.mean()
                rows.append({"cas": f"{kind}#{i}", "methode": method,
                             "err_rms": float(np.sqrt(np.mean(err ** 2))),
                             "besoin_rms": float(np.sqrt(np.mean((want[band] - want[band].mean()) ** 2))),
                             "se": c["median_uncertainty_db"], "sat": c["saturated_fraction"]})
                print(f"{rows[-1]['cas']:<14} {method:<8} erreur {rows[-1]['err_rms']:5.2f} dB "
                      f"(correction demandée {rows[-1]['besoin_rms']:5.2f} dB) "
                      f"incertitude {rows[-1]['se']:.2f} dB, saturé {100*rows[-1]['sat']:.0f} %")
    print()
    for method in ("global λ20", "global λ60", "global λ150", "classes λ20"):
        faux = [r["err_rms"] for r in rows if r["methode"] == method and r["cas"].startswith("aucune")]
        vrais = [r for r in rows if r["methode"] == method and not r["cas"].startswith("aucune")]
        e = [r["err_rms"] for r in vrais]
        b = [r["besoin_rms"] for r in vrais]
        print(f"{method:<8} : fausse correction médiane (texte seul) {np.median(faux):5.2f} dB | "
              f"erreur médiane sur EQ connue {np.median(e):5.2f} dB pour {np.median(b):5.2f} dB demandés")


if __name__ == "__main__":
    main()
