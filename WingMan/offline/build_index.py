# offline/build_index.py
# ─────────────────────────────────────────────────────────────────────────────
# FAISS index builder — Person B (Task B7 Step 2)
#
# Run this ONCE offline to build the similarity search index.
# Reads vectors.npy + outcomes.npy produced by feature_extract.py.
# Writes bahrain_index.faiss + outcomes.npy (kept alongside the index).
#
# Usage:
#   python -m offline.feature_extract   # (first time — downloads FastF1 data)
#   python -m offline.build_index
# ─────────────────────────────────────────────────────────────────────────────

import os
import json
import numpy as np

VECTOR_DIM = 8
INDEX_NAME = "bahrain_index.faiss"
OUT_DIR    = os.path.dirname(os.path.abspath(__file__))


def build(vectors_path: str | None = None, outcomes_path: str | None = None):
    try:
        import faiss
    except ImportError:
        raise ImportError("faiss-cpu not installed. Run: pip install faiss-cpu")

    vec_path = vectors_path or os.path.join(OUT_DIR, "vectors.npy")
    out_path = outcomes_path or os.path.join(OUT_DIR, "outcomes.npy")

    if not os.path.exists(vec_path):
        print(f"vectors.npy not found at {vec_path}")
        print("Running feature_extract to generate synthetic data ...")
        from offline.feature_extract import _build_synthetic
        vectors, outcomes = _build_synthetic(n=500)
        np.save(vec_path, vectors)
        np.save(out_path, outcomes)
    else:
        vectors  = np.load(vec_path).astype(np.float32)
        outcomes = np.load(out_path, allow_pickle=True)

    print(f"Building FAISS index: {len(vectors)} vectors, dim={VECTOR_DIM}")
    assert vectors.shape[1] == VECTOR_DIM, (
        f"Expected {VECTOR_DIM}-dim vectors, got shape {vectors.shape}"
    )

    # Flat L2 index — exact nearest neighbour, fast enough for our N
    index = faiss.IndexFlatL2(VECTOR_DIM)
    index.add(vectors)

    index_path = os.path.join(OUT_DIR, INDEX_NAME)
    faiss.write_index(index, index_path)
    print(f"Index written -> {index_path}  ({index.ntotal} vectors)")

    # Also save a JSON metadata file for the live wrapper
    meta = {
        "dim":         VECTOR_DIM,
        "n_vectors":   int(index.ntotal),
        "index_file":  INDEX_NAME,
        "outcomes_file": "outcomes.npy",
    }
    meta_path = os.path.join(OUT_DIR, "index_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Metadata written -> {meta_path}")
    return index_path


if __name__ == "__main__":
    build()
    print("\nOK  FAISS index built successfully")
