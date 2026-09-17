"""Worker d'inférence Rec-RIR, exécuté dans l'environnement isolé `.venv-engine` (architecture §8).

Protocole : un objet JSON par ligne sur stdin → une réponse JSON par ligne sur stdout ; journaux sur stderr.
Les tableaux transitent par fichiers dans un dossier de travail contrôlé par l'application.

Requêtes :
  {"cmd": "ping"}
  {"cmd": "estimate", "job_id": str, "input_wav": chemin WAV mono 16 kHz, "output_npy": chemin}

Ce module n'importe rien de l'application principale (ni FastAPI, ni l'UI).
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path

PROTOCOL_OUT = sys.stdout
sys.stdout = sys.stderr  # tout print() éventuel du code de recherche part dans les journaux

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from scipy import signal as sps  # noqa: E402

HERE = Path(__file__).resolve().parent
ADAPTER_VERSION = "0.1.0"
CODE_REVISION = "b3ac6fc1421bd58e017022037feb14f30366b46f"
NATIVE_SR = 16000
# Configuration publique config/Rec-RIR.toml au commit épinglé.
MODEL_ARGS = dict(
    dim_input=2, dim_output_spch=2, dim_output_CTF=120, dim_hidden=96, dim_squeeze=8, num_freqs=257,
    num_layers_spch=6, num_layers_noise=2, num_layers_CTF=6, encoder_kernel_size=5, dropout=[0, 0, 0],
    kernel_size=[5, 3], conv_groups=[8, 8], norms=["LN"] * 6, full_share=0, attention="mamba(16,4)",
)
ACOUSTIC_ARGS = dict(sr=16000, n_fft=512, win_len=512, hop_len=256, win_type="sqrthann")


def _install_shims() -> None:
    """mamba_ssm → implémentation CPU de référence ; torchaudio → module vide (seules les fonctions
    non utilisées ici en dépendent : chargement/sauvegarde WAV et convolution finale, refaites ci-dessous)."""
    from mimetic.engines.recrir import mamba_ref

    shim = types.ModuleType("mamba_ssm")
    shim.Mamba = mamba_ref.Mamba
    sys.modules["mamba_ssm"] = shim
    if "torchaudio" not in sys.modules:
        ta = types.ModuleType("torchaudio")
        ta.functional = types.ModuleType("torchaudio.functional")
        sys.modules["torchaudio"] = ta
        sys.modules["torchaudio.functional"] = ta.functional


class Engine:
    def __init__(self, repo_dir: Path, ckpt_path: Path):
        _install_shims()
        sys.path.insert(0, str(repo_dir))
        from acoustics.feature import transforms  # code des auteurs, non modifié
        from method.pim import PIM
        from model.RecRIR import BiSpatialNet

        torch.set_num_threads(max(1, os.cpu_count() or 1))
        self.TF = transforms(**ACOUSTIC_ARGS)
        self.model = BiSpatialNet(**MODEL_ARGS)
        # weights_only=True : désérialisation restreinte aux tenseurs, pas d'objets Python arbitraires.
        ckpt = torch.load(str(ckpt_path), map_location="cpu", weights_only=True)
        state = {}
        for k, v in ckpt["model"].items():
            if any(x in k for x in ["ops", "params"]):  # même filtre que inference.py
                continue
            state[k[7:] if k.startswith("module.") else k] = v
        self.model.load_state_dict(state, strict=True)
        self.model.eval()
        self.pim = PIM(sr=NATIVE_SR)

    @torch.no_grad()
    def estimate(self, wav: np.ndarray) -> tuple[np.ndarray, dict]:
        """Reprend PIM.process sans ses deux post-traitements destructifs :
        ni découpe au maximum global − 2,5 ms, ni normalisation au pic ; la sortie complète est rendue."""
        x = torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32))
        ctf = self.pim.init_seg(self.TF, self.model, x)   # normalisation d'amplitude + réseau + flip
        L = ctf.shape[-1]
        sweep_spec = self.TF.stft(self.pim.sinesweep, "complex")
        sweep_spec = torch.nn.functional.pad(sweep_spec, (L - 1, L - 1)).unfold(1, L, 1)
        ir_spec = torch.matmul(sweep_spec, ctf.unsqueeze(2)).squeeze()
        ir = self.TF.istft(ir_spec, "complex")
        rir = sps.fftconvolve(self.pim.invfilter.double().numpy(), ir.double().numpy(), mode="full")
        peak = int(np.argmax(np.abs(rir)))
        diag = {
            "ctf_frames": int(L),
            "ctf_horizon_seconds": L * ACOUSTIC_ARGS["hop_len"] / NATIVE_SR,
            "input_scale": float(self.TF.ori_scale),
            "raw_length": int(rir.size),
            "raw_global_peak_index": peak,
        }
        return rir, diag


def main() -> None:
    repo = Path(os.environ["MIMETIC_RECRIR_REPO"])
    ckpt = Path(os.environ["MIMETIC_RECRIR_CKPT"])
    engine = None

    def reply(obj):
        PROTOCOL_OUT.write(json.dumps(obj) + "\n")
        PROTOCOL_OUT.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = {}
        try:
            req = json.loads(line)
            if req.get("cmd") == "ping":
                reply({"ok": True, "torch": torch.__version__, "adapter_version": ADAPTER_VERSION})
                continue
            if req.get("cmd") != "estimate":
                raise ValueError("commande inconnue")
            t0 = time.perf_counter()
            if engine is None:
                engine = Engine(repo, ckpt)
            load_s = time.perf_counter() - t0
            wav, sr = sf.read(req["input_wav"], dtype="float32")
            if sr != NATIVE_SR or wav.ndim != 1:
                raise ValueError(f"entrée attendue mono {NATIVE_SR} Hz")
            if not np.any(wav):
                reply({"job_id": req.get("job_id"), "ok": False,
                       "error": {"code": "EMPTY_REFERENCE", "detail": "sélection silencieuse"}})
                continue
            t1 = time.perf_counter()
            rir, diag = engine.estimate(wav)
            diag.update({"model_load_seconds": load_s, "inference_seconds": time.perf_counter() - t1,
                         "input_seconds": wav.size / NATIVE_SR, "torch": torch.__version__,
                         "threads": torch.get_num_threads()})
            tmp = req["output_npy"] + ".tmp.npy"
            np.save(tmp, rir, allow_pickle=False)
            os.replace(tmp, req["output_npy"])
            reply({"job_id": req.get("job_id"), "ok": True, "output_npy": req["output_npy"], "diagnostics": diag})
        except MemoryError:
            reply({"job_id": req.get("job_id"), "ok": False, "error": {"code": "OUT_OF_MEMORY", "detail": None}})
        except Exception as exc:  # erreur structurée ; le détail complet va dans les journaux
            import traceback
            traceback.print_exc()
            reply({"job_id": req.get("job_id"), "ok": False,
                   "error": {"code": "ENGINE_FAILURE", "detail": f"{type(exc).__name__}: {exc}"}})


if __name__ == "__main__":
    main()
