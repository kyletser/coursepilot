"""Archive audit records without copying full external source passages or weights."""

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path


def compact_external(record):
    record = json.loads(json.dumps(record))
    for evidence in record["response"].get("evidence", []):
        text = evidence.pop("text")
        evidence["text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    for citation in record["response"].get("citations", []):
        quote = citation.pop("quote")
        citation["quote_sha256"] = hashlib.sha256(quote.encode()).hexdigest()
    return record


def archive(args):
    members = {}
    for directory in (args.evidence, args.agent):
        for path in sorted(directory.rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix in {".json", ".jsonl", ".log"}
            ):
                members[
                    directory.name + "/" + path.relative_to(directory).as_posix()
                ] = path.read_bytes()
    originals = {}
    for directory in args.external:
        for filename in ("provenance.json", "summary.json"):
            members[directory.name + "/" + filename] = (
                directory / filename
            ).read_bytes()
        raw = (directory / "outputs.jsonl").read_bytes()
        originals[directory.name] = hashlib.sha256(raw).hexdigest()
        rows = [
            compact_external(json.loads(line)) for line in raw.decode().splitlines()
        ]
        members[directory.name + "/outputs-compact.jsonl"] = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        ).encode()
    manifest = {
        "schema": "coursepilot.finetuning-archive/1",
        "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in members.items()
        },
        "external_original_output_sha256": originals,
        "external_source_passages": "omitted; original full records retained in ignored experiment runs",
        "weights_and_credentials": "not included",
        "external_text_license": "CMRC-derived keys and generated excerpts: CC-BY-SA-4.0; https://github.com/ymcui/cmrc2018",
    }
    members["archive-manifest.json"] = json.dumps(manifest, indent=2).encode()
    with tarfile.open(args.output, "x:gz") as output:
        for name, data in sorted(members.items()):
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(data), 0o644
            output.addfile(item, io.BytesIO(data))
    with tarfile.open(args.output) as check:
        for name, expected in manifest["files"].items():
            if hashlib.sha256(check.extractfile(name).read()).hexdigest() != expected:
                raise ValueError("archive verification failed")
    print(
        json.dumps(
            {
                "files": len(members),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--external", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    archive(parser.parse_args())
