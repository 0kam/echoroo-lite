#!/usr/bin/env python3
"""Write latest.json, the update manifest the installed app reads from the latest release.

Usage:
    make_update_manifest.py ASSETS_DIR TAG    writes ASSETS_DIR/latest.json
    make_update_manifest.py --self-check
"""

import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DOWNLOAD = "https://github.com/0kam/echoroo-lite/releases/download"

# Updater target -> ending of the one staged file that target downloads.
# The updater plugin looks up "<os>-<arch>-<bundle type>" first, then "<os>-<arch>".
# Only a .deb install needs its own entry: it cannot install the AppImage.
# ponytail: one arch per OS; add keys here when a second arch is built.
PLATFORMS = {
    "windows-x86_64": "-setup.exe",
    "darwin-aarch64": ".app.tar.gz",
    "linux-x86_64": ".AppImage",
    "linux-x86_64-deb": ".deb",
}


def build(assets: Path, tag: str) -> dict:
    version = tag.removeprefix("v")
    platforms = {}
    for target, ending in PLATFORMS.items():
        found = sorted(p.name for p in assets.iterdir() if p.name.endswith(ending))
        if len(found) != 1:
            sys.exit(f"{target}: expected one *{ending} in {assets}, found {found}")
        name = found[0]
        # GitHub rewrites other characters in asset names, so the URL would not match.
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            sys.exit(f"{target}: GitHub would rename the asset {name!r}")
        # A tag that differs from the app version would offer the same update forever.
        if f"_{version}_" not in name:
            sys.exit(f"{target}: {name} is not version {version} (tag {tag})")
        sig = assets / f"{name}.sig"
        signature = sig.read_text().strip() if sig.is_file() else ""
        if not signature:
            sys.exit(f"{target}: signature {sig.name} is missing or empty")
        platforms[target] = {
            "url": f"{DOWNLOAD}/{tag}/{name}",
            "signature": signature,
            "size": (assets / name).stat().st_size,  # not read by the plugin; the app shows it
        }
    return {
        "version": version,
        "pub_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platforms": platforms,
    }


def self_check() -> None:
    def fails(assets: Path, tag: str = "v0.2.0") -> bool:
        try:
            build(assets, tag)
        except SystemExit:
            return True
        return False

    with tempfile.TemporaryDirectory() as tmp:
        assets = Path(tmp)
        names = [
            "Echoroo.Lite_0.2.0_x64-setup.exe",
            "Echoroo.Lite_0.2.0_aarch64.dmg",
            "Echoroo.Lite_0.2.0_aarch64-update.app.tar.gz",
            "Echoroo.Lite_0.2.0_amd64.AppImage",
            "Echoroo.Lite_0.2.0_amd64.deb",
        ]
        for name in names:
            (assets / name).write_bytes(b"x" * len(name))
            if not name.endswith(".dmg"):
                (assets / f"{name}.sig").write_text(f"sig-of-{name}\n")

        manifest = build(assets, "v0.2.0")
        print(json.dumps(manifest, indent=2))
        assert manifest["version"] == "0.2.0"
        assert datetime.fromisoformat(manifest["pub_date"]).utcoffset() is not None
        assert set(manifest["platforms"]) == set(PLATFORMS)
        deb = manifest["platforms"]["linux-x86_64-deb"]
        assert deb == {
            "url": f"{DOWNLOAD}/v0.2.0/Echoroo.Lite_0.2.0_amd64.deb",
            "signature": "sig-of-Echoroo.Lite_0.2.0_amd64.deb",
            "size": len("Echoroo.Lite_0.2.0_amd64.deb"),
        }
        assert manifest["platforms"]["linux-x86_64"]["url"].endswith(".AppImage")
        assert manifest["platforms"]["darwin-aarch64"]["url"].endswith("-update.app.tar.gz")

        assert fails(assets, "v0.3.0"), "a tag that differs from the file version must fail"
        spaced = assets / "Echoroo Lite_0.2.0_x64-setup.exe"
        (assets / names[0]).rename(spaced)
        assert fails(assets), "a name GitHub would rewrite must fail"
        spaced.rename(assets / names[0])
        (assets / f"{names[4]}.sig").write_text("")
        assert fails(assets), "an empty signature must fail"
        (assets / f"{names[4]}.sig").unlink()
        assert fails(assets), "a missing signature must fail"
        (assets / f"{names[4]}.sig").write_text("sig")
        (assets / names[3]).unlink()
        assert fails(assets), "a missing platform must fail"
    print("self-check passed")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        self_check()
    elif len(sys.argv) == 3:
        out = Path(sys.argv[1]) / "latest.json"
        out.write_text(json.dumps(build(Path(sys.argv[1]), sys.argv[2]), indent=2) + "\n")
        print(out.read_text(), end="")
    else:
        sys.exit(__doc__)
