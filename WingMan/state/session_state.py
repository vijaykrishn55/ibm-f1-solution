"""Session state shared object (placeholder)
Holds live session info and extension hooks for modules.
"""

class SessionState:
    def __init__(self):
        self.latest = None
        self.history = []

    def push(self, state_vector):
        self.latest = state_vector
        self.history.append(state_vector)
