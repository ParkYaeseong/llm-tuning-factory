"""Explicit-credential S3 imports, kept separate from training and job infrastructure."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


class S3StorageError(ValueError):
    """An actionable error safe to show without exposing SDK request details."""


class DownloadCanceled(S3StorageError):
    pass


@dataclass(frozen=True)
class S3Settings:
    endpoint_url: str = ""
    region: str = "us-east-1"
    addressing_style: str = "auto"
    access_key_id: str = field(default="", repr=False)
    secret_access_key: str = field(default="", repr=False)
    session_token: str = field(default="", repr=False)
    allow_loopback_http: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> S3Settings:
        return cls(
            endpoint_url=str(values.get("S3_ENDPOINT_URL", "") or ""),
            region=str(values.get("S3_REGION", "us-east-1") or "us-east-1"),
            addressing_style=str(values.get("S3_ADDRESSING_STYLE", "auto") or "auto"),
            access_key_id=str(values.get("S3_ACCESS_KEY_ID", "") or ""),
            secret_access_key=str(values.get("S3_SECRET_ACCESS_KEY", "") or ""),
            session_token=str(values.get("S3_SESSION_TOKEN", "") or ""),
            allow_loopback_http=values.get("S3_ALLOW_LOOPBACK_HTTP") is True,
        )


def _endpoint(settings: S3Settings) -> str:
    value = settings.endpoint_url.strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (
            not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")
            or "\\" in value or any(ord(character) < 33 for character in value)
        ):
            raise ValueError
        _ = parsed.port
        loopback = parsed.hostname.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and loopback and settings.allow_loopback_http
        ):
            raise ValueError
    except (ValueError, UnicodeError):
        raise S3StorageError("S3 endpoint must be an HTTPS origin without credentials, query, or path.") from None
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _object_reference(bucket: str, key: str | None = None) -> None:
    if not bucket or len(bucket) > 255 or any(c in bucket for c in "/\\\r\n\x00"):
        raise S3StorageError("An exact S3 bucket name is required.")
    if key is not None and (not key or "\x00" in key or "\r" in key or "\n" in key):
        raise S3StorageError("An exact S3 object key is required.")


def _safe_sdk_error(error: Exception) -> S3StorageError:
    response = getattr(error, "response", {})
    code = response.get("Error", {}).get("Code", "") if isinstance(response, dict) else ""
    messages = {
        "AccessDenied": "S3 access denied. Check bucket permissions and credentials.",
        "InvalidAccessKeyId": "S3 credentials were rejected.",
        "SignatureDoesNotMatch": "S3 signature rejected. Check credentials, region, and endpoint.",
        "ExpiredToken": "S3 session credentials expired.",
        "InvalidToken": "S3 session credentials were rejected.",
        "NoSuchBucket": "S3 bucket was not found.",
        "NoSuchKey": "S3 object was not found.",
        "NoSuchVersion": "The selected S3 object version was not found.",
        "PreconditionFailed": "S3 object changed after selection. Refresh the object and try again.",
        "412": "S3 object changed after selection. Refresh the object and try again.",
    }
    return S3StorageError(messages.get(code, "S3 request failed. Check endpoint, connection, and permissions."))


class S3Storage:
    def __init__(self, settings: S3Settings | Mapping[str, Any], client: Any = None):
        self.settings = settings if isinstance(settings, S3Settings) else S3Settings.from_mapping(settings)
        self.endpoint_url = _endpoint(self.settings)
        if self.settings.addressing_style not in {"auto", "path", "virtual"}:
            raise S3StorageError("S3 addressing style must be auto, path, or virtual.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", self.settings.region):
            raise S3StorageError("An explicit S3 region is required.")
        if not self.settings.access_key_id or not self.settings.secret_access_key:
            raise S3StorageError("Explicit S3 credentials are required; ambient AWS profiles are not used.")
        if client is not None:
            self.client = client
            return
        try:
            import boto3
            from botocore.config import Config

            session = boto3.Session(
                aws_access_key_id=self.settings.access_key_id,
                aws_secret_access_key=self.settings.secret_access_key,
                aws_session_token=self.settings.session_token or None,
                region_name=self.settings.region,
            )
            self.client = session.client(
                "s3", endpoint_url=self.endpoint_url or None, verify=True,
                config=Config(
                    signature_version="s3v4", connect_timeout=10, read_timeout=30,
                    retries={"mode": "standard", "total_max_attempts": 3},
                    s3={"addressing_style": self.settings.addressing_style},
                ),
            )
        except Exception as error:
            raise _safe_sdk_error(error) from None

    def list_objects(
        self, bucket: str, prefix: str = "", continuation_token: str | None = None, max_keys: int = 1000,
    ) -> dict[str, Any]:
        _object_reference(bucket)
        if not 1 <= max_keys <= 1000:
            raise S3StorageError("S3 list page size must be between 1 and 1000.")
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": max_keys}
        if continuation_token:
            request["ContinuationToken"] = continuation_token
        try:
            response = self.client.list_objects_v2(**request)
            objects = [
                {
                    "bucket": bucket, "key": item["Key"], "size": int(item["Size"]),
                    "etag": item.get("ETag"), "version_id": None,
                    "last_modified": _text(item.get("LastModified")),
                }
                for item in response.get("Contents", [])
            ]
            return {
                "objects": objects,
                "common_prefixes": [item["Prefix"] for item in response.get("CommonPrefixes", [])],
                "next_token": response.get("NextContinuationToken"),
                "truncated": bool(response.get("IsTruncated", False)),
            }
        except Exception as error:
            raise _safe_sdk_error(error) from None

    def head(self, bucket: str, key: str, version_id: str | None = None) -> dict[str, Any]:
        _object_reference(bucket, key)
        request: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if version_id:
            request["VersionId"] = version_id
        try:
            response = self.client.head_object(**request)
            return {
                "bucket": bucket, "key": key, "size": int(response["ContentLength"]),
                "etag": response.get("ETag"), "version_id": response.get("VersionId", version_id),
                "checksum_sha256": response.get("ChecksumSHA256"),
                "checksum_type": response.get("ChecksumType"),
                "last_modified": _text(response.get("LastModified")),
            }
        except Exception as error:
            raise _safe_sdk_error(error) from None

    def download(
        self, bucket: str, key: str, destination_directory: str | Path,
        expected: Mapping[str, Any], maximum_bytes: int,
        canceled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Materialize a selected immutable revision; caller owns user import authorization."""
        _object_reference(bucket, key)
        if expected.get("bucket") != bucket or expected.get("key") != key:
            raise S3StorageError("Selected S3 object does not match its frozen metadata.")
        try:
            expected_size = int(expected["size"])
        except (KeyError, ValueError, TypeError):
            raise S3StorageError("S3 object size is missing from selected metadata.") from None
        if maximum_bytes <= 0 or expected_size < 0 or expected_size > maximum_bytes:
            raise S3StorageError("S3 object exceeds the import size limit.")
        version = expected.get("version_id")
        etag = expected.get("etag")
        if not version or version == "null":
            if not isinstance(etag, str) or not etag or any(c in etag for c in "\r\n\x00"):
                raise S3StorageError("An S3 VersionId or ETag is required to freeze the selected object.")
        request: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if version:
            request["VersionId"] = version
        if etag:
            request["IfMatch"] = etag
        body = None
        temporary: Path | None = None
        final_path: Path | None = None
        try:
            self._check_canceled(canceled)
            response = self.client.get_object(**request)
            body = response["Body"]
            if int(response["ContentLength"]) != expected_size:
                raise S3StorageError("S3 object length changed after selection.")
            if etag and response.get("ETag") != etag:
                raise S3StorageError("S3 ETag changed after selection.")
            if version and response.get("VersionId") != version:
                raise S3StorageError("S3 object version changed after selection.")
            directory = Path(destination_directory).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            suffix = PurePosixPath(key).suffix.lower()
            suffix = suffix if suffix in {".json", ".jsonl"} else ".bin"
            descriptor, temporary_name = tempfile.mkstemp(prefix=".s3-import-", suffix=".part", dir=directory)
            temporary = Path(temporary_name)
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(descriptor, "wb") as stream:
                while True:
                    self._check_canceled(canceled)
                    chunk = body.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > maximum_bytes or size > expected_size:
                        raise S3StorageError("S3 download exceeds the selected length or import size limit.")
                    digest.update(chunk)
                    stream.write(chunk)
                self._check_canceled(canceled)
                stream.flush()
                os.fsync(stream.fileno())
            if size != expected_size:
                raise S3StorageError("S3 download is truncated; the selected length was not received.")
            sha256 = digest.hexdigest()
            if expected.get("sha256") and expected["sha256"] != sha256:
                raise S3StorageError("S3 download SHA-256 does not match the selected content hash.")
            self._verify_checksum(expected, response, digest.digest())
            self._check_canceled(canceled)
            final_path = directory / f"s3-{uuid4().hex}{suffix}"
            temporary.replace(final_path)
            temporary = None
            return {
                "path": str(final_path), "size": size, "sha256": sha256,
                "source": {
                    "kind": "s3", "bucket": bucket, "key": key,
                    "version_id": version, "etag": etag, "size": size, "sha256": sha256,
                    "endpoint_url": self.endpoint_url, "region": self.settings.region,
                },
            }
        except S3StorageError:
            raise
        except Exception as error:
            raise _safe_sdk_error(error) from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _check_canceled(canceled: Callable[[], bool] | None) -> None:
        if canceled and canceled():
            raise DownloadCanceled("S3 import canceled; partial data was removed.")

    @staticmethod
    def _verify_checksum(expected: Mapping[str, Any], response: Mapping[str, Any], digest: bytes) -> None:
        # Multipart composite checksums and ETags are deliberately not treated as a content SHA-256.
        for metadata, field_name, type_name in (
            (expected, "checksum_sha256", "checksum_type"),
            (response, "ChecksumSHA256", "ChecksumType"),
        ):
            checksum = metadata.get(field_name)
            if checksum and metadata.get(type_name) == "FULL_OBJECT":
                try:
                    decoded = base64.b64decode(checksum, validate=True)
                except (ValueError, TypeError):
                    raise S3StorageError("S3 full-object SHA-256 metadata is invalid.") from None
                if decoded != digest:
                    raise S3StorageError("S3 full-object SHA-256 integrity check failed.")
