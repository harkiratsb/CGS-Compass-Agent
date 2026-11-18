"""A fake `_call` for framework.agent: canned JSON picked by the [STEP:...] marker a prompt starts with."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MARKER = re.compile(r"\[STEP:(\w+)\]")


def fake_call_from(path):
    responses = json.loads((ROOT / path).read_text())

    def fake(system, user, **kw):
        m = _MARKER.match(user)
        if not m or m.group(1) not in responses:
            raise AssertionError(f"no canned response for prompt starting {user[:40]!r}")
        return json.dumps(responses[m.group(1)])
    return fake
