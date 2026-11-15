import os
import tempfile
from pathlib import Path
from typing import Protocol

from app.core.config import settings


class StorageProvider(Protocol):
    def put_original(self, key: str, data: bytes) -> None: ...
    def read_media(self, key: str) -> Path: ...
    def create_derivative(self, key: str, data: bytes) -> None: ...
    def delete_media(self, key: str) -> None: ...
    def generate_signed_or_authorized_url(self, photo_id: str, variant: str) -> str: ...


class LocalStorage:
    def __init__(self, root=None):
        self.root = Path(root or settings.data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def read_media(self, key):
        path = self.root / key
        if Path(key).is_absolute() or ".." in Path(key).parts or not path.resolve().is_relative_to(self.root):
            raise ValueError("invalid storage key")
        cursor = path
        while cursor != self.root:
            if cursor.is_symlink():
                raise ValueError("symlink media is not permitted")
            cursor = cursor.parent
        return path

    def put_original(self, key, data):
        path = self.read_media(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)

    create_derivative = put_original

    def delete_media(self, key):
        self.read_media(key).unlink(missing_ok=True)

    def generate_signed_or_authorized_url(self, photo_id, variant):
        if variant not in {"320", "960", "original"}:
            raise ValueError("invalid variant")
        return f"/api/v1/photos/{photo_id}/media/{variant}"


storage = LocalStorage()
