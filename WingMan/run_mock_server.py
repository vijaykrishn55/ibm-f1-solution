"""
run_mock_server.py  — Run from WingMan/ root:
    python run_mock_server.py

Starts the mock OpenF1 HTTP server on port 8000.
Equivalent to:  uvicorn ingestion.mock_server:app --port 8000 --reload
"""
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import uvicorn

if __name__ == "__main__":
    print("[MockServer] Starting WingMan mock OpenF1 server on http://localhost:8000")
    print("[MockServer] Endpoints: /v1/car_data  /v1/position  /v1/intervals  /v1/session_status")
    print("[MockServer] Press Ctrl+C to stop\n")
    uvicorn.run(
        "ingestion.mock_server:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
        reload=False,
    )
