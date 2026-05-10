"""Rules engine (placeholder)
Stateless per-call rules evaluation. Reads config externally.
"""

def evaluate(state_vector, faiss_results=None, config=None):
    # TODO: implement rule evaluation returning recommendation + confidence
    return {
        'recommendation': 'Maintain current mode',
        'confidence': 0.5,
        'reason': 'placeholder'
    }
