"""Accès aux données source.

Toute source concrète implémente `ItemSource` et rend du JSON *brut*. La
normalisation vit ailleurs : quand une API casse à la sortie d'une mise à jour de
Dofus, seule cette couche doit bouger.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

USER_AGENT = "dofus-opti/0.1 (+optimiseur de stuff, usage personnel)"


class ItemSource(Protocol):
    """Fournit les données brutes d'équipements et de panoplies."""

    name: str

    def game_version(self) -> str: ...

    def equipment(self) -> list[dict]: ...

    def sets(self) -> list[dict]: ...


def _http_get_json(url: str, *, timeout: int = 180, retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"échec de la récupération de {url}") from last


class DofusDudeSource:
    """API publique https://api.dofusdu.de (projet dofusdude/doduapi).

    Les réponses brutes sont mises en cache sur disque : une réingestion ne
    dépend alors plus du réseau, et un build reste reproductible.
    """

    name = "dofusdude"

    def __init__(
        self,
        cache_dir: Path,
        *,
        game: str = "dofus3",
        version: str = "v1",
        lang: str = "fr",
        refresh: bool = False,
    ) -> None:
        self.base = f"https://api.dofusdu.de/{game}/{version}/{lang}"
        self.meta_base = f"https://api.dofusdu.de/{game}/{version}/meta"
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cached(self, key: str, url: str) -> dict:
        path = self.cache_dir / f"{key}.json"
        if path.exists() and not self.refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        payload = _http_get_json(url)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    def game_version(self) -> str:
        try:
            return str(self._cached("meta_version", f"{self.meta_base}/version").get("version", "?"))
        except RuntimeError:
            return "?"

    def equipment(self) -> list[dict]:
        return self._cached("equipment_all", f"{self.base}/items/equipment/all")["items"]

    def sets(self) -> list[dict]:
        return self._cached("sets_all", f"{self.base}/sets/all")["sets"]
