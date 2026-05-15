"""Granite Client: async wrapper for IBM Granite API.
Never called from the fast path. Triggered by lap completion events only.
"""

import asyncio
import json
import httpx


class GraniteClient:
    def __init__(self, api_key: str, endpoint: str, model: str = "ibm-granite/granite-3.3-8b-instruct"):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model

    async def call(self, prompt: str, timeout: float = 10.0) -> dict:
        """
        Send a prompt to Granite. Returns parsed response dict.
        On timeout or error: logs and returns {"error": "..."} — never crashes fast path.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

                # Extract text from response
                text = data["choices"][0]["message"]["content"]
                return self._parse_response(text)

        except httpx.TimeoutException:
            print("[Granite] Timeout — fast path unaffected")
            return {"error": "timeout"}
        except Exception as e:
            print(f"[Granite] Error: {e}")
            return {"error": str(e)}

    async def analyse_laps(self, lap_summaries: list) -> dict:
        """
        Main entry point. Takes last N lap summaries, returns structured output.
        Called at end of every 10th lap by the pipeline.
        """
        if not lap_summaries:
            return {"error": "no laps to analyse"}

        prompt = self._build_prompt(lap_summaries)
        return await self.call(prompt)

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
        """Safely parse Granite's JSON response."""
        try:
            # Strip markdown fences if present
            clean = text.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())
        except Exception as e:
            print(f"[Granite] Parse error: {e} | Raw: {text[:100]}")
            return {
                "fan_explanation": text[:100],
                "strategy_note": "",
                "threshold_updates": {}
            }