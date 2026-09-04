"""Meeting transcriber: AssemblyAI diarization + Claude name guessing + editable transcripts."""
from pathlib import Path
import os

def load_env(path: str = ".env") -> None:
    """Tiny .env loader so there's no python-dotenv dependency."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
