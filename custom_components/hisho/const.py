"""Constants for the Hisho integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "hisho"
MANUFACTURER: Final = "Hisho"

SNAPSHOT_PATH: Final = "/api/ha/snapshot"
# Highest snapshot schema this version understands. Hisho bumps it when a field
# changes meaning; a newer one is refused rather than misread.
SUPPORTED_SCHEMA: Final = 1

# A Hisho health run takes several seconds of SSH probes; Core caches it for 30 s.
UPDATE_INTERVAL: Final = timedelta(seconds=60)
REQUEST_TIMEOUT: Final = 30

MACHINE_STATES: Final = ["up", "asleep", "away", "maintenance", "unreachable", "faulted"]
FOLDER_STATES: Final = ["ok", "info", "warn", "error", "unknown"]
WORKER_STATES: Final = ["running", "blocked", "unknown"]
