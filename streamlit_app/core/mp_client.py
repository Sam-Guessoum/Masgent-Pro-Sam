"""Materials Project client with an on-disk cache, retries and friendly errors.

Used for the interactive candidate table (the Masgent tool itself still does
the authoritative download inside a worker). The API key is read from ``.env``
and never logged or stored in the cache.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from core.settings import get_secret, redact

FIELDS = ["material_id", "formula_pretty", "energy_above_hull", "symmetry", "volume", "band_gap", "nsites",
          "density", "is_stable", "formation_energy_per_atom"]


class MPError(RuntimeError):
    """Friendly, key-free error message."""


class MPClient:
    def __init__(self, cache_dir: Path, ttl_days: float = 30, retries: int = 3, timeout: float = 30):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_days * 86400
        self.retries = retries
        self.timeout = timeout

    # ---------------------------------------------------------------- cache
    def _key(self, kind: str, query: str) -> Path:
        h = hashlib.sha256(f"{kind}:{query}".encode()).hexdigest()[:24]
        return self.cache_dir / f"{kind}_{h}.json"

    def _get_cached(self, kind: str, query: str, refresh: bool) -> Any | None:
        p = self._key(kind, query)
        if refresh or not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - data["ts"] < self.ttl:
                return data["value"]
        except Exception:
            return None
        return None

    def _put(self, kind: str, query: str, value: Any) -> None:
        p = self._key(kind, query)
        p.write_text(json.dumps({"ts": time.time(), "query": query, "value": value}, default=str), encoding="utf-8")

    def cache_info(self, kind: str, query: str) -> float | None:
        p = self._key(kind, query)
        try:
            return json.loads(p.read_text(encoding="utf-8"))["ts"] if p.exists() else None
        except Exception:
            return None

    # -------------------------------------------------------------- calling
    def _rester(self):  # noqa: ANN202
        key = get_secret("MP_API_KEY")
        if not key:
            raise MPError("No Materials Project API key. Add MP_API_KEY in Settings (get one free at materialsproject.org/api).")
        try:
            from mp_api.client import MPRester
        except ImportError as e:
            raise MPError("The 'mp-api' package is not installed (uv pip install mp-api).") from e
        return MPRester(key, mute_progress_bars=True)

    def _call(self, fn):  # noqa: ANN001, ANN202
        last = None
        for attempt in range(self.retries):
            try:
                with self._rester() as mpr:
                    return fn(mpr)
            except MPError:
                raise
            except Exception as e:  # network / API errors
                msg = redact(str(e))
                low = msg.lower()
                if "401" in low or "403" in low or "api key" in low or "unauthorized" in low or "invalid" in low and "key" in low:
                    raise MPError("Materials Project rejected the API key. Check it in Settings → Materials Project.") from None
                last = msg
                if "429" in low or "rate" in low:
                    time.sleep(2 * (attempt + 1))
                else:
                    time.sleep(0.8 * (attempt + 1))
        raise MPError(f"Could not reach the Materials Project after {self.retries} attempts: {last[:300] if last else 'unknown error'}")

    # ------------------------------------------------------------------ API
    def search_formula(self, formula: str, refresh: bool = False) -> list[dict]:
        formula = formula.strip()
        cached = self._get_cached("summary", formula, refresh)
        if cached is not None:
            return cached

        def fn(mpr):  # noqa: ANN001, ANN202
            docs = mpr.materials.summary.search(formula=formula, fields=FIELDS)
            rows = []
            for d in docs:
                sym = getattr(d, "symmetry", None)
                rows.append({
                    "material_id": str(d.material_id), "formula": d.formula_pretty,
                    "e_above_hull (eV/atom)": d.energy_above_hull, "space_group": getattr(sym, "symbol", None),
                    "crystal_system": str(getattr(sym, "crystal_system", "") or ""), "nsites": d.nsites,
                    "volume (Å³)": d.volume, "density (g/cm³)": d.density, "band_gap (eV)": d.band_gap,
                    "formation_energy (eV/atom)": d.formation_energy_per_atom, "is_stable": d.is_stable,
                })
            return sorted(rows, key=lambda r: (r["e_above_hull (eV/atom)"] is None, r["e_above_hull (eV/atom)"] or 0))
        rows = self._call(fn)
        self._put("summary", formula, rows)
        return rows

    def structure_poscar(self, material_id: str, conventional: bool = True, refresh: bool = False) -> str:
        q = f"{material_id}:{conventional}"
        cached = self._get_cached("structure", q, refresh)
        if cached is not None:
            return cached

        def fn(mpr):  # noqa: ANN001, ANN202
            from pymatgen.io.vasp import Poscar

            s = mpr.get_structure_by_material_id(material_id, conventional_unit_cell=conventional)
            p = Poscar(s, comment=f"{material_id} from Materials Project (via Masgent Streamlit app)")
            return p.get_str(direct=True) if hasattr(p, "get_str") else str(p)
        text = self._call(fn)
        self._put("structure", q, text)
        return text

    def test_connection(self) -> tuple[bool, str]:
        """Fetch one known entry (Al, mp-134) without revealing the key."""
        try:
            def fn(mpr):  # noqa: ANN001, ANN202
                docs = mpr.materials.summary.search(material_ids=["mp-134"], fields=["material_id", "formula_pretty"])
                return [(str(d.material_id), d.formula_pretty) for d in docs]
            res = self._call(fn)
            if res:
                return True, f"Connected: fetched {res[0][0]} ({res[0][1]})."
            return False, "Connected, but mp-134 was not returned."
        except MPError as e:
            return False, str(e)
