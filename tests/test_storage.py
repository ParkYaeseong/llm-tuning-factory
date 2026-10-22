from __future__ import annotations

import base64
import hashlib
import hmac
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

import pytest

from tuning_factory.storage import DownloadCanceled, S3Settings, S3Storage, S3StorageError


def settings(**overrides):
    return S3Settings(
        **{"access_key_id": "test-access", "secret_access_key": "test-secret", **overrides}
    )


class MemoryS3:
    def __init__(self, data=b'[{"instruction":"hello","output":"world"}]', **response):
        self.data = data
        self.response = response
        self.calls = []
        self.body = None

    def list_objects_v2(self, **kwargs):
        self.calls.append(("list", kwargs))
        return {
            "Contents": [{"Key": "prefix/train.jsonl", "Size": 42, "ETag": '"opaque-2"'}],
            "CommonPrefixes": [{"Prefix": "prefix/folder/"}],
            "IsTruncated": True,
            "NextContinuationToken": "page-2",
        }

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        return {"ContentLength": len(self.data), "ETag": '"opaque-2"', **self.response}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        self.body = io.BytesIO(self.data)
        return {
            "ContentLength": len(self.data), "ETag": '"opaque-2"',
            "Body": self.body, **self.response,
        }


def test_list_paginates_and_head_preserves_opaque_etag():
    client = MemoryS3()
    storage = S3Storage(settings(), client=client)
    result = storage.list_objects("datasets", "prefix/", "page-1", max_keys=20)
    assert client.calls == [("list", {
        "Bucket": "datasets", "Prefix": "prefix/", "ContinuationToken": "page-1", "MaxKeys": 20,
    })]
    assert result["next_token"] == "page-2"
    assert result["common_prefixes"] == ["prefix/folder/"]
    assert result["objects"][0]["etag"] == '"opaque-2"'
    assert "sha256" not in result["objects"][0]
    metadata = storage.head("datasets", "prefix/train.jsonl", version_id="version-1")
    assert metadata["etag"] == '"opaque-2"'
    assert client.calls[-1] == ("head", {
        "Bucket": "datasets", "Key": "prefix/train.jsonl", "VersionId": "version-1",
    })


def test_download_freezes_version_and_hashes_bytes_without_using_key_as_path(tmp_path):
    client = MemoryS3(VersionId="version-1")
    storage = S3Storage(settings(endpoint_url="https://storage.example.com"), client=client)
    expected = storage.head("datasets", "../../unsafe/train.jsonl", "version-1")
    result = storage.download("datasets", "../../unsafe/train.jsonl", tmp_path, expected, 1024)
    downloaded = Path(result["path"])
    assert downloaded.parent == tmp_path.resolve()
    assert downloaded.name.endswith(".jsonl")
    assert downloaded.read_bytes() == client.data
    assert result["sha256"] == hashlib.sha256(client.data).hexdigest()
    assert result["source"]["version_id"] == "version-1"
    assert result["source"]["endpoint_url"] == "https://storage.example.com"
    assert "test-secret" not in str(result)
    assert client.calls[-1] == ("get", {
        "Bucket": "datasets", "Key": "../../unsafe/train.jsonl", "VersionId": "version-1",
        "IfMatch": '"opaque-2"',
    })
    assert client.body.closed


def test_unversioned_object_requires_if_match_and_checks_full_sha256(tmp_path):
    client = MemoryS3()
    client.response["ChecksumSHA256"] = base64.b64encode(hashlib.sha256(client.data).digest()).decode()
    client.response["ChecksumType"] = "FULL_OBJECT"
    storage = S3Storage(settings(), client=client)
    result = storage.download("datasets", "train.json", tmp_path,
                              storage.head("datasets", "train.json"), 1024)
    assert client.calls[-1][1]["IfMatch"] == '"opaque-2"'
    assert "VersionId" not in client.calls[-1][1]
    assert Path(result["path"]).exists()


