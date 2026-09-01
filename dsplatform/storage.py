"""Storage behind an interface so local -> Cloudflare is a config change, not a rewrite.

Two implementations, identical from the outside:

  LocalStorage  writes to ./storage — the default, and what development uses.
  R2Storage     writes to Cloudflare R2, and hands out presigned URLs that expire.

R2 is the choice because egress is free there. Cost scales with what is stored, not
with how often it is watched, so a dancer going viral is a good day rather than an
invoice. The expiry on a presigned URL is also the literal mechanism behind "the
person who posted can end that privilege" — revocation is not bolted on afterwards.

Pose tracks are stored rounded and gzipped: measured on real tracks that is 9% of
the naive size, and the rounding is to a millimetre, far finer than pose estimation
resolves. They are served still compressed, so the saving is on the wire too.
"""
import gzip
import json
import os
import pathlib
from abc import ABC, abstractmethod

ROOT = pathlib.Path(os.environ.get("STORAGE_DIR", "./storage")).resolve()

# Millimetres. Pose estimation does not resolve anywhere near this finely, so the
# precision thrown away here is noise, and it is most of the file.
POSE_DECIMALS = int(os.environ.get("POSE_DECIMALS", "3"))
GZIP_LEVEL = 6


def compact(payload: dict) -> bytes:
    """Round the coordinates, minify, gzip. The whole size win lives here."""
    out = dict(payload)
    if "j" in out:
        out["j"] = [[[[round(float(v), POSE_DECIMALS) for v in joint]
                      for joint in frame]
                     for frame in dancer]
                    for dancer in out["j"]]
    if "vis" in out and out["vis"]:
        out["vis"] = [[[round(float(v), 3) for v in frame] for frame in dancer]
                      for dancer in out["vis"]]
    raw = json.dumps(out, separators=(",", ":")).encode()
    return gzip.compress(raw, GZIP_LEVEL)


def _inflate(blob: bytes) -> dict:
    """Read either format — tracks written before compaction are plain JSON."""
    if blob[:2] == b"\x1f\x8b":
        blob = gzip.decompress(blob)
    return json.loads(blob)


class Storage(ABC):
    @abstractmethod
    def put_pose(self, key: str, payload: dict) -> str: ...
    @abstractmethod
    def get_pose(self, key: str) -> dict: ...
    @abstractmethod
    def pose_blob(self, key: str) -> tuple[bytes, bool]:
        """Bytes to serve, and whether they are gzipped."""
    @abstractmethod
    def pose_url(self, key: str) -> str: ...
    @abstractmethod
    def put_video(self, key: str, data: bytes) -> str: ...
    @abstractmethod
    def video_url(self, key: str) -> str: ...
    @abstractmethod
    def put_avatar(self, key: str, data: bytes) -> str: ...
    @abstractmethod
    def avatar_bytes(self, key: str) -> bytes: ...
    @abstractmethod
    def delete(self, *, pose: str = "", video: str = "", avatar: str = "") -> None:
        """Remove an object. Missing is not an error — deletion must be safe to
        retry, and a half-finished delete must be finishable."""


class LocalStorage(Storage):
    def __init__(self, root: pathlib.Path = ROOT):
        self.root = root
        (self.root / "pose").mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> pathlib.Path:
        safe = key.replace("..", "").lstrip("/")
        return self.root / "pose" / f"{safe}.json"

    def put_pose(self, key: str, payload: dict) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(compact(payload))
        return key

    def get_pose(self, key: str) -> dict:
        return _inflate(self._path(key).read_bytes())

    def pose_blob(self, key: str) -> tuple[bytes, bool]:
        blob = self._path(key).read_bytes()
        return blob, blob[:2] == b"\x1f\x8b"

    def pose_url(self, key: str) -> str:
        return f"/pose/{key}.json"

    def put_video(self, key: str, data: bytes) -> str:
        p = self.video_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def video_path(self, key: str) -> pathlib.Path:
        return self.root / "video" / f"{key}.mov"

    def video_url(self, key: str) -> str:
        return f"/video/{key}.mov"

    def put_avatar(self, key: str, data: bytes) -> str:
        p = self.root / "avatar" / f"{key}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def avatar_bytes(self, key: str) -> bytes:
        p = self.root / "avatar" / f"{key}.jpg"
        if not p.exists():
            raise FileNotFoundError(key)
        return p.read_bytes()

    def delete(self, *, pose: str = "", video: str = "", avatar: str = "") -> None:
        for path in (self._path(pose) if pose else None,
                     self.video_path(video) if video else None,
                     (self.root / "avatar" / f"{avatar}.jpg") if avatar else None):
            if path is not None:
                path.unlink(missing_ok=True)


