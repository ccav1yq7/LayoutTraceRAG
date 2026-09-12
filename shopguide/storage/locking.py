"""Cooperative lifetime lock: all Repository users vs offline backup/restore."""

import fcntl
from pathlib import Path


class RootLock:
    def __init__(self, root: Path, *, exclusive=False, service=False):
        root.mkdir(parents=True, exist_ok=True)
        self.file = (
            root / (".shopguide-service.lock" if service else ".shopguide.lock")
        ).open("a+")
        try:
            fcntl.flock(
                self.file,
                (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            self.file.close()
            raise RuntimeError("DATA_ROOT_BUSY_STOP_SERVICE_FIRST") from None

    def close(self):
        if not self.file.closed:
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
