"""Shared helpers for the customizable chat UI and Archipelago item notification.

This module centralises everything needed to theme the in-game chat panel and
the Archipelago item notification with a colour of the user's choosing and a
font they already have installed on their machine.

The font support reads the Windows font registry (when available) and falls
back to scanning common system font directories so it works on Linux/macOS
too.  ``load_font`` caches the Panda3D ``Font`` objects it creates, so the
same family installed in several weights is only loaded once.
"""
import os
import re
import sys
import itertools
from typing import Dict, List, Optional

from panda3d.core import Vec4

# Sentinel name meaning "use the game's built-in font".  Picking this in the
# options menu keeps whatever font the game normally renders with.
DEFAULT_FONT = "Default"
_CUSTOM_FONT_DIRECTORY = "Custom File..."

# Sentinel stored for a colour option that means "keep following the accent".
# Used by the chat header and the AP channel so they stay in sync with the
# theme/accent unless the player explicitly overrides them.
AUTO_COLOR = "Auto"


_COLOR_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def is_auto_color(value):
    """True when a colour setting holds the 'follow the accent' sentinel."""
    if not isinstance(value, str):
        return False
    return value.strip().lower() in ("", "auto", "default", "use accent")


def normalize_hex(color, fallback: str = "#F5F7FF") -> str:
    """Return a canonical ``#RRGGBB`` string, or ``fallback`` if invalid."""
    if isinstance(color, str):
        match = _COLOR_HEX_RE.match(color.strip())
        if match:
            return "#" + match.group(1).upper()
    return fallback


def parse_hex_color(color, default=Vec4(1, 1, 1, 1)) -> Vec4:
    """Convert a ``#RRGGBB`` color into a Panda3D ``Vec4`` in 0..1 range."""
    if isinstance(color, str):
        match = _COLOR_HEX_RE.match(color.strip())
        if match:
            value = int(match.group(1), 16)
            red = ((value >> 16) & 0xFF) / 255.0
            green = ((value >> 8) & 0xFF) / 255.0
            blue = (value & 0xFF) / 255.0
            return Vec4(red, green, blue, 1.0)
    return Vec4(default)
_FONT_FILE_EXTENSIONS = (".ttf", ".otf", ".ttc")


# ---------------------------------------------------------------------------
# Installed font discovery
# ---------------------------------------------------------------------------

def _font_directories() -> List[str]:
    """Return a list of directories known to hold fonts on this machine."""
    directories = [r"C:\Windows\Fonts", r"C:\Windows\Fonts\Admin"]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            directories.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
        else:
            home = os.path.expanduser("~")
            directories.append(os.path.join(home, "AppData", "Local", "Microsoft", "Windows", "Fonts"))
    elif sys.platform == "darwin":
        home = os.path.expanduser("~")
        directories += ["/System/Library/Fonts", "/Library/Fonts"]
        directories.append(os.path.join(home, "Library", "Fonts"))
    else:  # Linux and friends
        home = os.path.expanduser("~")
        directories += ["/usr/share/fonts", "/usr/local/share/fonts"]
        directories.append(os.path.join(home, ".fonts"))
        directories.append(os.path.join(home, ".local", "share", "fonts"))
    return [d for d in directories if os.path.isdir(d)]


def _windows_registry_fonts() -> Dict[str, str]:
    """Return ``{display name: font file path}`` from the Windows registry.

    This maps a readable family name (e.g. "Arial (TrueType)") to the file that
    provides it, which gives us much nicer menu labels than raw filenames.
    """
    result: Dict[str, str] = {}
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts",
        )
    except (ImportError, OSError):
        return result

    try:
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            # Values look like "arial.ttf", a full path, or occasionally a
            # comma separated list of files.  Only keep the first real file.
            candidate = str(value)
            first = candidate.split(",")[0].strip()
            if not first.lower().endswith(_FONT_FILE_EXTENSIONS):
                continue
            path = first if os.path.isabs(first) or "/" in first else (
                os.path.join(r"C:\Windows\Fonts", first))
            result[name] = path
    finally:
        try:
            winreg.CloseKey(key)
        except OSError:
            pass
    return result


def _slug(text: str) -> str:
    """Lowercase the text and keep only letters, digits and spaces."""
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def _scan_directory_fonts(directories: List[str]) -> Dict[str, str]:
    """Return ``{filename stem: file path}`` for fonts in the directories."""
    result: Dict[str, str] = {}
    for directory in directories:
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for entry in entries:
            if not entry.lower().endswith(_FONT_FILE_EXTENSIONS):
                continue
            stem = os.path.splitext(entry)[0]
            result[stem.lower()] = os.path.join(directory, entry)
    return result


_font_index = None  # (name_to_path, stem_to_path) built lazily


