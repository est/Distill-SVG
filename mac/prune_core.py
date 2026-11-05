#!/usr/bin/env python3
"""Safely prune unused Pillow plugins and shared libraries from a frozen core.

The DistillSVG core only uses Pillow for text rendering::

    from PIL import Image, ImageDraw, ImageFont

It never uses Pillow to *decode* images (OpenCV performs all image I/O), so the
AVIF/WebP/colour-management/Tk plugins and their Pillow-private dylibs are dead
weight.  Libraries that OpenCV also links against (for example liblcms2) are
kept even when the Pillow plugin that introduced them is removed.

This script is intentionally conservative:

* it starts from the extension modules that are actually imported, plus every
  Pillow plugin that is not on the explicit denylist, and walks ``otool -L``;
* it removes only a small, tested plugin denylist;
* it removes only dylibs in ``PIL/.dylibs`` that are unreachable from the
  remaining modules;
* it refuses to remove ``_imaging``/``_imagingft`` or any dylib in their
  closure;
* ``--dry-run`` reports what would happen without touching the tree.

It does not sign the result.  If the input is part of an app bundle, re-sign
afterwards (see mac/build_app.sh).

Usage::

    python3 mac/prune_core.py "dist/DistillSVG.app"
    python3 mac/prune_core.py build/core/dist/distill-core/_internal
    python3 mac/prune_core.py --dry-run "dist/DistillSVG.app"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Plugins that the pipeline never imports.  Keep this list deliberately small
# and evidence-based: each entry was removed and a full conversion was run.
PLUGIN_DENYLIST = (
    "_avif",
    "_webp",
    "_imagingcms",
    "_imagingtk",
    "_imagingmath",  # ImageMath is not used by ImageDraw/font fitting.
)

# These modules are required.  Never remove them, even if a future denylist or
# reachability calculation is wrong.
PROTECTED_PLUGINS = ("_imaging", "_imagingft")


class PruneError(RuntimeError):
    pass


def find_internal_dir(path: Path) -> Path:
    """Return the PyInstaller ``_internal`` directory for an app/core path."""
    path = path.expanduser().resolve()
    candidates = []
    if path.suffix == ".app" or (path / "Contents").is_dir():
        candidates.append(path / "Contents" / "Resources" / "core" / "_internal")
        candidates.append(path / "Contents" / "Resources" / "core")
    candidates.extend([path / "_internal", path])

    for candidate in candidates:
        if (candidate / "PIL").is_dir():
            return candidate
    raise PruneError(f"cannot find _internal/PIL below: {path}")


def otool_dependencies(path: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["otool", "-L", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - macOS only
        raise PruneError("otool not found; install Xcode command line tools") from exc
    if proc.returncode != 0:
        raise PruneError(f"otool failed for {path}:\n{proc.stderr.strip()}")

    deps = []
    for line in proc.stdout.splitlines()[1:]:
        text = line.strip()
        if not text:
            continue
        # Lines look like: "\t@rpath/libfoo.dylib (compatibility version ...)"
        token = text.split(" (compatibility version", 1)[0].strip()
        if token:
            deps.append(token)
    return deps


def is_system_dependency(token: str) -> bool:
    return token.startswith(("/usr/lib/", "/System/Library/"))


def plugin_stem(path: Path) -> str:
    """'_imaging.cpython-314-darwin.so' -> '_imaging'."""
    return path.name.split(".", 1)[0]


def is_denylisted(path: Path) -> bool:
    return plugin_stem(path) in PLUGIN_DENYLIST


def is_protected_plugin(path: Path) -> bool:
    return plugin_stem(path) in PROTECTED_PLUGINS


def find_external_users(
    internal_dir: Path, pil_dir: Path, dylib_names: set[str]
) -> dict[str, list[str]]:
    """Find non-Pillow Mach-O files that reference a PIL/.dylibs basename.

    PyInstaller creates top-level symlinks such as _internal/liblcms2.2.dylib
    because OpenCV's libjxl_cms also links against it.  Those libraries are not
    Pillow-private and must not be removed just because Pillow's plugin that
    originally pulled them in is unused.
    """
    pil_root = pil_dir.resolve()
    users: dict[str, list[str]] = {}
    for root, dirs, files in os.walk(internal_dir):
        root_path = Path(root)
        # Do not treat Pillow's own modules as "external"; they are seeds.
        if root_path.resolve() == pil_root:
            dirs[:] = []
            continue
        for name in files:
            path = root_path / name
            if path.is_symlink():
                continue
            if not (name.endswith((".so", ".dylib")) or os.access(path, os.X_OK)):
                continue
            try:
                deps = otool_dependencies(path)
            except PruneError:
                continue
            for token in deps:
                base = os.path.basename(token)
                if base in dylib_names:
                    users.setdefault(base, []).append(str(path.relative_to(internal_dir)))
    return users


def reachable_dylibs(pil_dir: Path, seeds: list[Path]) -> tuple[set[str], dict[str, list[str]]]:
    """Return reachable dylib basenames and a small dependency trace."""
    dylib_dir = pil_dir / ".dylibs"
    available = {}
    if dylib_dir.is_dir():
        for entry in dylib_dir.iterdir():
            if entry.is_file() or entry.is_symlink():
                available[entry.name] = entry
                # Versioned symlinks (libfoo.dylib -> libfoo.1.dylib) resolve by
                # their own basename too.
                real = entry.resolve()
                if real.is_file():
                    available.setdefault(real.name, entry)

    reachable: set[str] = set()
    trace: dict[str, list[str]] = {}
    queue = list(seeds)
    seen_files: set[str] = set()

    while queue:
        current = queue.pop()
        key = str(current)
        if key in seen_files:
            continue
        seen_files.add(key)

        deps = otool_dependencies(current)
        trace[str(current)] = deps
        for token in deps:
            if is_system_dependency(token):
                continue
            name = os.path.basename(token)
            if name in reachable:
                continue
            target = available.get(name)
            if target is None:
                # A dependency may live beside the plugin rather than in
                # .dylibs; follow it only if it is a real file in PIL/.
                beside = pil_dir / name
                if beside.is_file():
                    target = beside
                else:
                    continue
            reachable.add(name)
            queue.append(target)

    return reachable, trace


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"


def file_size(path: Path) -> int:
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def prune_plugins(pil_dir: Path, dry_run: bool) -> list[tuple[Path, int]]:
    removed = []
    for entry in sorted(pil_dir.iterdir()):
        if not entry.is_file() or not entry.name.endswith(".so"):
            continue
        if is_protected_plugin(entry):
            continue
        if not is_denylisted(entry):
            continue
        size = file_size(entry)
        removed.append((entry, size))
        if not dry_run:
            entry.unlink()
    return removed


def prune_dylibs(
    pil_dir: Path, reachable: set[str], protected: set[str], dry_run: bool
) -> list[tuple[Path, int]]:
    removed = []
    dylib_dir = pil_dir / ".dylibs"
    if not dylib_dir.is_dir():
        return removed
    for entry in sorted(dylib_dir.iterdir()):
        if not (entry.is_file() or entry.is_symlink()):
            continue
        # Follow symlinks so a versioned alias of a protected library is kept.
        try:
            real_name = entry.resolve().name
        except OSError:
            real_name = entry.name
        names = {entry.name, real_name}
        if names & protected:
            continue
        if names & reachable:
            continue
        size = file_size(entry)
        removed.append((entry, size))
        if not dry_run:
            entry.unlink()
    return removed


def prune_dangling_symlinks(
    internal_dir: Path, removed_targets: set[Path], dry_run: bool
) -> list[Path]:
    """Remove top-level PyInstaller aliases that pointed into PIL/.dylibs."""
    removed = []
    pil_dylibs = (internal_dir / "PIL" / ".dylibs").resolve()
    for entry in sorted(internal_dir.iterdir()):
        if not entry.is_symlink():
            continue
        try:
            target = entry.resolve()
        except OSError:
            continue
        try:
            target.relative_to(pil_dylibs)
        except ValueError:
            continue
        if target not in removed_targets and target.exists():
            continue  # still valid: its dylib was kept
        removed.append(entry)
        if not dry_run:
            entry.unlink()
    return removed


def app_bundle_for(internal_dir: Path) -> Path | None:
    for parent in internal_dir.parents:
        if parent.suffix == ".app":
            return parent
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remove unused Pillow plugins/dylibs from a frozen DistillSVG core."
    )
    parser.add_argument(
        "path",
        help="app bundle, core directory, or PyInstaller _internal directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be removed without changing anything",
    )
    args = parser.parse_args(argv)

    try:
        internal_dir = find_internal_dir(Path(args.path))
        pil_dir = internal_dir / "PIL"
        if not pil_dir.is_dir():
            raise PruneError(f"PIL directory not found: {pil_dir}")

        plugins = sorted(p for p in pil_dir.iterdir() if p.is_file() and p.name.endswith(".so"))

        # Protect the required modules and every dylib they need.
        required = [p for p in plugins if is_protected_plugin(p)]
        missing = [
            name for name in PROTECTED_PLUGINS
            if not any(plugin_stem(p) == name for p in required)
        ]
        if missing:
            raise PruneError(
                "refusing to prune: required Pillow module(s) missing: " + ", ".join(missing)
            )

        protected_dylibs, _ = reachable_dylibs(pil_dir, required)

        # Keep every plugin we are not explicitly removing working: include its
        # dylibs in the closure too.  This makes the script safe for bundles
        # with extra Pillow plugins.
        retained_plugins = [p for p in plugins if not is_denylisted(p)]
        dylib_dir = pil_dir / ".dylibs"
        dylib_names = (
            {entry.name for entry in dylib_dir.iterdir() if entry.is_file() or entry.is_symlink()}
            if dylib_dir.is_dir()
            else set()
        )
        external_users = find_external_users(internal_dir, pil_dir, dylib_names)
        external_seeds = sorted(
            {internal_dir / user for users in external_users.values() for user in users}
        )
        reachable, _trace = reachable_dylibs(pil_dir, retained_plugins + external_seeds)

        # Never allow a bug in reachability to delete a required dylib.
        reachable |= protected_dylibs

        removed_plugins = prune_plugins(pil_dir, args.dry_run)
        removed_dylibs = prune_dylibs(
            pil_dir, reachable, protected_dylibs, args.dry_run
        )
        removed_targets = {
            path.resolve() for path, _size in removed_dylibs if path.exists() or path.is_symlink()
        }
        removed_links = prune_dangling_symlinks(
            internal_dir, removed_targets, args.dry_run
        )

        removed_bytes = sum(size for _path, size in removed_plugins)
        removed_bytes += sum(size for _path, size in removed_dylibs)

        verb = "Would remove" if args.dry_run else "Removed"
        print(f"core: {internal_dir}")
        print(f"mode: {'dry-run' if args.dry_run else 'apply'}")
        print()
        print(f"{verb} plugins ({len(removed_plugins)}):")
        for path, size in removed_plugins:
            print(f"  {path.name:<42} {human_bytes(size):>10}")
        if not removed_plugins:
            print("  (none)")

        print(f"{verb} dylibs ({len(removed_dylibs)}):")
        for path, size in removed_dylibs:
            print(f"  {path.name:<42} {human_bytes(size):>10}")
        if not removed_dylibs:
            print("  (none)")

        if removed_links:
            print(f"{verb} dangling aliases ({len(removed_links)}):")
            for path in removed_links:
                print(f"  {path.name}")

        kept = sorted(
            p.name for p in plugins
            if p.name not in {removed.name for removed, _ in removed_plugins}
        )
        print()
        print(f"kept plugins: {', '.join(kept) if kept else '(none)'}")
        print(f"kept dylibs : {len(reachable)} reachable name(s)")
        if external_users:
            print("kept for non-Pillow users:")
            for name in sorted(external_users):
                users = external_users[name]
                print(f"  {name:<35} <- {', '.join(sorted(users)[:3])}"
                      + (" ..." if len(users) > 3 else ""))
        print(f"total       : {human_bytes(removed_bytes)} in {len(removed_plugins) + len(removed_dylibs)} file(s)")

        if not args.dry_run:
            bundle = app_bundle_for(internal_dir)
            target = bundle or internal_dir
            print()
            print(f"NOTE: {target} now has an invalid signature; re-sign before launching.")

        return 0
    except PruneError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
