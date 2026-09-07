"""List the models this API key can actually use.

    python scripts/list_models.py

Availability differs by account age, region and billing status, so published tables are
a guide, not an answer. This asks the API directly.

CAVEAT, learned the hard way: this listing is a catalogue, not a permission check. A model
can appear here and still return 404 "no longer available to new users" when you call it.
The only real test is a request. Treat this list as candidates to try, not a guarantee.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from google import genai  # noqa: E402

from redline.config import require_api_key, settings  # noqa: E402


def main() -> int:
    client = genai.Client(api_key=require_api_key())

    try:
        models = list(client.models.list())
    except Exception as exc:  # noqa: BLE001
        print(f"Could not list models: {type(exc).__name__}: {exc}")
        return 1

    if not models:
        print("The API returned no models for this key.")
        return 1

    print(f"{len(models)} model(s) available to this key.")
    print(f"Currently configured in .env: {settings.gemini_model}\n")

    rows = []
    for m in models:
        name = getattr(m, "name", "") or ""
        short = name.split("/")[-1]
        actions = getattr(m, "supported_actions", None) or getattr(
            m, "supported_generation_methods", None
        )
        rows.append((short, ", ".join(actions) if actions else ""))

    width = max(len(r[0]) for r in rows)
    for short, actions in sorted(rows):
        marker = " <- current" if short == settings.gemini_model else ""
        print(f"  {short:<{width}}  {actions}{marker}")

    print(
        "\nPick one that supports content generation, put it in .env as GEMINI_MODEL,\n"
        "and prefer a 'flash' or 'flash-lite' variant — they carry the largest free quotas."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