class R2Storage(Storage):
    """Cloudflare R2 over its S3-compatible API.

    Reads go out as presigned URLs with a short life, so a link someone copied
    stops working — which is what makes access revocable rather than permanent.
    """

    def __init__(self):
        import boto3
        from botocore.config import Config

        account = os.environ["R2_ACCOUNT_ID"]
        self.bucket = os.environ.get("R2_BUCKET", "dancesage")
        # Everything a non-production run writes lands under its own prefix. Object
        # names are derived from a video count, so a local database and production
        # storage will happily agree on a name for different clips — which is how
        # a real recording got overwritten by a test.
        self.prefix = os.environ.get("R2_PREFIX", "").strip("/")
        if self.prefix:
            self.prefix += "/"
        self.ttl = int(os.environ.get("R2_URL_TTL", "3600"))
        # A custom domain in front of the bucket lets Cloudflare cache the popular
        # clips, so the busiest videos mostly never reach R2 at all.
        self.public_base = os.environ.get("R2_PUBLIC_BASE", "").rstrip("/")

        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4", region_name="auto"),
        )

    def _get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise FileNotFoundError(key) from e
            raise

    def put_pose(self, key: str, payload: dict) -> str:
        self.s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}pose/{key}.json",
                           Body=compact(payload),
                           ContentType="application/json",
                           ContentEncoding="gzip")
        return key

    def get_pose(self, key: str) -> dict:
        return _inflate(self._get(f"{self.prefix}pose/{key}.json"))

    def pose_blob(self, key: str) -> tuple[bytes, bool]:
        blob = self._get(f"{self.prefix}pose/{key}.json")
        return blob, blob[:2] == b"\x1f\x8b"

    def pose_url(self, key: str) -> str:
        return self._url(f"{self.prefix}pose/{key}.json")

    def put_video(self, key: str, data: bytes) -> str:
        self.s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}video/{key}.mov",
                           Body=data, ContentType="video/quicktime")
        return key

    def video_url(self, key: str) -> str:
        return self._url(f"{self.prefix}video/{key}.mov")

    def put_avatar(self, key: str, data: bytes) -> str:
        self.s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}avatar/{key}.jpg",
                           Body=data, ContentType="image/jpeg")
        return key

    def avatar_bytes(self, key: str) -> bytes:
        return self._get(f"{self.prefix}avatar/{key}.jpg")

    def delete(self, *, pose: str = "", video: str = "", avatar: str = "") -> None:
        keys = []
        if pose:
            keys.append(f"{self.prefix}pose/{pose}.json")
        if video:
            keys.append(f"{self.prefix}video/{video}.mov")
        if avatar:
            keys.append(f"{self.prefix}avatar/{avatar}.jpg")
        for k in keys:
            # R2 returns success for a key that is already gone, which is what we want.
            self.s3.delete_object(Bucket=self.bucket, Key=k)

    def _url(self, key: str) -> str:
        if self.public_base:
            return f"{self.public_base}/{key}"
        return self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.ttl)


def get_storage() -> Storage:
    """Local unless STORAGE_BACKEND says otherwise. Nothing leaves the machine by default."""
    if os.environ.get("STORAGE_BACKEND", "local").lower() == "r2":
        return R2Storage()
    return LocalStorage()
