import os
from pathlib import Path


def data_home() -> Path:
    path = Path(os.environ.get("RAILNOMAD_PUBLIC_HOME", Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RailNomadPublic"))
    path.mkdir(parents=True, exist_ok=True)
    for name in ("snapshots", "downloads", "logs", "jobs"):
        (path / name).mkdir(exist_ok=True)
    return path
