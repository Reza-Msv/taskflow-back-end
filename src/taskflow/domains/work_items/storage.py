import os
import re
import uuid
from pathlib import Path
from typing import Protocol

from fastapi import UploadFile, status

from taskflow.core.config import settings
from taskflow.core.exceptions import DomainException


def sanitize_filename(filename: str) -> str:
    """
    Sanitize an uploaded filename to prevent directory traversal and header injection.
    Retains only alphanumeric characters, dots, underscores, and hyphens.
    """
    # Extract basename in case client supplied a path
    clean_name = os.path.basename(filename)
    # Replace dangerous characters
    clean_name = re.sub(r"[^a-zA-Z0-9._-]", "_", clean_name)
    # Avoid hidden files starting with dot
    clean_name = clean_name.lstrip(".")
    if not clean_name:
        clean_name = f"attachment_{uuid.uuid4().hex[:8]}"
    return clean_name[:255]


class StorageBackend(Protocol):
    """Abstract interface for file attachment storage."""

    async def save_file(
        self,
        file: UploadFile,
        subfolder: str | None = None,
    ) -> tuple[str, str, int, str]:
        """
        Save an uploaded file and return:
        (sanitized_filename, storage_path, file_size, content_type)
        """
        ...

    def get_absolute_path(self, storage_path: str) -> Path:
        """Resolve storage path to absolute filesystem path with containment check."""
        ...

    async def delete_file(self, storage_path: str) -> bool:
        """Delete file from storage if it exists."""
        ...


class LocalStorageBackend:
    """
    Local filesystem storage backend with path traversal defense,
    extension allowlists, and size verification.
    """

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.ATTACHMENT_STORAGE_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_absolute_path(self, storage_path: str) -> Path:
        """
        Resolve storage_path safely against base_dir.
        Enforces that the path is strictly within base_dir.
        """
        # Normalize relative path
        norm_path = os.path.normpath(storage_path.lstrip("/\\"))
        target = (self.base_dir / norm_path).resolve()
        try:
            target.relative_to(self.base_dir)
        except ValueError:
            raise DomainException(
                code="INVALID_FILE_PATH",
                message="Invalid or malicious file path detected.",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from None
        return target

    async def save_file(
        self,
        file: UploadFile,
        subfolder: str | None = None,
    ) -> tuple[str, str, int, str]:
        """
        Validate, safely store file, and return metadata:
        (original_filename, relative_storage_path, file_size, content_type)
        """
        raw_name = file.filename or "attachment"
        safe_name = sanitize_filename(raw_name)

        # Validate file extension
        suffix = Path(safe_name).suffix.lower()
        if suffix not in settings.ATTACHMENT_ALLOWED_EXTENSIONS:
            raise DomainException(
                code="UNSUPPORTED_FILE_TYPE",
                message=f"File extension '{suffix}' is not permitted.",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={
                    "extension": suffix,
                    "allowed": settings.ATTACHMENT_ALLOWED_EXTENSIONS,
                },
            )

        content_type = file.content_type or "application/octet-stream"

        # Unique storage key on disk to prevent collisions and execution
        stored_identifier = f"{uuid.uuid4().hex}{suffix}"

        if subfolder:
            clean_subfolder = re.sub(r"[^a-zA-Z0-9_-]", "", subfolder)
            target_dir = self.base_dir / clean_subfolder
            target_dir.mkdir(parents=True, exist_ok=True)
            relative_path = f"{clean_subfolder}/{stored_identifier}"
            dest_path = target_dir / stored_identifier
        else:
            relative_path = stored_identifier
            dest_path = self.base_dir / stored_identifier

        # Stream write with size limit checking
        total_size = 0
        chunk_size = 1024 * 64  # 64 KB

        try:
            with open(dest_path, "wb") as f:
                while chunk := await file.read(chunk_size):
                    total_size += len(chunk)
                    if total_size > settings.ATTACHMENT_MAX_FILE_SIZE_BYTES:
                        raise DomainException(
                            code="FILE_TOO_LARGE",
                            message=(
                                f"File exceeds maximum allowed size of "
                                f"{settings.ATTACHMENT_MAX_FILE_SIZE_BYTES} bytes."
                            ),
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            details={
                                "max_bytes": settings.ATTACHMENT_MAX_FILE_SIZE_BYTES,
                                "received_bytes": total_size,
                            },
                        )
                    f.write(chunk)
        except Exception:
            # Clean up partial write on failure
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except OSError:
                    pass
            raise

        return safe_name, relative_path, total_size, content_type

    async def delete_file(self, storage_path: str) -> bool:
        """Delete stored file from disk."""
        try:
            target = self.get_absolute_path(storage_path)
            if target.exists() and target.is_file():
                target.unlink()
                return True
        except Exception:
            return False
        return False


# Global default storage provider instance
default_storage: StorageBackend = LocalStorageBackend()
