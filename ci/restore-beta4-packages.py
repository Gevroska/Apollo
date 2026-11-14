"""Restore the signed beta.4 package snapshot in a disposable Windows CI runner."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

MANIFEST_URL = "https://github.com/Gevroska/Apollo/releases/download/v0.4.7-beta.4/windows-packages.txt"
MANIFEST_SHA256 = "763f89fe934b534f8aaa4d3e82bdb3063e4dd75c78123ad69e763896b0a68197"


def inventory(text):
    packages = {}
    for line in text.splitlines():
        if not re.fullmatch(r"[A-Za-z0-9+_.-]+ [A-Za-z0-9+_.:~\-]+", line):
            raise RuntimeError(f"Invalid package inventory line: {line!r}")
        name, version = line.split()
        if name in packages:
            raise RuntimeError(f"Duplicate package: {name}")
        packages[name] = version
    if not packages:
        raise RuntimeError("Empty package inventory")
    return packages


def restore():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows":
        raise RuntimeError("Package restoration is restricted to disposable Windows CI runners")
    root = Path(os.environ["APOLLO_MSYS2_ROOT"]).resolve(strict=True)
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    if not root.is_relative_to(runner_temp):
        raise RuntimeError("MSYS2 must be installed inside the disposable runner temporary directory")
    pacman = root / "usr/bin/pacman.exe"
    manifest = urllib.request.urlopen(MANIFEST_URL, timeout=60).read()
    if hashlib.sha256(manifest).hexdigest() != MANIFEST_SHA256:
        raise RuntimeError("Beta.4 package manifest checksum mismatch")
    expected = inventory(manifest.decode("utf-8"))

    def installed():
        return inventory(subprocess.check_output([str(pacman), "-Q"], text=True, encoding="utf-8"))

    actual = installed()
    changed = {name: version for name, version in expected.items() if actual.get(name) != version}
    extras = sorted(actual.keys() - expected.keys())
    if not changed and not extras:
        print("Windows packages already match beta.4.")
        return
    with tempfile.TemporaryDirectory(prefix="apollo-beta4-packages-", dir=runner_temp) as directory:
        cache = Path(directory)

        def download(item):
            name, version = item
            # Epochs are recorded by pacman but omitted from archive filenames.
            archive_version = re.split(r"[:~]", version, maxsplit=1)[-1]
            repository = "mingw/ucrt64" if name.startswith("mingw-w64-ucrt-x86_64-") else "msys/x86_64"
            architectures = ("any",) if repository == "mingw/ucrt64" else ("x86_64", "any")
            for architecture in architectures:
                for compression in ("zst", "xz"):
                    filename = f"{name}-{archive_version}-{architecture}.pkg.tar.{compression}"
                    url = f"https://repo.msys2.org/{repository}/{filename}"
                    target = cache / filename
                    try:
                        with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as output:
                            shutil.copyfileobj(response, output)
                        with urllib.request.urlopen(url + ".sig", timeout=60) as response:
                            target.with_name(filename + ".sig").write_bytes(response.read())
                    except urllib.error.HTTPError as error:
                        if error.code != 404:
                            raise
                        continue
                    print(f"Restoring {name} {version}", flush=True)
                    return str(target)
            raise RuntimeError(f"Signed beta.4 package is unavailable: {name} {version}")

        # Download every required archive and signature before changing the runner.
        with ThreadPoolExecutor(max_workers=8) as pool:
            archives = list(pool.map(download, changed.items()))
        config = (root / "etc/pacman.conf").read_text(encoding="utf-8")
        config = re.sub(r"(?m)^\s*LocalFileSigLevel\s*=.*$", "LocalFileSigLevel = Required", config)
        if "LocalFileSigLevel = Required" not in config:
            config = config.replace("[options]", "[options]\nLocalFileSigLevel = Required", 1)
        config_path = cache / "pacman.conf"
        config_path.write_text(config, encoding="utf-8")
        # Renamed packages must leave before installing their beta.4 counterparts.
        # Dependency consistency is checked after the complete snapshot is restored.
        if extras:
            subprocess.run([str(pacman), "--noconfirm", "-Rdd", *extras], check=True)
        if archives:
            subprocess.run([str(pacman), "--config", str(config_path), "--noconfirm", "-U", *archives], check=True)
    if installed() != expected:
        raise RuntimeError("Restored Windows package inventory differs from beta.4")
    subprocess.run([str(pacman), "-Dk"], check=True)
    print("Signed Windows package snapshot matches beta.4 exactly.")


if __name__ == "__main__":
    restore()
