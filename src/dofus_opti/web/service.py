"""Service d'optimisation : assemblage des requêtes, file d'attente, cache.

CP-SAT occupe tous les cœurs pendant 20 à 200 secondes : les demandes sont
sérialisées par un unique worker, et les résultats mis en cache sur la requête
normalisée — la même demande n'est jamais calculée deux fois.
"""

from __future__ import annotations

import json
import queue
import sqlite3
from collections import OrderedDict
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..combat.catalog import load_target
from ..combat.formula import CritPolicy
from ..optim.custom import CustomItemError, CustomItemSpec
from ..optim.model import ForcedItemUnavailable
from ..export.dofusdb import API_URL, build_payload, build_url
from ..model.items import SLOT_CAPACITY, Slot
from ..model.stats import StatKey
from ..optim.request import BuildRequest, StatBound
from ..optim.solver import optimize
from ..optim.statpoints import (
    ASSIGNABLE,
    Allocation,
    base_characteristics,
    base_hit_points,
    load_stat_costs,
    points_available,
    unit_cost,
)

DEFAULT_DB = Path(__file__).resolve().parents[3] / "data" / "dofus.db"

#: mêmes noms courts que la CLI, pour les contraintes du formulaire.
BOUNDABLE = {
    "pa": StatKey.PA, "pm": StatKey.PM, "po": StatKey.PO,
    "vitalite": StatKey.VITALITE, "sagesse": StatKey.SAGESSE,
    "force": StatKey.FORCE, "intelligence": StatKey.INTELLIGENCE,
    "chance": StatKey.CHANCE, "agilite": StatKey.AGILITE,
    "puissance": StatKey.PUISSANCE, "dommages": StatKey.DOMMAGES,
    "critique": StatKey.CRITIQUE_PCT,
    "dommages-critiques": StatKey.DOMMAGES_CRITIQUES,
    "soins": StatKey.SOINS, "invocations": StatKey.INVOCATIONS,
    "retrait-pa": StatKey.RETRAIT_PA, "retrait-pm": StatKey.RETRAIT_PM,
    "esquive-pa": StatKey.ESQUIVE_PA, "esquive-pm": StatKey.ESQUIVE_PM,
    "tacle": StatKey.TACLE, "fuite": StatKey.FUITE,
    "initiative": StatKey.INITIATIVE, "prospection": StatKey.PROSPECTION,
    "res-terre": StatKey.RES_PCT_TERRE, "res-feu": StatKey.RES_PCT_FEU,
    "res-eau": StatKey.RES_PCT_EAU, "res-air": StatKey.RES_PCT_AIR,
    "res-neutre": StatKey.RES_PCT_NEUTRE,
    "res-critiques": StatKey.RES_CRITIQUES, "res-poussee": StatKey.RES_POUSSEE,
}

EXOABLE = {"pa": StatKey.PA, "pm": StatKey.PM, "po": StatKey.PO}


class BadRequest(ValueError):
    pass


@dataclass
class Job:
    job_id: str
    payload: dict
    status: str = "queued"  # queued | running | done | error
    result: dict | None = None
    error: str | None = None
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None


#: Au-delà, les plus anciennes entrées sont oubliées. Un service qui tourne des
#: semaines ne doit pas accumuler des milliers de résultats de 50 Ko.
MAX_JOBS = 200
MAX_CACHE = 200


