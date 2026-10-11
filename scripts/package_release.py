"""Copy a source-only release to a new/empty folder and create a GitHub ZIP."""

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("cleanroom", "docs", "scripts", "tests", ".github", "evidence/sample-run", "evidence/pdf-preview")
FILES = ("README.md", "LICENSE", ".gitignore", ".gitattributes", "pyproject.toml", "requirements.txt",
         "requirements-dev.txt", "安装.cmd", "启动.cmd", "复现测试.cmd",
         "evidence/test-results.txt", "evidence/coverage.json", "evidence/验证结论.md",
         "evidence/dependency-audit.json")


def source_files():
    for relative in FILES:
        path = ROOT / relative
        if not path.is_file():
            raise ValueError(f"Missing release artifact: {relative}")
        yield path
    for relative in DIRECTORIES:
        directory = ROOT / relative
        if not directory.is_dir():
            raise ValueError(f"Missing release directory: {relative}")
        yield from (path for path in directory.rglob("*") if path.is_file()
                    and "__pycache__" not in path.parts and path.suffix != ".pyc")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Destination must be new or empty; existing files are never overwritten.")
    paths = sorted(source_files())
    destination.mkdir(parents=True, exist_ok=True)
    for source in paths:
        output = destination / source.relative_to(ROOT)
        output.parent.mkdir(parents=True, exist_ok=True)
        if source.parent.name == "tests" and source.name.startswith("red-"):
            # Failed-import baselines can contain personal installation paths.
            raw = source.read_bytes()
            encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = raw.decode(encoding, errors="replace").replace(str(ROOT), "<project>")
            text = text.replace(str(Path.home()), "<user-home>")
            text = text.replace(Path.home().as_posix(), "<user-home>")
            output.write_text(text, encoding="utf-8")
        else:
            shutil.copyfile(source, output)
    manifest = {path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(destination.rglob("*")) if path.is_file()}
    (destination / "SHA256SUMS.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    archive_path = destination / "Doc-Cleanroom-GitHub.zip"
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in [*manifest, "SHA256SUMS.json"]:
            archive.write(destination / name, "doc-cleanroom/" + name)
    print(json.dumps({"destination": str(destination), "files": len(manifest),
                      "archive": str(archive_path), "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest()},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