@pytest.mark.parametrize("change", [
    {"size": 999}, {"etag": '"changed"'}, {"version_id": "changed"},
    {"checksum_sha256": base64.b64encode(bytes(32)).decode(), "checksum_type": "FULL_OBJECT"},
])
def test_changed_or_corrupt_objects_leave_no_partial_file(tmp_path, change):
    client = MemoryS3(VersionId="original")
    storage = S3Storage(settings(), client=client)
    expected = {**storage.head("datasets", "train.json"), **change}
    with pytest.raises(S3StorageError):
        storage.download("datasets", "train.json", tmp_path, expected, 1024)
    assert list(tmp_path.iterdir()) == []


def test_limit_is_checked_before_fetch_and_again_while_streaming(tmp_path):
    client = MemoryS3()
    storage = S3Storage(settings(), client=client)
    expected = storage.head("datasets", "train.json")
    with pytest.raises(S3StorageError, match="limit"):
        storage.download("datasets", "train.json", tmp_path, expected, 1)
    assert all(call[0] != "get" for call in client.calls)
    client.response["ContentLength"] = 1
    with pytest.raises(S3StorageError):
        storage.download("datasets", "train.json", tmp_path,
                         {**expected, "size": 1}, 10)
    assert list(tmp_path.iterdir()) == []


def test_cancel_closes_stream_and_removes_partial(tmp_path):
    client = MemoryS3(data=b"x" * 10000)
    storage = S3Storage(settings(), client=client)
    calls = 0

    def canceled():
        nonlocal calls
        calls += 1
        return calls >= 3

    with pytest.raises(DownloadCanceled):
        storage.download("datasets", "train.jsonl", tmp_path,
                         storage.head("datasets", "train.jsonl"), 20000, canceled)
    assert client.body.closed
    assert list(tmp_path.iterdir()) == []


def test_error_messages_and_settings_repr_do_not_expose_credentials(tmp_path):
    class FailingS3(MemoryS3):
        def get_object(self, **kwargs):
            raise RuntimeError("Authorization: test-secret X-Amz-Credential=test-access")

    storage = S3Storage(settings(), client=FailingS3())
    assert "test-secret" not in repr(storage.settings)
    with pytest.raises(S3StorageError) as error:
        storage.download("datasets", "train.json", tmp_path,
                         storage.head("datasets", "train.json"), 1024)
    assert "test-secret" not in str(error.value)
    assert "test-access" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("endpoint", [
    "https://user:secret@example.com", "https://example.com/?X-Amz-Credential=secret",
    "https://example.com/#secret", "http://example.com", "https://example.com/path",
    "file:///tmp/data", "https://example.com\\@evil.example",
])
def test_endpoint_rejects_credentials_and_insecure_remote_urls(endpoint):
    with pytest.raises(S3StorageError):
        S3Storage(settings(endpoint_url=endpoint), client=MemoryS3())


