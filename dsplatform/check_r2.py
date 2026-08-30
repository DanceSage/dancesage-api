"""Prove the R2 credentials work before anything depends on them.

Writes a small object, reads it back, signs a URL, fetches that URL, deletes it.
Nothing else in the system is touched.

    STORAGE_BACKEND=r2 python -m dsplatform.check_r2
"""
import os, sys, time, urllib.request

REQUIRED = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]


def main() -> int:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print("Missing environment variables:", ", ".join(missing))
        print("\nCloudflare dashboard -> R2 -> Manage API tokens -> Create token")
        print("(Object Read & Write, scoped to the dancesage bucket).")
        return 1

    os.environ["STORAGE_BACKEND"] = "r2"
    from .storage import get_storage

    st = get_storage()
    key = f"_healthcheck/{int(time.time())}"
    payload = {"fps": 30, "frames": 1, "j": [[[[0.123456789, 1.0, -0.5]]]]}

    print(f"bucket: {st.bucket}")
    st.put_pose(key, payload)
    print("  write            ok")

    back = st.get_pose(key)
    assert back["j"][0][0][0][0] == 0.123, back
    print("  read back        ok (rounded, gzipped, inflated)")

    url = st.pose_url(key)
    kind = "custom domain" if st.public_base else f"presigned, expires in {st.ttl}s"
    with urllib.request.urlopen(url, timeout=15) as r:
        assert r.status == 200
    print(f"  fetch over HTTP  ok ({kind})")

    st.s3.delete_object(Bucket=st.bucket, Key=f"pose/{key}.json")
    print("  cleanup          ok")
    print("\nR2 is ready. Set STORAGE_BACKEND=r2 to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
