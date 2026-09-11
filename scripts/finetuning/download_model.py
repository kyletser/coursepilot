"""Download a pinned safetensors snapshot, verifying upstream file hashes.

Uses only public model files, never authentication credentials. An optional mirror
must return the same pinned revision; every LFS object is verified by SHA256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODEL = "Qwen/Qwen3-4B"
REVISION = "1cfa9a7208912126459214e8b04321603b3df60c"


def fetch_file(
    endpoint: str, root: Path, item: dict, download_revision: str, model: str = MODEL
) -> dict:
    name = item["rfilename"]
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    lfs = item.get("lfs")

    def valid(path: Path) -> bool:
        if not path.is_file():
            return False
        if lfs and path.stat().st_size != lfs["size"]:
            return False
        digest = hashlib.sha256() if lfs else hashlib.sha1()
        if not lfs:
            digest.update(f"blob {path.stat().st_size}\0".encode())
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest() == (lfs["sha256"] if lfs else item["blobId"])

    if valid(target):
        print(f"verified cached {name}", flush=True)
        return {"name": name, "bytes": target.stat().st_size, "verified": True}
    partial = target.with_name(target.name + ".partial")
    for attempt in range(8):
        try:
            start = partial.stat().st_size if partial.exists() else 0
            request = urllib.request.Request(
                f"{endpoint}/{model}/resolve/{download_revision}/{name}",
                headers={
                    "User-Agent": "CoursePilot-model-download/1.0",
                    **({"Range": f"bytes={start}-"} if start else {}),
                },
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                resume = start > 0 and response.status == 206
                if resume and not response.headers.get("Content-Range", "").startswith(
                    f"bytes {start}-"
                ):
                    raise ValueError("unexpected resume range")
                with partial.open("ab" if resume else "wb") as output:
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        output.write(block)
            if not valid(partial):
                # A fully downloaded but corrupt object must not be appended to.
                bad = partial.with_name(partial.name + f".invalid-{time.time_ns()}")
                partial.rename(bad)
                raise ValueError(f"hash mismatch: {name}")
            os.replace(partial, target)
            print(f"downloaded and verified {name}", flush=True)
            return {"name": name, "bytes": target.stat().st_size, "verified": True}
        except Exception as exc:
            print(
                f"retry {attempt + 1} {name}: {type(exc).__name__}: {exc}", flush=True
            )
            if attempt == 7:
                raise
            time.sleep(min(30, 2**attempt))
    raise RuntimeError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--endpoint", default="https://huggingface.co")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--revision", default=REVISION)
    parser.add_argument("--include-pytorch-bin", action="store_true")
    parser.add_argument(
        "--download-revision",
        default=REVISION,
        help="Transport ref only; all files still verified against pinned hashes",
    )
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    if metadata["sha"] != args.revision or metadata["id"] != args.model:
        raise ValueError("upstream snapshot does not match pinned model/revision")
    files = [
        item
        for item in metadata["siblings"]
        if (
            "/" not in item["rfilename"] or item["rfilename"] == "1_Pooling/config.json"
        )
        and (
            item["rfilename"].endswith((".safetensors", ".json", ".txt", ".model"))
            or item["rfilename"] in {"LICENSE", "README.md"}
            or (args.include_pytorch_bin and item["rfilename"] == "pytorch_model.bin")
        )
    ]
    args.root.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: fetch_file(
                    args.endpoint.rstrip("/"),
                    args.root,
                    item,
                    args.download_revision,
                    args.model,
                ),
                files,
            )
        )
    manifest = {
        "model": args.model,
        "revision": args.revision,
        "files": results,
        "download_endpoint": args.endpoint,
        "completed_at": time.time(),
    }
    (args.root / "download_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print("DOWNLOAD_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
