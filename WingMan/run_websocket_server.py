"""
run_websocket_server.py  — Run from WingMan/ root:
    python run_websocket_server.py

Starts the WingMan WebSocket alert server on port 8001.
Equivalent to:  uvicorn output.websocket_server:app --port 8001
"""
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import uvicorn

if __name__ == "__main__":
    print("[WebSocket] Starting WingMan alert WebSocket server on http://localhost:8001")
    print("[WebSocket] UI available at: http://localhost:8001/ui/index.html")
    print("[WebSocket] WS endpoint:     ws://localhost:8001/ws")
    print("[WebSocket] Stats:           http://localhost:8001/stats")
    print("[WebSocket] Press Ctrl+C to stop\n")
    uvicorn.run(
        "output.websocket_server:app",
        host="0.0.0.0",
        port=8001,
        log_level="info",
        reload=False,
    )