def test_real_client_requires_explicit_credentials_without_ambient_fallback(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
    with pytest.raises(S3StorageError, match="credentials"):
        S3Storage(S3Settings())


def test_env_mapping_keeps_explicit_config_and_secret_out_of_repr():
    result = S3Settings.from_mapping({
        "S3_ACCESS_KEY_ID": "access", "S3_SECRET_ACCESS_KEY": "very-secret",
        "S3_SESSION_TOKEN": "token", "S3_ENDPOINT_URL": "https://s3.example.com",
        "S3_REGION": "kr-standard", "S3_ADDRESSING_STYLE": "path",
    })
    assert result.region == "kr-standard"
    assert result.addressing_style == "path"
    assert result.secret_access_key == "very-secret"
    assert "very-secret" not in repr(result)


def test_no_immutable_validator_is_rejected_before_download(tmp_path):
    storage = S3Storage(settings(), client=MemoryS3())
    with pytest.raises(S3StorageError, match="ETag"):
        storage.download("datasets", "train.json", tmp_path,
                         {"bucket": "datasets", "key": "train.json", "size": 1}, 1024)
    assert list(tmp_path.iterdir()) == []


def test_null_version_is_still_protected_by_if_match(tmp_path):
    client = MemoryS3(VersionId="null")
    storage = S3Storage(settings(), client=client)
    storage.download("datasets", "train.json", tmp_path,
                     storage.head("datasets", "train.json"), 1024)
    assert client.calls[-1][1]["IfMatch"] == '"opaque-2"'


def test_multipart_etag_and_composite_checksum_are_not_content_hashes(tmp_path):
    client = MemoryS3(ChecksumSHA256="not-a-full-file-hash-5", ChecksumType="COMPOSITE")
    storage = S3Storage(settings(), client=client)
    result = storage.download("datasets", "train.json", tmp_path,
                              storage.head("datasets", "train.json"), 1024)
    assert result["sha256"] == hashlib.sha256(client.data).hexdigest()


def test_sdk_client_error_is_sanitized_and_precondition_change_is_actionable(tmp_path):
    from botocore.exceptions import ClientError

    class ChangedS3(MemoryS3):
        def get_object(self, **kwargs):
            raise ClientError({
                "Error": {"Code": "PreconditionFailed", "Message": "signed secret test-secret"},
            }, "GetObject")

    storage = S3Storage(settings(), client=ChangedS3())
    with pytest.raises(S3StorageError, match="changed") as error:
        storage.download("datasets", "train.json", tmp_path,
                         storage.head("datasets", "train.json"), 1024)
    assert "test-secret" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_real_sdk_lists_heads_and_downloads_signed_loopback_protocol(tmp_path):
    data = b'[{"instruction":"synthetic","output":"test"}]'
    requests = []
    signatures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _signature_is_valid(self):
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("AWS4-HMAC-SHA256 "):
                return False
            parts = dict(piece.strip().split("=", 1) for piece in authorization.split(" ", 1)[1].split(","))
            access, scope = parts["Credential"].split("/", 1)
            date, region, service, terminal = scope.split("/")
            signed_headers = parts["SignedHeaders"]
            header_lines = "".join(
                f"{name}:{' '.join(self.headers[name].split())}\n" for name in signed_headers.split(";")
            )
            parsed = urlsplit(self.path)
            query = "&".join(
                f"{quote(name, safe='-_.~')}={quote(value, safe='-_.~')}"
                for name, value in sorted(parse_qsl(parsed.query, keep_blank_values=True))
            )
            canonical_request = "\n".join([
                self.command, parsed.path, query, header_lines, signed_headers,
                self.headers["X-Amz-Content-SHA256"],
            ])
            string_to_sign = "\n".join([
                "AWS4-HMAC-SHA256", self.headers["X-Amz-Date"], scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ])
            signing_key = ("AWS4" + "test-secret").encode()
            for segment in (date, region, service, terminal):
                signing_key = hmac.new(signing_key, segment.encode(), hashlib.sha256).digest()
            expected = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
            return access == "test-access" and hmac.compare_digest(expected, parts["Signature"])

        def _respond(self):
            valid = self._signature_is_valid()
            signatures.append(valid)
            requests.append((self.command, self.path, self.headers.get("If-Match")))
            if not valid:
                self.send_response(403)
                self.end_headers()
                return
            is_list = "list-type=2" in self.path
            payload = (
                b'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                b'<Name>datasets</Name><Prefix>prefix/</Prefix><IsTruncated>false</IsTruncated>'
                b'<Contents><Key>prefix/train.json</Key><Size>43</Size><ETag>"opaque-2"</ETag>'
                b'</Contents></ListBucketResult>'
            ) if is_list else data
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Type", "application/xml" if is_list else "application/json")
            self.send_header("ETag", '"opaque-2"')
            self.send_header("x-amz-version-id", "test-version")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        do_HEAD = _respond
        do_GET = _respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        storage = S3Storage(settings(
            endpoint_url=f"http://127.0.0.1:{server.server_port}",
            addressing_style="path", allow_loopback_http=True,
        ))
        assert storage.client.meta.config.signature_version == "s3v4"
        assert storage.list_objects("datasets", "prefix/")["objects"][0]["key"] == "prefix/train.json"
        selected = storage.head("datasets", "prefix/train.json")
        result = storage.download("datasets", "prefix/train.json", tmp_path, selected, 1024)
        assert Path(result["path"]).read_bytes() == data
        assert result["source"]["version_id"] == "test-version"
        assert all(signatures)
        assert len(signatures) == 3
        assert requests[-1] == ("GET", "/datasets/prefix/train.json?versionId=test-version", '"opaque-2"')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
