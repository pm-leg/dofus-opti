"""Client DofusDB — la source des sorts.

dofusdude ne sert pas les sorts ; DofusDB si, et sur la même version de jeu
(3.6.7.7 au moment de l'écriture). Les deux sources sont donc cohérentes.

L'API est une instance Feathers : pagination par `$limit`/`$skip`, filtres par
champ, et `champ[$in][]` pour les listes.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.dofusdb.fr"
USER_AGENT = "dofus-opti/0.1 (+optimiseur de stuff, usage personnel)"
PAGE_SIZE = 50  # plafond imposé par l'API


def _request(url: str, *, timeout: int = 90, retries: int = 3) -> dict:
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


class DofusDbSource:
    """Sorts, classes et table des effets, avec cache disque."""

    name = "dofusdb"

    def __init__(self, cache_dir: Path, *, lang: str = "fr", refresh: bool = False) -> None:
        self.cache_dir = cache_dir
        self.lang = lang
        self.refresh = refresh
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ bas niveau

    def _url(self, path: str, params: dict) -> str:
        query = urllib.parse.urlencode(params, doseq=True)
        return f"{BASE}/{path}" + (f"?{query}" if query else "")

    def _fetch_all(self, path: str, **params) -> list[dict]:
        """Parcourt toutes les pages d'une collection."""
        rows: list[dict] = []
        skip = 0
        while True:
            page = _request(self._url(path, {**params, "$limit": PAGE_SIZE, "$skip": skip}))
            rows.extend(page.get("data") or [])
            total = page.get("total", 0)
            skip += PAGE_SIZE
            if skip >= total or not page.get("data"):
                break
        return rows

    def _cached(self, key: str, produce) -> list[dict] | dict:
        path = self.cache_dir / f"{key}.json"
        if path.exists() and not self.refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        payload = produce()
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    # ------------------------------------------------------------------ collections

    def game_version(self) -> str:
        try:
            return str(self._cached("ddb_version", lambda: _request(f"{BASE}/version")))
        except RuntimeError:
            return "?"

    def breeds(self) -> list[dict]:
        return self._cached("ddb_breeds", lambda: self._fetch_all("breeds"))

    def effects(self) -> list[dict]:
        """Table officielle des effets, utilisée pour valider notre correspondance."""
        return self._cached("ddb_effects", lambda: self._fetch_all("effects"))

    def monsters(self) -> list[dict]:
        """Monstres et leurs résistances par grade — les cibles d'optimisation.

        On restreint les champs : la réponse complète transporte les butins, les
        sorts et les apparences, sans utilité ici.
        """
        return self._cached(
            "ddb_monsters",
            lambda: self._fetch_all(
                "monsters", **{"$select[]": ["id", "name", "grades"]}
            ),
        )

    def item_sources(self, ankama_ids: list[int]) -> list[dict]:
        """Provenance des items : butin et recette.

        dofusdude ne dit pas si un item est obtenable. Sans cette information, le
        solveur propose des objets d'administrateur — un familier niveau 20 à
        +600 dans chaque caractéristique bat évidemment tout le reste.
        """
        return self._cached(
            "ddb_item_sources",
            lambda: self._fetch_by_ids_selected(
                "items", "id", ankama_ids, ["id", "dropMonsterIds", "hasRecipe"]
            ),
        )

    def _fetch_by_ids_selected(
        self, path: str, field: str, ids: list[int], select: list[str]
    ) -> list[dict]:
        rows: list[dict] = []
        batch = 40
        for start in range(0, len(ids), batch):
            rows.extend(
                self._fetch_all(
                    path, **{f"{field}[$in][]": ids[start:start + batch], "$select[]": select}
                )
            )
        return rows

    def spell_variants(self) -> list[dict]:
        """Associe une classe à ses sorts (`breedId` → `spellIds`)."""
        return self._cached("ddb_spell_variants", lambda: self._fetch_all("spell-variants"))

    def spells(self, spell_ids: list[int]) -> list[dict]:
        return self._cached(
            "ddb_spells", lambda: self._fetch_by_ids("spells", "id", spell_ids)
        )

    def spell_levels(self, spell_ids: list[int]) -> list[dict]:
        return self._cached(
            "ddb_spell_levels", lambda: self._fetch_by_ids("spell-levels", "spellId", spell_ids)
        )

    def _fetch_by_ids(self, path: str, field: str, ids: list[int]) -> list[dict]:
        """Récupère par lots — une requête par identifiant serait interminable."""
        rows: list[dict] = []
        batch = 40
        for start in range(0, len(ids), batch):
            chunk = ids[start:start + batch]
            rows.extend(self._fetch_all(path, **{f"{field}[$in][]": chunk}))
        return rows
