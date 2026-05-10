"""Sliding window stats per corner (circular buffer)"""

from collections import deque

class CornerWindow:
    def __init__(self, size=50):
        self.buf = deque(maxlen=size)

    def push(self, value):
        self.buf.append(value)

    def avg(self):
        if not self.buf:
            return 0.0
        return sum(self.buf)/len(self.buf)
