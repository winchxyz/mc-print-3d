"""Find the slicers installed on this machine and hand a file to one of them.

The point is the last step of the pipeline: after mc-print-3d has written a 3MF/STL, the user
wants to open it in the slicer they already use.  Detection is deliberately conservative - a
:class:`SlicerApp` is only returned when an executable actually exists on disk.

Windows uses the *Uninstall* registry keys plus a list of well known install paths; macOS looks
in ``/Applications``; Linux uses ``$PATH`` and the usual flatpak ids.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, Optional

log = logging.getLogger(__name__)


@dataclass
class SlicerApp:
    """One installed slicer.

    ``exe`` is what :func:`open_in_slicer` runs: an executable path, a macOS ``.app`` bundle, or
    a ``flatpak run <id>`` command line.
    """

    key: str
    name: str
    exe: str
    version: str = ""

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return "%s (%s)" % (self.name, self.exe)


#: key -> display name.
SLICER_NAMES: dict[str, str] = {
    "bambu_studio": "Bambu Studio",
    "orca_slicer": "OrcaSlicer",
    "prusa_slicer": "PrusaSlicer",
    "super_slicer": "SuperSlicer",
    "cura": "UltiMaker Cura",
    "creality_print": "Creality Print",
    "elegoo_slicer": "Elegoo Slicer",
    "anycubic_slicer": "Anycubic Slicer Next",
    "qidi_slicer": "QIDI Slicer",
    "flashforge_orca": "Orca-Flashforge",
}

#: Substrings looked for in a registry ``DisplayName`` (matched against the name with every
#: non-alphanumeric character removed and lower-cased), most specific first so ``SuperSlicer``
#: does not become ``PrusaSlicer`` and the Electron app called plain "Orca" is not mistaken for
#: OrcaSlicer.
_REGISTRY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bambu_studio", ("bambustudio",)),
    ("flashforge_orca", ("orcaflashforge", "flashforgeorca")),
    ("orca_slicer", ("orcaslicer",)),
    ("super_slicer", ("superslicer",)),
    ("prusa_slicer", ("prusaslicer",)),
    ("creality_print", ("crealityprint",)),
    ("elegoo_slicer", ("elegooslicer",)),
    ("anycubic_slicer", ("anycubicslicer",)),
    ("qidi_slicer", ("qidislicer", "qidistudio")),
    ("cura", ("ultimakercura", "cura")),
)

#: Executable basenames to look for inside a registry ``InstallLocation``.
_EXE_NAMES: dict[str, tuple[str, ...]] = {
    "bambu_studio": ("bambu-studio.exe", "BambuStudio.exe"),
    "orca_slicer": ("orca-slicer.exe", "OrcaSlicer.exe"),
    "prusa_slicer": ("prusa-slicer.exe", "PrusaSlicer.exe"),
    "super_slicer": ("superslicer.exe", "SuperSlicer.exe"),
    "cura": ("UltiMaker-Cura.exe", "Ultimaker-Cura.exe", "Cura.exe"),
    "creality_print": ("Creality Print.exe", "CrealityPrint.exe"),
    "elegoo_slicer": ("elegoo-slicer.exe", "ElegooSlicer.exe"),
    "anycubic_slicer": ("anycubic-slicer.exe", "AnycubicSlicerNext.exe"),
    "qidi_slicer": ("qidi-slicer.exe", "QIDISlicer.exe"),
    "flashforge_orca": ("orca-flashforge.exe", "Orca-Flashforge.exe"),
}

#: macOS bundle name -> key.
_MAC_BUNDLES: tuple[tuple[str, str], ...] = (
    ("BambuStudio.app", "bambu_studio"),
    ("Bambu Studio.app", "bambu_studio"),
    ("OrcaSlicer.app", "orca_slicer"),
    ("PrusaSlicer.app", "prusa_slicer"),
    ("SuperSlicer.app", "super_slicer"),
    ("UltiMaker-Cura.app", "cura"),
    ("Ultimaker Cura.app", "cura"),
    ("ELEGOO Slicer.app", "elegoo_slicer"),
    ("QIDISlicer.app", "qidi_slicer"),
)

#: Linux: command name in ``$PATH`` -> key.
_LINUX_COMMANDS: tuple[tuple[str, str], ...] = (
    ("bambu-studio", "bambu_studio"),
    ("orca-slicer", "orca_slicer"),
    ("orcaslicer", "orca_slicer"),
    ("prusa-slicer", "prusa_slicer"),
    ("superslicer", "super_slicer"),
    ("cura", "cura"),
    ("UltiMaker-Cura", "cura"),
)

#: Linux: flatpak application id -> key.
_FLATPAK_IDS: tuple[tuple[str, str], ...] = (
    ("com.bambulab.BambuStudio", "bambu_studio"),
    ("io.github.softfever.OrcaSlicer", "orca_slicer"),
    ("com.prusa3d.PrusaSlicer", "prusa_slicer"),
    ("com.ultimaker.cura", "cura"),
)


def _program_dirs() -> list[str]:
    """The ``Program Files`` directories that exist on this Windows machine."""
    names = ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
    dirs: list[str] = []
    for name in names:
        value = os.environ.get(name)
        if value and value not in dirs and os.path.isdir(value):
            dirs.append(value)
    default = r"C:\Program Files"
    if not dirs and os.path.isdir(default):
        dirs.append(default)
    return dirs


def _windows_probe_patterns() -> list[tuple[str, str]]:
    """``(key, glob pattern)`` pairs for the standard Windows install locations."""
    patterns: list[tuple[str, str]] = []
    for base in _program_dirs():
        patterns += [
            ("bambu_studio", os.path.join(base, "Bambu Studio", "bambu-studio.exe")),
            ("orca_slicer", os.path.join(base, "OrcaSlicer", "orca-slicer.exe")),
            ("prusa_slicer", os.path.join(base, "Prusa3D", "PrusaSlicer", "prusa-slicer.exe")),
            ("super_slicer", os.path.join(base, "SuperSlicer", "superslicer.exe")),
            ("cura", os.path.join(base, "UltiMaker Cura*", "UltiMaker-Cura.exe")),
            ("cura", os.path.join(base, "Ultimaker Cura*", "Ultimaker-Cura.exe")),
            ("creality_print", os.path.join(base, "Creality", "Creality Print*",
                                            "Creality Print.exe")),
            ("creality_print", os.path.join(base, "Creality Print*", "Creality Print.exe")),
            ("elegoo_slicer", os.path.join(base, "ELEGOO Slicer", "elegoo-slicer.exe")),
            ("qidi_slicer", os.path.join(base, "QIDISlicer", "qidi-slicer.exe")),
            ("anycubic_slicer", os.path.join(base, "Anycubic Slicer Next",
                                             "anycubic-slicer.exe")),
            ("flashforge_orca", os.path.join(base, "Orca-Flashforge", "orca-flashforge.exe")),
        ]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        programs = os.path.join(local, "Programs")
        patterns += [
            ("orca_slicer", os.path.join(programs, "OrcaSlicer", "orca-slicer.exe")),
            ("bambu_studio", os.path.join(programs, "Bambu Studio", "bambu-studio.exe")),
            ("prusa_slicer", os.path.join(programs, "PrusaSlicer", "prusa-slicer.exe")),
        ]
    return patterns


def _clean_display_icon(value: str) -> str:
    """``'C:\\...\\app.exe,0'`` -> ``'C:\\...\\app.exe'``."""
    text = (value or "").strip().strip('"')
    if text.lower().endswith((",0", ",1", ",2")):
        text = text.rsplit(",", 1)[0].strip()
    return text


def _key_for_display_name(display_name: str) -> Optional[str]:
    """Match a registry ``DisplayName`` against :data:`_REGISTRY_PATTERNS`.

    Spaces, hyphens and version numbers are dropped first so that "Orca-Flashforge" and
    "UltiMaker Cura 5.7.1" both match, while the unrelated Electron app named "Orca" does not.
    """
    squashed = re.sub(r"[^a-z0-9]+", "", (display_name or "").lower())
    if not squashed:
        return None
    for key, needles in _REGISTRY_PATTERNS:
        for needle in needles:
            if needle in squashed:
                return key
    return None


def _exe_from_registry(key: str, install_location: str, display_icon: str) -> str:
    """Best guess at the executable of an uninstall entry (``''`` when nothing exists)."""
    icon = _clean_display_icon(display_icon)
    if icon.lower().endswith(".exe") and os.path.isfile(icon):
        return icon
    location = (install_location or "").strip().strip('"')
    if location and os.path.isdir(location):
        for name in _EXE_NAMES.get(key, ()):
            candidate = os.path.join(location, name)
            if os.path.isfile(candidate):
                return candidate
        try:
            for entry in sorted(os.listdir(location)):
                if entry.lower().endswith(".exe") and "uninstall" not in entry.lower():
                    candidate = os.path.join(location, entry)
                    if os.path.isfile(candidate):
                        return candidate
        except OSError:
            pass
    if icon and os.path.isfile(icon):
        return icon
    return ""


def _iter_registry_entries() -> Iterable[tuple[str, str, str, str]]:
    """Yield ``(display_name, install_location, display_icon, version)`` from Uninstall keys."""
    try:
        import winreg
    except Exception:  # pragma: no cover - non-Windows
        return
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,
         r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for root, path in roots:
        try:
            base = winreg.OpenKey(root, path)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(base, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(base, sub_name) as sub:
                        def read(name: str) -> str:
                            try:
                                return str(winreg.QueryValueEx(sub, name)[0] or "")
                            except OSError:
                                return ""
                        display_name = read("DisplayName")
                        if not display_name:
                            continue
                        yield (display_name, read("InstallLocation"),
                               read("DisplayIcon"), read("DisplayVersion"))
                except OSError:
                    continue
        finally:
            try:
                base.Close()
            except OSError:  # pragma: no cover - defensive
                pass


def _find_windows() -> list[SlicerApp]:
    found: dict[str, SlicerApp] = {}
    for display_name, location, icon, version in _iter_registry_entries():
        key = _key_for_display_name(display_name)
        if not key:
            continue
        exe = _exe_from_registry(key, location, icon)
        if not exe:
            continue
        found.setdefault(key, SlicerApp(key=key, name=SLICER_NAMES.get(key, display_name),
                                        exe=exe, version=version))
    for key, pattern in _windows_probe_patterns():
        if key in found:
            continue
        for match in sorted(glob.glob(pattern)):
            if os.path.isfile(match):
                found[key] = SlicerApp(key=key, name=SLICER_NAMES.get(key, key), exe=match)
                break
    return list(found.values())


def _find_macos() -> list[SlicerApp]:
    found: dict[str, SlicerApp] = {}
    roots = ["/Applications", os.path.expanduser("~/Applications")]
    for root in roots:
        for bundle, key in _MAC_BUNDLES:
            if key in found:
                continue
            path = os.path.join(root, bundle)
            if os.path.isdir(path):
                found[key] = SlicerApp(key=key, name=SLICER_NAMES.get(key, key), exe=path)
    return list(found.values())


def _find_linux() -> list[SlicerApp]:
    found: dict[str, SlicerApp] = {}
    for command, key in _LINUX_COMMANDS:
        if key in found:
            continue
        path = shutil.which(command)
        if path:
            found[key] = SlicerApp(key=key, name=SLICER_NAMES.get(key, key), exe=path)
    if shutil.which("flatpak"):
        installed = _flatpak_installed()
        for app_id, key in _FLATPAK_IDS:
            if key in found or app_id not in installed:
                continue
            found[key] = SlicerApp(key=key, name=SLICER_NAMES.get(key, key),
                                   exe="flatpak run %s" % app_id)
    return list(found.values())


def _flatpak_installed() -> set[str]:
    """Application ids reported by ``flatpak list`` (empty set on any failure)."""
    try:
        out = subprocess.run(["flatpak", "list", "--app", "--columns=application"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return set()
    if out.returncode != 0:
        return set()
    return {line.strip() for line in (out.stdout or "").splitlines() if line.strip()}


def find_slicers() -> list[SlicerApp]:
    """Return the slicers installed on this machine, sorted by display name.

    Never raises: a broken registry entry or an unreadable directory only means that slicer is
    not reported.
    """
    try:
        if sys.platform.startswith("win"):
            apps = _find_windows()
        elif sys.platform == "darwin":
            apps = _find_macos()
        else:
            apps = _find_linux()
    except Exception:  # pragma: no cover - detection must never break the caller
        log.debug("slicer detection failed", exc_info=True)
        return []
    apps.sort(key=lambda a: a.name.lower())
    return apps


def find_slicer(key: str) -> Optional[SlicerApp]:
    """Return the installed slicer with this key, or ``None``."""
    for app in find_slicers():
        if app.key == key:
            return app
    return None


def open_in_slicer(app: SlicerApp, path: str) -> None:
    """Open *path* in *app*, detached from this process.

    Raises :class:`FileNotFoundError` when the file does not exist and :class:`OSError` when the
    slicer cannot be started.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    exe = (app.exe or "").strip()
    if not exe:
        raise OSError("no executable recorded for %s" % app.name)

    if exe.startswith("flatpak "):
        argv = shlex.split(exe) + [os.path.abspath(path)]
    elif sys.platform == "darwin" and exe.endswith(".app"):
        argv = ["open", "-a", exe, os.path.abspath(path)]
    else:
        argv = [exe, os.path.abspath(path)]

    kwargs: dict = {"close_fds": True}
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL

    log.info("opening %s in %s", path, app.name)
    subprocess.Popen(argv, **kwargs)