def _get_font_index():
    """Build (and cache) the combined font lookup tables for this machine."""
    global _font_index
    if _font_index is not None:
        return _font_index

    name_to_path: Dict[str, str] = {}
    stem_to_path: Dict[str, str] = {}

    if sys.platform == "win32":
        for name, path in _windows_registry_fonts().items():
            # Registry names carry suffixes like " (TrueType)" / " (OpenType)".
            clean_name = re.sub(r"\s*\((?:True|Open)Type\s*\)$", "", name).strip()
            if clean_name not in name_to_path:
                name_to_path[clean_name] = path
                stem_to_path[_slug(os.path.splitext(os.path.basename(path))[0])] = path

    scanned = _scan_directory_fonts(_font_directories())
    # Fill any gaps left by the registry (covers Linux/macOS and custom dirs).
    for directory in _font_directories():
        if os.path.isdir(directory):
            for entry in os.listdir(directory):
                if not entry.lower().endswith(_FONT_FILE_EXTENSIONS):
                    continue
                path = os.path.join(directory, entry)
                if path.lower() in [p.lower() for p in name_to_path.values()]:
                    continue
                stem = os.path.splitext(entry)[0]
                stem_to_path[stem.lower()] = path
                # Derive a readable-ish label from the filename when the
                # registry didn't give us a proper family name.
                pretty = stem.replace("-", " ").replace("_", " ").title().strip()
                if pretty and pretty not in name_to_path:
                    name_to_path[pretty] = path
    stem_to_path.update(scanned)

    _font_index = (name_to_path, stem_to_path)
    return _font_index


def get_installed_fonts() -> List[str]:
    """Return a sorted list of selectable font names for the options menu.

    The first entry is always 'Default', meaning "use the game font".
    """
    name_to_path, _ = _get_font_index()
    names = {DEFAULT_FONT}
    names.update(name_to_path.keys())
    return sorted(names)


def resolve_font_path(name: str) -> Optional[str]:
    """Return the file on disk that provides the given font name.

    Accepts a real font family name, a filename stem, or a direct path to a
    ``.ttf``/``.otf``/``.ttc``/``.otf`` file.  Returns ``None`` when the font
    cannot be located (or the caller passed the ``Default`` sentinel).
    """
    if not name or str(name).strip() == DEFAULT_FONT:
        return None

    name = str(name).strip()
    # A direct path to a font file.
    if name.lower().endswith(_FONT_FILE_EXTENSIONS) and os.path.exists(name):
        return name

    name_to_path, stem_to_path = _get_font_index()

    if name in name_to_path:
        return name_to_path[name]

    slug = _slug(name)
    if not slug:
        return None

    # Try exact stem first, then a prefix match (e.g. "Comic Sans" for
    # "comic.ttf").
    if slug in stem_to_path:
        return stem_to_path[slug]
    for stem, path in stem_to_path.items():
        if stem.startswith(slug) or slug.startswith(stem):
            return path

    # Try matching against the readable names once more in case the exact key
    # lookup missed it because of punctuation differences.
    for display_name, path in name_to_path.items():
        if _slug(display_name) == slug:
            return path
    return None


# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

_font_cache: Dict[str, object] = {}


def load_font(name: str):
    """Load (and cache) a Panda3D ``Font`` for ``name``.

    Returns ``None`` for the ``Default`` sentinel, meaning "leave the standard
    game font in place".  If the requested font cannot be found or fails to
    load we also return ``None`` rather than crash, so the UI never breaks from
    a bad font choice.
    """
    name = str(name) if name is not None else DEFAULT_FONT
    if name == DEFAULT_FONT:
        return None
    if name in _font_cache:
        return _font_cache[name]

    path = resolve_font_path(name)
    font = None
    if path:
        try:
            font = loader.loadFont(path)
        except Exception:
            font = None

    # Cache both successful loads and failures (as None) so we don't retry the
    # same missing file every settings refresh.
    _font_cache[name] = font
    return font


def apply_font(widget, name: str) -> None:
    """Apply the configured font to a DirectGui widget.

    When the font is the built-in one (or could not be loaded) the widget is
    left alone so the game's own font controls it.
    """
    font = load_font(name)
    if font is not None and widget is not None:
        try:
            widget["text_font"] = font
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

# A handful of pleasing presets for each role; users can also type any hex.
TEXT_COLOR_PRESETS = [
    "#FFFFFF", "#F5F7FF", "#E5EFFF", "#D9F2E3", "#FFF3D6",
    "#FFD9D9", "#EAD9FF", "#CFF5FF", "#000000",
]
BACKGROUND_COLOR_PRESETS = [
    "#0B132B", "#051630", "#12121F", "#10221A", "#2A1A0B",
    "#1A1A1A", "#202040", "#3A1033", "#FFFFFF",
]
ACCENT_COLOR_PRESETS = [
    "#1490E0", "#0FA3B1", "#4CC38A", "#E06C14", "#A33CDB",
    "#E0416F", "#3DDA5F", "#FFC107", "#8D99AE",
]
