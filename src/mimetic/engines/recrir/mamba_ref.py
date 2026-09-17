"""Remplacement CPU en PyTorch pur de `mamba_ssm.Mamba` (v2.2.5, chemin `mamba_simple`).

Mêmes noms et formes de paramètres que l'original, afin de charger les poids Rec-RIR sans conversion.
Le calcul suit `selective_scan_ref` de mamba_ssm (référence des auteurs de Mamba) :

    delta = softplus(dt_proj(x_proj(x)) + dt_bias)
    h_t   = exp(delta_t · A) ⊙ h_{t-1} + delta_t · B_t · u_t
    y_t   = <h_t, C_t> + D · u_t,   sortie = out_proj(y ⊙ SiLU(z))

Différence volontaire : exp(delta·A) n'est pas pré-calculé sur toute la séquence
([batch, d_inner, L, d_state], ~2 Go pour 10 s) mais pas à pas, pour tenir en RAM.
`selective_scan_loop` garde la transcription littérale de la référence, pour les tests d'équivalence.
Uniquement l'inférence (pas de cache de génération).
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F


class Mamba(nn.Module):
    def __init__(self, d_model, d_state=16, d_conv=4, expand=2, dt_rank="auto", conv_bias=True, bias=False,
                 layer_idx=None, device=None, dtype=None, **_ignored):
        super().__init__()
        kw = {"device": device, "dtype": dtype}
        self.d_model = d_model
        self.d_state = d_state
        self.d_conv = d_conv
        self.d_inner = int(expand * d_model)
        self.dt_rank = math.ceil(d_model / 16) if dt_rank == "auto" else dt_rank
        self.layer_idx = layer_idx

        self.in_proj = nn.Linear(d_model, self.d_inner * 2, bias=bias, **kw)
        self.conv1d = nn.Conv1d(self.d_inner, self.d_inner, bias=conv_bias, kernel_size=d_conv,
                                groups=self.d_inner, padding=d_conv - 1, **kw)
        self.act = nn.SiLU()
        self.x_proj = nn.Linear(self.d_inner, self.dt_rank + d_state * 2, bias=False, **kw)
        self.dt_proj = nn.Linear(self.dt_rank, self.d_inner, bias=True, **kw)
        A = torch.arange(1, d_state + 1, dtype=torch.float32).repeat(self.d_inner, 1)
        self.A_log = nn.Parameter(torch.log(A))
        self.D = nn.Parameter(torch.ones(self.d_inner))
        self.out_proj = nn.Linear(self.d_inner, d_model, bias=bias, **kw)

    @torch.no_grad()  # inférence uniquement : le scan utilise des opérations en place
    def forward(self, hidden_states: torch.Tensor, inference_params=None) -> torch.Tensor:
        """hidden_states : [batch, seqlen, d_model] → même forme."""
        _, seqlen, _ = hidden_states.shape
        xz = self.in_proj(hidden_states).transpose(1, 2)  # [b, 2·d_inner, L]
        x, z = xz.chunk(2, dim=1)
        x = self.act(self.conv1d(x)[..., :seqlen])       # convolution causale

        x_dbl = self.x_proj(x.transpose(1, 2))            # [b, L, dt_rank + 2N]
        dt, B, C = torch.split(x_dbl, [self.dt_rank, self.d_state, self.d_state], dim=-1)
        delta = F.softplus(F.linear(dt, self.dt_proj.weight) + self.dt_proj.bias)  # [b, L, d_inner]
        A = -torch.exp(self.A_log.float())                # [d_inner, N]
        u = x.transpose(1, 2)                             # [b, L, d_inner]

        y = selective_scan(u, delta, A, B, C)
        y = y + u * self.D
        y = y * self.act(z.transpose(1, 2))
        return self.out_proj(y)


def selective_scan(u, delta, A, B, C):
    """Même récurrence que `selective_scan_loop`, avec opérations en place et produits matriciels
    (~3,5× plus rapide sur CPU). Un scan parallèle par blocs en log-espace a été essayé : exact
    mais ~10× plus lent sur CPU, abandonné."""
    b, L, D = u.shape
    N = A.shape[1]
    u, delta = u.contiguous(), delta.contiguous()
    du = delta * u                                     # [b, L, D]
    Bc = B.contiguous()
    Cc = C.unsqueeze(-1).contiguous()                  # [b, L, N, 1]
    h = u.new_zeros(b, D, N)
    dA = u.new_empty(b, D, N)
    y = u.new_empty(b, L, D)
    for t in range(L):
        torch.mul(delta[:, t, :, None], A, out=dA)
        dA.exp_()
        h.mul_(dA)                                     # exp(delta_t·A) ⊙ h
        h.baddbmm_(du[:, t, :, None], Bc[:, t, None, :])  # + (delta_t·u_t) ⊗ B_t
        y[:, t] = torch.bmm(h, Cc[:, t]).squeeze(-1)   # <h_t, C_t>
    return y


def selective_scan_loop(u, delta, A, B, C):
    """Référence pas à pas. u, delta : [b, L, D] ; A : [D, N] ; B, C : [b, L, N] → y : [b, L, D]."""
    b, L, D = u.shape
    h = u.new_zeros(b, D, A.shape[1])
    ys = []
    for t in range(L):
        d_t = delta[:, t, :].unsqueeze(-1)                          # [b, D, 1]
        h = torch.exp(d_t * A) * h + d_t * B[:, t, None, :] * u[:, t, :, None]
        ys.append(torch.einsum("bdn,bn->bd", h, C[:, t, :]))
    return torch.stack(ys, dim=1)
