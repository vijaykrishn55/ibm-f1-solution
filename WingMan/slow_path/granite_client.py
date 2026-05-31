"""Granite Client — IBM Granite 3.3 via Ollama (local, free, no GPU required).

Ollama runs a local OpenAI-compatible server at http://localhost:11434
No API key needed. All inference runs on your CPU.

Model choices (pick based on your RAM):
  granite3.3:2b   ~1.5 GB RAM  ← recommended for low-spec PCs
  granite3.3:8b   ~5.0 GB RAM

Setup:
  1. Install Ollama  → https://ollama.com/download
  2. Run: ollama pull granite3.3:2b
  3. Run: ollama serve   (starts on port 11434 automatically)
  4. python slow_path/granite_client.py   (smoke test)

Never called from the fast path. Triggered by lap events only.
"""

import asyncio
import json
import httpx


# ── Config ────────────────────────────────────────────────────────────────────
_ENDPOINT = "http://localhost:11434/v1/chat/completions"
_MODEL    = "granite3.3:2b"   # change to granite3.3:8b if you have >5 GB free RAM
_TIMEOUT  = 30.0              # local inference can be slower than cloud


class GraniteClient:
    def __init__(
        self,
        endpoint: str = _ENDPOINT,
        model:    str = _MODEL,
        api_key:  str = None,
    ):
        self.endpoint = endpoint
        self.model    = model
        self.api_key  = api_key

    # ── Public API ──────────────────────────────────────────────────────────

    async def call(self, prompt: str, timeout: float = _TIMEOUT) -> dict:
        """
        Send a prompt to local Ollama Granite.
        Returns parsed response dict.
        On error: logs and returns {"error": "..."} — never crashes fast path.
        """
        payload = {
            "model":       self.model,
            "messages":    [{"role": "user", "content": prompt}],
            "max_tokens":  500,
            "temperature": 0.3,
            "stream":      False,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
                text = data["choices"][0]["message"]["content"]
                return self._parse_response(text)

        except httpx.ConnectError:
            print("[Granite] Ollama not running. Start it with: ollama serve")
            return {"error": "ollama_not_running"}
        except httpx.TimeoutException:
            print("[Granite] Timeout — model may still be loading, try again")
            return {"error": "timeout"}
        except Exception as e:
            print(f"[Granite] Error: {e}")
            return {"error": str(e)}

    async def analyse_laps(self, lap_summaries: list) -> dict:
        """Main entry point — called by slow path every 5 laps."""
        if not lap_summaries:
            return {"error": "no laps to analyse"}
        return await self.call(self._build_prompt(lap_summaries))

    # ── Internals ───────────────────────────────────────────────────────────

    def _build_prompt(self, laps: list) -> str:
        lap_lines = "\n".join([
            f"Lap {l['lap']}: avg SOC {l['avg_soc']:.2f}, "
            f"alerts fired: {l['alerts_this_lap']}, "
            f"key decision: {l['key_decision']}"
            for l in laps
        ])

        return f"""You are an F1 race strategist AI. Analyse these lap summaries and respond ONLY with valid JSON.

Lap data:
{lap_lines}

Respond with this exact JSON structure:
{{
  "fan_explanation": "one sentence explaining energy strategy in plain English for fans",
  "strategy_note": "one sentence engineering note for the race engineer",
  "threshold_updates": {{}}
}}

Rules:
- threshold_updates should only contain keys from: soc_danger_threshold, net_lift_value
- Only suggest threshold changes if the data clearly shows a pattern
- Keep fan_explanation under 20 words
- Return only JSON, no markdown, no extra text"""

    def _parse_response(self, text: str) -> dict:
        try:
            clean = text.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())
        except Exception as e:
            print(f"[Granite] Parse error: {e} | Raw: {text[:100]}")
            return {
                "fan_explanation":   text[:100],
                "strategy_note":     "",
                "threshold_updates": {}
            }


# ── Smoke test ────────────────────────────────────────────────────────────────

async def _smoke_test():
    client = GraniteClient()
    print(f"[Granite] Model    : {client.model}")
    print(f"[Granite] Endpoint : {client.endpoint}")
    print(f"[Granite] (No API key needed — Ollama is local)")

    # First check Ollama is reachable
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get("http://localhost:11434/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            print(f"[Granite] Ollama running. Models available: {models}")
            if not any(client.model.split(":")[0] in m for m in models):
                print(f"[Granite] WARNING: '{client.model}' not found.")
                print(f"[Granite] Run: ollama pull {client.model}")
                return
    except Exception:
        print("[Granite] FAILED: Ollama not reachable at localhost:11434")
        print("[Granite] Fix: install Ollama then run 'ollama serve'")
        return

    test_laps = [
        {"lap": 28, "avg_soc": 0.70, "alerts_this_lap": 1, "key_decision": "recharge_under_sc"},
        {"lap": 29, "avg_soc": 0.68, "alerts_this_lap": 0, "key_decision": "nominal"},
        {"lap": 30, "avg_soc": 0.63, "alerts_this_lap": 2, "key_decision": "lift_at_T4"},
    ]

    print("\n[Granite] Sending test prompt to local Granite...")
    result = await client.analyse_laps(test_laps)

    if "error" in result:
        print(f"[Granite] FAILED: {result['error']}")
    else:
        print("[Granite] Response received (OK):")
        print(f"  fan_explanation  : {result.get('fan_explanation', '-')}")
        print(f"  strategy_note    : {result.get('strategy_note', '-')}")
        print(f"  threshold_updates: {result.get('threshold_updates', {})}")


if __name__ == "__main__":
    asyncio.run(_smoke_test())