class OptimizerService:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        if not db_path.exists():
            raise SystemExit(
                f"Base introuvable : {db_path}\n"
                "Lancez d'abord `python -m dofus_opti.ingest.build`."
            )
        self.db_path = db_path
        # `OrderedDict` pour évincer les plus anciennes entrées.
        self.jobs: OrderedDict[str, Job] = OrderedDict()
        self.cache: OrderedDict[str, dict] = OrderedDict()
        self.queue: queue.Queue[str] = queue.Queue()
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._run_worker, daemon=True)
        self.worker.start()

    def _remember(self, job: Job) -> None:
        """Enregistre une tâche, en oubliant les plus anciennes terminées."""
        with self.lock:
            self.jobs[job.job_id] = job
            while len(self.jobs) > MAX_JOBS:
                for job_id, old in self.jobs.items():
                    if old.status in ("done", "error"):
                        del self.jobs[job_id]
                        break
                else:
                    break  # que des tâches en cours : on ne touche à rien

    # ------------------------------------------------------------ connexions

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------- formulaire

    def meta(self) -> dict:
        conn = self.connect()
        try:
            breeds = [r["name"] for r in conn.execute("SELECT name FROM breed ORDER BY name")]
            dofus = [
                {"name": r["name"], "level": r["level"]} for r in conn.execute(
                    "SELECT name, level FROM item WHERE type_name = 'Dofus' "
                    "ORDER BY level, name"
                )
            ]
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
        return {
            "breeds": breeds,
            "dofus": dofus,
            "elements": ["terre", "feu", "eau", "air", "neutre"],
            "bounds": sorted(BOUNDABLE),
            "exos": sorted(EXOABLE),
            "crit_policies": ["expected", "never", "always"],
            "rolls": ["max", "avg", "min"],
            "charges": ["max", "none"],
            "invest": ["force", "intelligence", "chance", "agilite", "vitalite", "sagesse"],
            "game_version": meta.get("game_version", "?"),
        }

    def spells_for(self, breed: str, level: int) -> list[dict]:
        from ..combat.catalog import load_spells

        conn = self.connect()
        try:
            spells = load_spells(conn, breed, level)
        finally:
            conn.close()
        return [
            {
                "name": s.name,
                "ap": s.ap_cost,
                "crit": s.crit_probability,
                "elements": sorted({r.element for r in s.rolls}),
                "damage": " + ".join(
                    f"{r.base_min}-{r.base_max} {r.element}" for r in s.rolls
                ),
            }
            for s in sorted(spells, key=lambda s: (s.ap_cost, s.name))
        ]

    def items_matching(self, fragment: str, level: int, limit: int = 20) -> list[dict]:
        """Recherche d'items équipables, pour le choix des items imposés."""
        conn = self.connect()
        try:
            return [
                {"name": r["name"], "slot": r["slot"], "level": r["level"]}
                for r in conn.execute(
                    "SELECT name, slot, level FROM item "
                    "WHERE name LIKE ? AND level <= ? ORDER BY level DESC, name LIMIT ?",
                    (f"%{fragment}%", level, limit),
                )
            ]
        finally:
            conn.close()

    def monsters_matching(self, fragment: str, limit: int = 15) -> list[str]:
        conn = self.connect()
        try:
            return [
                r["name"] for r in conn.execute(
                    "SELECT DISTINCT name FROM monster WHERE name LIKE ? "
                    "ORDER BY name LIMIT ?",
                    (f"%{fragment}%", limit),
                )
            ]
        finally:
            conn.close()

    # ---------------------------------------------------------------- demandes

    def submit(self, payload: dict) -> dict:
        key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        with self.lock:
            cached = self.cache.get(key)
        if cached is not None:
            job = Job(job_id=str(uuid.uuid4()), payload=payload,
                      status="done", result=cached)
            self._remember(job)
            return {"job_id": job.job_id, "cached": True}

        # La validation se fait à la soumission : une erreur de formulaire doit
        # répondre immédiatement, pas au fond de la file.
        self._assemble(payload, self.connect())

        job = Job(job_id=str(uuid.uuid4()), payload=payload)
        self._remember(job)
        self.queue.put(job.job_id)
        return {"job_id": job.job_id, "cached": False}

    def status(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise BadRequest("tâche inconnue")
            waiting = sum(
                1 for j in self.jobs.values()
                if j.status == "queued" and j.submitted_at < job.submitted_at
            )
        out: dict[str, Any] = {"status": job.status, "queued_before": waiting}
        if job.status == "done":
            out["result"] = job.result
        if job.status == "error":
            out["error"] = job.error
        if job.started_at and not job.finished_at:
            out["elapsed"] = round(time.time() - job.started_at, 1)
        return out

    def publish(self, job_id: str) -> dict:
        """Publie le build sur DofusDB. Déclenché par un clic explicite."""
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None or job.status != "done" or not job.result:
            raise BadRequest("aucun résultat à publier pour cette tâche")
        payload = dict(job.result["dofusdb_payload"])
        payload["shared"] = "public"

        request = urllib.request.Request(
            API_URL,
            headers={"User-Agent": "dofus-opti/0.1", "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            created = json.loads(response.read().decode("utf-8"))
        return {"url": build_url(created["_id"])}

    # ------------------------------------------------------------------ worker

    def _run_worker(self) -> None:
        while True:
            job_id = self.queue.get()
            with self.lock:
                job = self.jobs[job_id]
                job.status = "running"
                job.started_at = time.time()
            try:
                result = self._solve(job.payload)
                key = json.dumps(job.payload, sort_keys=True, ensure_ascii=False)
                with self.lock:
                    job.result = result
                    job.status = "done"
                    job.finished_at = time.time()
                    self.cache[key] = result
                    while len(self.cache) > MAX_CACHE:
                        self.cache.popitem(last=False)
            except Exception as exc:  # noqa: BLE001 — remonté tel quel au client
                with self.lock:
                    job.status = "error"
                    job.error = str(exc)
                    job.finished_at = time.time()

    # ------------------------------------------------------------- résolution

    def _assemble(self, payload: dict, conn: sqlite3.Connection) -> tuple:
        breed = payload.get("breed") or ""
        level = int(payload.get("level") or 200)
        elements = set(payload.get("elements") or [])
        if not elements:
            raise BadRequest("choisissez au moins un élément")

        bounds: dict[StatKey, StatBound] = {}
        for row in payload.get("bounds") or []:
            name = row.get("stat")
            key = BOUNDABLE.get(name)
            if key is None:
                raise BadRequest(f"contrainte inconnue : {name}")
            exact = row.get("exact")
            if exact is not None and exact != "":
                bounds[key] = StatBound.exactly(int(exact))
                continue
            low, high = row.get("min"), row.get("max")
            if (low is None or low == "") and (high is None or high == ""):
                continue
            bounds[key] = StatBound(
                minimum=int(low) if low not in (None, "") else None,
                maximum=int(high) if high not in (None, "") else None,
            )

        base_hp = base_hit_points(level)
        min_hp = payload.get("min_hp")
        if min_hp not in (None, ""):
            needed = max(0, int(min_hp) - base_hp)
            existing = bounds.get(StatKey.VITALITE)
            floor = max(needed, existing.minimum or 0) if existing else needed
            bounds[StatKey.VITALITE] = StatBound(
                minimum=floor, maximum=existing.maximum if existing else None
            )

        exos = {
            EXOABLE[name]: 1 for name in payload.get("exos") or [] if name in EXOABLE
        }

        # Items forgemagés du joueur — le cas typique : « Gelano:pm=+1 », le
        # Gelano PA d'origine portant un exo PM, très courant avant le niveau 199.
        custom_specs = []
        for text in payload.get("custom") or []:
            try:
                custom_specs.append(CustomItemSpec.parse(text))
            except CustomItemError as exc:
                raise BadRequest(str(exc)) from exc

        excluded_slots = {Slot.MONTURE} if payload.get("exclude_mount", True) else set()

        # Items que le joueur veut absolument voir dans le build.
        forced: set[int] = set()
        custom_by_name = {
            spec.base_name.casefold(): -index
            for index, spec in enumerate(custom_specs, start=1)
        }
        for name in payload.get("forced") or []:
            key = name.casefold()
            # Un item forgemagé remplace son modèle du catalogue dans le pool :
            # on impose alors la version du joueur, pas l'original.
            if key in custom_by_name:
                forced.add(custom_by_name[key])
                continue
            row = conn.execute(
                "SELECT ankama_id, level, slot FROM item WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if row is None:
                raise BadRequest(f"item à imposer inconnu : {name}")
            if row["level"] > level:
                raise BadRequest(
                    f"{name} est niveau {row['level']} : inéquipable à {level}"
                )
            if Slot(row["slot"]) in excluded_slots:
                raise BadRequest(
                    f"{name} occupe l'emplacement {row['slot']}, que vous avez exclu"
                )
            forced.add(row["ankama_id"])

        scrolls = int(payload.get("scrolls") or 0)
        manual = payload.get("invest_manual") or {}
        if manual:
            base_chars, allocations = self._manual_invest(
                conn, breed, level, manual, scrolls
            )
        else:
            invest = [StatKey(s) for s in payload.get("invest") or []]
            base_chars, allocations = base_characteristics(
                conn, breed, level, invest=invest or None, scrolled=scrolls or None
            )

        banned: set[int] = set()
        for name in payload.get("banned") or []:
            row = conn.execute(
                "SELECT ankama_id FROM item WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()
            if row is None:
                raise BadRequest(f"item à exclure inconnu : {name}")
            banned.add(row["ankama_id"])

        allowed_dofus = None
        wanted_dofus = payload.get("dofus")
        if wanted_dofus is not None:
            allowed_dofus = set()
            for name in wanted_dofus:
                row = conn.execute(
                    "SELECT ankama_id FROM item WHERE name = ? AND type_name = 'Dofus'",
                    (name,),
                ).fetchone()
                if row is None:
                    raise BadRequest(f"Dofus inconnu : {name}")
                allowed_dofus.add(row["ankama_id"])

        target_name = payload.get("target")
        target = load_target(conn, target_name) if target_name else None

        request = BuildRequest(
            level=level,
            breed=breed,
            elements=elements,
            bounds=bounds,
            crit_policy=CritPolicy(payload.get("crit_policy") or "expected"),
            roll=payload.get("roll") or "max",
            charge_policy=payload.get("charges") or "max",
            spell_names=set(payload.get("spells") or []),
            excluded_slots=excluded_slots,
            allowed_dofus=allowed_dofus,
            base_characteristics=base_chars,
            exos=exos,
            custom_specs=custom_specs,
            forced_items=forced,
            banned_items=banned,
            allow_prysmaradites=bool(payload.get("allow_prysmaradites", False)),
        )
        if target is not None:
            request.target = target
        return request, allocations, base_hp

    def _manual_invest(
        self, conn, breed: str, level: int, manual: dict, scrolls: int
    ) -> tuple[dict[StatKey, int], list[Allocation]]:
        """Répartition choisie par le joueur, aux jauges.

        `manual` donne la valeur **investie** par caractéristique. Le serveur
        revalide le coût avec les barèmes réels de la base : le calcul du
        formulaire n'est jamais cru sur parole.
        """
        costs = load_stat_costs(conn, breed)
        budget = points_available(level)

        base: dict[StatKey, int] = (
            {key: scrolls for key in ASSIGNABLE} if scrolls else {}
        )
        allocations: list[Allocation] = []
        total_spent = 0
        for name, raw in manual.items():
            invested = int(raw or 0)
            if invested <= 0:
                continue
            key = StatKey(name)
            field = ASSIGNABLE.get(key)
            if field is None:
                raise BadRequest(f"caractéristique non investissable : {name}")
            tiers = costs[field]
            spent = sum(unit_cost(i, tiers) for i in range(invested))
            total_spent += spent
            allocations.append(Allocation(
                stat=key, invested=invested, scroll=scrolls,
                points_spent=spent, points_left=0,
            ))
            base[key] = invested + (scrolls if key in ASSIGNABLE else 0)

        if total_spent > budget:
            raise BadRequest(
                f"répartition impossible : {total_spent} points nécessaires, "
                f"{budget} disponibles au niveau {level}"
            )
        return base, allocations

    def _solve(self, payload: dict) -> dict:
        conn = self.connect()
        try:
            request, allocations, base_hp = self._assemble(payload, conn)
            time_limit = min(300.0, float(payload.get("time_limit") or 45.0))

            try:
                solution = optimize(conn, request, time_limit=time_limit)
            except ForcedItemUnavailable as exc:
                raise BadRequest(
                    "un item imposé n'a pas pu être considéré (niveau, emplacement "
                    f"exclu ou exclusion manuelle) — {exc}"
                ) from exc
            if not solution.solved:
                raise BadRequest(
                    "aucune solution ne satisfait ces contraintes ("
                    + "; ".join(solution.notes) + ")"
                )

            return self._render(conn, payload, request, solution, allocations, base_hp)
        finally:
            conn.close()

    def _render(self, conn, payload, request, solution, allocations, base_hp) -> dict:
        items = []
        by_slot: dict[Slot, list] = {}
        for item in solution.items:
            by_slot.setdefault(item.slot, []).append(item)
        for slot in SLOT_CAPACITY:
            for item in by_slot.get(slot, []):
                set_name = None
                if item.set_id:
                    row = conn.execute(
                        "SELECT name FROM item_set WHERE ankama_id = ?", (item.set_id,)
                    ).fetchone()
                    set_name = row["name"] if row else None
                items.append({
                    "slot": slot.value, "name": item.name,
                    "level": item.level, "set": set_name,
                })

        active: dict[int, int] = {}
        for item in solution.items:
            if item.set_id is not None:
                active[item.set_id] = active.get(item.set_id, 0) + 1
        sets = []
        for set_id, worn in sorted(active.items(), key=lambda kv: -kv[1]):
            if worn < 2:
                continue
            row = conn.execute(
                "SELECT name, n_items FROM item_set WHERE ankama_id = ?", (set_id,)
            ).fetchone()
            bonus = [
                f"{r['stat']} {r['max']}" for r in conn.execute(
                    "SELECT stat, max FROM set_bonus WHERE set_id = ? AND item_count = ? "
                    "ORDER BY max DESC LIMIT 6", (set_id, worn),
                )
            ]
            sets.append({"name": row["name"], "worn": worn,
                         "total": row["n_items"], "bonus": bonus})

        totals = {k.value: v for k, v in solution.totals.items() if v}
        vitality = solution.totals.get(StatKey.VITALITE, 0)

        # Les exos portés par un item forgemagé retenu deviennent des exos de
        # build dans l'export — c'est la représentation de DofusDB. On ne les
        # déclare que si l'item est effectivement équipé.
        export_exos = dict(request.exos)
        equipped = {i.name for i in solution.items}
        for spec in request.custom_specs:
            if f"{spec.base_name} (perso)" not in equipped:
                continue
            for stat, (mode, value) in spec.overrides.items():
                if mode == "delta" and value:
                    export_exos[stat] = export_exos.get(stat, 0) + value

        breed_row = conn.execute(
            "SELECT breed_id FROM breed WHERE name = ? COLLATE NOCASE", (request.breed,)
        ).fetchone()
        dofusdb_payload, export_report = build_payload(
            solution.items,
            name=payload.get("build_name") or f"{request.breed} {request.level}",
            level=request.level,
            breed_id=breed_row["breed_id"],
            invested={a.stat: a.invested for a in allocations},
            scrolls=(
                {stat: int(payload.get("scrolls"))
                 for stat in (StatKey.VITALITE, StatKey.SAGESSE, StatKey.FORCE,
                              StatKey.INTELLIGENCE, StatKey.CHANCE, StatKey.AGILITE)}
                if payload.get("scrolls") else {}
            ),
            exos=export_exos,
        )

        return {
            "damage": round(solution.damage),
            "rotation": solution.rotation.describe(),
            "status": solution.status,
            "iterations": solution.iterations,
            "items": items,
            "sets": sets,
            "totals": totals,
            "hp": base_hp + vitality,
            "base_hp": base_hp,
            "allocations": [a.describe() for a in allocations],
            "points_available": points_available(request.level),
            "constraints": request.describe_constraints(),
            "pool": {
                "loaded": solution.pool.loaded,
                "kept": solution.pool.kept,
                "dominated": solution.pool.dominated,
                "prysmaradites_removed": solution.pool.prysmaradites_removed,
                "unobtainable_removed": solution.pool.unobtainable_removed,
            },
            "custom_notes": solution.pool.custom_notes,
            # Un item perso proposé mais écarté doit se voir : sinon l'utilisateur
            # croit à un bug alors que le solveur a simplement trouvé mieux.
            "custom_unused": [
                spec.base_name for spec in request.custom_specs
                if f"{spec.base_name} (perso)" not in equipped
            ],
            "notes": solution.notes,
            "export_warnings": export_report.warnings,
            "dofusdb_payload": dofusdb_payload,
        }
