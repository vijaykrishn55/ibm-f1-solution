"""CUSUM change-point detector (placeholder)"""

class Cusum:
    def __init__(self, threshold=1.0):
        self.g = 0.0
        self.threshold = threshold

    def update(self, value, baseline=0.0):
        self.g = max(0.0, self.g + (value - baseline))
        if self.g > self.threshold:
            self.g = 0.0
            return True
        return False
