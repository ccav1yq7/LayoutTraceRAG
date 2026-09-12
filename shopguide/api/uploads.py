import hashlib
import io
import os
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import text

from ..schemas import new_id
from ..sessions.store import Sessions

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 25_000_000


class Uploads:
    def __init__(self, repository, root: Path):
        self.repository = repository
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions = Sessions(repository)

    def add(self, principal, session_id, content, mime):
        session = self.sessions.get(session_id, principal)
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            raise ValueError("UPLOAD_FORMAT")
        if not content or len(content) > MAX_BYTES:
            raise ValueError("UPLOAD_SIZE")
        with Image.open(io.BytesIO(content)) as image:
            if Image.MIME.get(image.format or "") != mime or getattr(
                image, "is_animated", False
            ):
                raise ValueError("UPLOAD_FORMAT")
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("UPLOAD_PIXELS")
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            width, height = normalized.size
            out = io.BytesIO()
            normalized.save(out, format="PNG")
            data = out.getvalue()
        if len(data) > MAX_BYTES:
            raise ValueError("UPLOAD_SIZE")
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / digest
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("UPLOAD_PATH")
        fd, temp = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(temp, path)
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError("UPLOAD_CORRUPTED")
        finally:
            os.unlink(temp)
        identifier = new_id("upload")
        with self.repository.engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO sg_uploads VALUES (:id,:p,:s,:t,:o,:h,:w,:height,:e)"
                ),
                {
                    "id": identifier,
                    "p": principal,
                    "s": session_id,
                    "t": session["task"],
                    "o": hashlib.sha256(content).hexdigest(),
                    "h": digest,
                    "w": width,
                    "height": height,
                    "e": time.time() + 86400,
                },
            )
        return {
            "upload_id": identifier,
            "width": width,
            "height": height,
            "mime": "image/png",
        }

    def record(self, identifier, principal, session_id=None, task_id=None):
        with self.repository.engine.connect() as c:
            row = (
                c.execute(
                    text("SELECT * FROM sg_uploads WHERE id=:id"), {"id": identifier}
                )
                .mappings()
                .one_or_none()
            )
        if not row or row["principal"] != principal:
            raise PermissionError("UPLOAD_FORBIDDEN")
        if row["expires"] < time.time():
            raise PermissionError("UPLOAD_EXPIRED")
        self.sessions.get(row["session"], principal)
        if session_id is not None and row["session"] != session_id:
            raise PermissionError("UPLOAD_SESSION_MISMATCH")
        if task_id is not None and row["task"] != task_id:
            raise PermissionError("UPLOAD_TASK_MISMATCH")
        return dict(row)

    def read(self, identifier, principal, session_id=None, task_id=None):
        row = self.record(identifier, principal, session_id, task_id)
        digest = row["sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("UPLOAD_CORRUPTED")
        path = self.root / digest
        if not path.resolve().is_relative_to(self.root):
            raise PermissionError("UPLOAD_PATH")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("UPLOAD_CORRUPTED")
        return raw
