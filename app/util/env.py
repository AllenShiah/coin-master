"""Environment loading shared by every module's config."""

from __future__ import annotations

import os

try:  # Loading a local .env is a developer convenience; in Docker the values
    # are injected via ``env_file`` so python-dotenv is optional at runtime.
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:  # pragma: no cover - only hit when dep is absent
    pass


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()
