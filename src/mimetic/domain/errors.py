"""Codes d'erreur et d'avertissement stables (architecture §9)."""

from __future__ import annotations

MESSAGES_FR = {
    "INVALID_AUDIO": "Fichier audio invalide ou corrompu.",
    "UNSUPPORTED_FORMAT": "Format non pris en charge : WAV PCM 16/24 bits ou flottant 32 bits, 44,1 ou 48 kHz, mono ou 2 canaux.",
    "LIMIT_EXCEEDED": "Le fichier dépasse une limite configurée.",
    "EMPTY_REFERENCE": "La sélection de référence est vide.",
    "INSUFFICIENT_SPEECH": "Pas assez de parole dans la sélection.",
    "MODEL_UNAVAILABLE": "Moteur d'analyse non installé : l'estimation automatique est désactivée.",
    "ENGINE_FAILURE": "Le moteur a échoué.",
    "OUT_OF_MEMORY": "Mémoire insuffisante pour ce calcul ; essayez un extrait plus court.",
    "DIRECT_PATH_UNRESOLVED": "Le son direct n'a pas pu être séparé des réflexions.",
    "GAIN_UNCALIBRATED": "Niveau de reverb non calibré : réglage manuel requis.",
    "BANDWIDTH_LIMITED": "Bande passante de la reverb limitée.",
    "TAIL_TRUNCATED": "La queue de reverb est tronquée par la durée maximale.",
    "REFERENCE_INCONSISTENT": "La sélection de référence paraît hétérogène.",
    "MIX_OVER_0DBFS": "Le pic dépasse 0 dBFS (conservé en flottant, aucun limiteur).",
    "CANCELLED": "Analyse annulée.",
    "STALE_RESULT":"Le résultat est obsolète : relancez le rendu.",
    "NO_PROFILE": "Aucun profil de reverb utilisable.",
    "NO_ADR": "Aucun ADR importé.",
    "INVALID_PARAMETER": "Paramètre invalide.",
}


class MimeticError(Exception):
    """Erreur structurée avec un code stable."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        message = MESSAGES_FR.get(code, code)
        super().__init__(f"{code}: {message}" + (f" ({detail})" if detail else ""))

    def to_dict(self) -> dict:
        return {"code": self.code, "message": MESSAGES_FR.get(self.code, self.code), "detail": self.detail}
