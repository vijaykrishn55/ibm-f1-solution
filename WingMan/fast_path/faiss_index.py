# fast_path/faiss_index.py
# ─────────────────────────────────────────────────────────────────────────────
# FAISSIndex — Person B (Task B7 Step 3)
#
# Live query wrapper for the pre-built FAISS index.
# Called from fast_path pipeline to retrieve top-3 historically similar
# situations and pass them to the ConfidenceScorer.
#
# Gracefully degrades: if index file not found, all queries return [].
#
# Usage:
#   faiss_idx = FAISSIndex()
#   matches = faiss_idx.query(state_vector, k=3)
#   # → [{"outcome": "warning_fired", "distance": 0.03}, ...]
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import numpy as np

from offline.feature_extract import extract_features_from_state

_DEFAULT_INDEX_DIR = os.path.join(os.path.dirname(__file__), "..", "offline")


class FAISSIndex:
    """
    Wraps a pre-built FAISS IndexFlatL2 for real-time k-NN queries.

    Loads lazily on first query so startup is never blocked.
    """

    def __init__(
        self,
        index_path:   str | None = None,
        outcomes_path: str | None = None,
    ):
        self._index_path    = index_path    or os.path.join(_DEFAULT_INDEX_DIR, "bahrain_index.faiss")
        self._outcomes_path = outcomes_path or os.path.join(_DEFAULT_INDEX_DIR, "outcomes.npy")
        self._index         = None
        self._outcomes: np.ndarray | None = None
        self._ready         = False

    # ── Lazy load ────────────────────────────────────────────────────────────

    def _load(self) -> bool:
        """Load index + outcomes from disk. Returns True on success."""
        if self._ready:
            return True
        try:
            import faiss
        except ImportError:
            print("[FAISSIndex] faiss-cpu not installed — queries will return []")
            return False

        if not os.path.exists(self._index_path):
            print(f"[FAISSIndex] Index not found at {self._index_path}")
            print("  Run: python -m offline.build_index")
            return False

        if not os.path.exists(self._outcomes_path):
            print(f"[FAISSIndex] Outcomes file not found at {self._outcomes_path}")
            return False

        try:
            self._index    = faiss.read_index(self._index_path)
            self._outcomes = np.load(self._outcomes_path, allow_pickle=True)
            self._ready    = True
            print(f"[FAISSIndex] Loaded {self._index.ntotal} vectors from {self._index_path}")
            return True
        except Exception as e:
            print(f"[FAISSIndex] Load failed: {e}")
            return False

    # ── Query ────────────────────────────────────────────────────────────────

    def query(self, state: dict, k: int = 3) -> list[dict]:
        """
        Find the k most similar historical states.

        Args:
            state: current enriched state vector dict
            k:     number of neighbours to return (default 3)

        Returns:
            List of dicts: [{"outcome": str, "distance": float, "rank": int}]
            Returns [] if index not loaded.
        """
        if not self._load():
            return []

        try:
            features = extract_features_from_state(state)
            query_vec = np.array([features], dtype=np.float32)  # shape (1, 8)

            distances, indices = self._index.search(query_vec, k)

            results = []
            for rank, (dist, idx) in enumerate(zip(distances[0], indices[0])):
                if idx < 0 or idx >= len(self._outcomes):
                    continue
                results.append({
                    "rank":     rank + 1,
                    "outcome":  str(self._outcomes[idx]),
                    "distance": float(dist),
                })
            return results

        except Exception as e:
            print(f"[FAISSIndex] Query error: {e}")
            return []

    def is_ready(self) -> bool:
        return self._ready

    def n_vectors(self) -> int:
        if self._index is None:
            return 0
        return self._index.ntotal


# ── Quick standalone test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    print("FAISSIndex — query test")
    print("─" * 50)

    # Build index if missing
    from offline.build_index import build
    build()

    idx = FAISSIndex()

    test_states = [
        {"name": "High throttle, low SOC",
         "state": {"soc_estimated": 0.22, "throttle": 0.95, "speed": 290,
                   "corner_id": 11, "lap_fraction": 0.70, "energy_delta": -0.008,
                   "gap_ahead": 1.2, "aero_state": "straight_mode"}},
        {"name": "Braking, recovering SOC",
         "state": {"soc_estimated": 0.55, "throttle": 0.05, "speed": 120,
                   "corner_id": 4, "lap_fraction": 0.25, "energy_delta": 0.003,
                   "gap_ahead": 3.0, "aero_state": "corner_mode"}},
        {"name": "Safety car, high SOC",
         "state": {"soc_estimated": 0.80, "throttle": 0.30, "speed": 80,
                   "corner_id": 7, "lap_fraction": 0.50, "energy_delta": 0.001,
                   "gap_ahead": 0.5, "aero_state": "corner_mode"}},
    ]

    all_ok = True
    for case in test_states:
        matches = idx.query(case["state"], k=3)
        print(f"\n  Query: {case['name']}")
        if not matches:
            print("    ⚠ No matches returned (index may not be built)")
            all_ok = False
        else:
            for m in matches:
                print(f"    Rank {m['rank']}: outcome={m['outcome']}  dist={m['distance']:.4f}")

    print(f"\n  Index ready: {idx.is_ready()}")
    print(f"  Vectors:    {idx.n_vectors()}")
    print("\n✓  FAISS query test done")
