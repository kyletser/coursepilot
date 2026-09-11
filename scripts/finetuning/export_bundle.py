"""Export a clean commit with a verified per-file manifest for offline execution."""

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def export(output):
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("commit all changes before exporting")
    if output.exists():
        raise FileExistsError(output)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    subprocess.run(
        ["git", "archive", "--format=tar", "-o", str(output), commit],
        cwd=ROOT,
        check=True,
    )
    files = {}
    with tarfile.open(output) as archive:
        for member in archive.getmembers():
            if member.isfile():
                files[member.name] = hashlib.sha256(
                    archive.extractfile(member).read()
                ).hexdigest()
    body = json.dumps(
        {"git_commit": commit, "git_dirty": False, "files": files},
        sort_keys=True,
    ).encode()
    with tarfile.open(output, "a") as archive:
        member = tarfile.TarInfo("source_provenance.json")
        member.size = len(body)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(body))
    print(
        json.dumps(
            {
                "commit": commit,
                "files": len(files),
                "archive_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    export(parser.parse_args().output.resolve())
