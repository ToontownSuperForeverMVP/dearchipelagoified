"""PRC-driven configuration for the outdoor lighting + procedural sky systems.

This module is intentionally *game-agnostic*: it only knows about Panda3D
ConfigVariables and an optional, duck-typed game settings object attached to
``base.settings`` (e.g. a Toontown options menu).  It never imports toontown or
otp code, so the OutdoorLighting / ProceduralSky / SkyUtil modules it serves can
be lifted into any Panda3D Toontown source and driven entirely from a .prc file.

Resolution order (highest priority first)
──────────────────────────────────────────
1. Explicit value set in a .prc / Config.prc file (or on the command line).
   Detected with ConfigVariable.has_value() — a variable that only carries its
   code-registered default is NOT considered "set".
2. The game's settings provider (``base.settings.getSetting`` / ``base.settings.get``)
   when it exists and returns a non-None value.  This keeps the in-game options
   menu working for knobs the prc leaves untouched.
3. The typed default registered below (mirrors the old built-in defaults).

Every knob is registered up-front as a typed ConfigVariable with a description,
so `panda3d -v` / prc introspection lists them and a typo in a prc key shows up
as an unknown-variable warning instead of silently doing nothing.
"""

from __future__ import annotations

import math

from panda3d.core import (
    ConfigVariableBool,
    ConfigVariableDouble,
    ConfigVariableString,
)

# name -> (ConfigVariable, python default, description)
_BOOLS: dict[str, tuple] = {}
_DOUBLES: dict[str, tuple] = {}
_STRINGS: dict[str, tuple] = {}


def _registerBool(name: str, default: bool, description: str = '') -> None:
    _BOOLS[name] = (ConfigVariableBool(name, bool(default)), bool(default), description)


def _registerDouble(name: str, default: float, description: str = '') -> None:
    _DOUBLES[name] = (ConfigVariableDouble(name, float(default)), float(default), description)


def _registerString(name: str, default: str, description: str = '') -> None:
    _STRINGS[name] = (ConfigVariableString(name, str(default)), str(default), description)


# ─────────────────────────────────────────────────────────────────────────────
# Feature toggles
# ─────────────────────────────────────────────────────────────────────────────
_registerBool('want-modern-outdoor-lighting', True,
              'Master switch for the cinematic outdoor lighting rig (key/fill/rim lights, fog, sky).')
_registerBool('want-procedural-sky', True,
              'Use the shader-driven atmospheric sky dome instead of the legacy model sky.')
_registerBool('want-day-night-cycle', True,
              'Animate lights/fog/sky through per-zone keyframes over a 24h cycle.')
_registerBool('dynamic-shadows', True,
              'Enable per-zone directional sun shadows (and OTP blob/drop shadows).')
_registerBool('lighting-experimental-world-shadows', True,
              'Real-time world shadow maps cast by buildings/trees onto the ground.')
_registerBool('lighting-bloom-enabled', True, 'Cinematic bloom pass.')
_registerBool('lighting-god-rays', True, 'Screen-space sun shaft overlay.')
_registerBool('lighting-fog-enabled', True, 'Atmospheric fog on outdoor zones.')
_registerBool('lighting-specular-enabled', False, 'Specular highlights on lit geometry.')
_registerBool('lighting-tonemap-enabled', True, 'Tonemapping in the post chain.')
_registerBool('lighting-vignette-enabled', False, 'Subtle vignette post effect.')
_registerBool('lighting-contact-shadows', True,
              'Near-contact darkening in the world-shadow receiver.')
_registerBool('lighting-streetlamps-enabled', True, 'Night streetlamp point lights.')
_registerBool('lighting-pointlight-shadows', True, 'Shadow casting for lamp point lights.')
_registerBool('lighting-cel-shading', False, 'Cel/ink toon shading look.')
_registerBool('lighting-physical-atmosphere', True,
              'Physical Rayleigh/Mie sunlight extinction shared by sky + ground light.')
_registerBool('lighting-experimental-full-post', False,
              'Use the experimental full-scene RTT compositor instead of the stable post chain.')
_registerBool('want-water-reflections', True, 'Planar water reflections on water geoms.')
_registerBool('motion-blur', False, 'Camera-cut motion blur in the post chain.')
_registerBool('want-aurora-borealis', True,
              'Aurora in the procedural sky (used by The Brrrgh / Dreamland).')
_registerBool('lighting-debug', False, 'Verbose [OutdoorLighting] debug logging + bisect hotkeys.')
_registerBool('lighting-shadow-test-scene', False,
              'Spawn a cube+ground shadow test scene near the camera (debug only).')
_registerBool('lighting-shadow-force-huge-frustum', False,
              'Debug: force a very large shadow ortho frustum regardless of scene bounds.')

# ─────────────────────────────────────────────────────────────────────────────
# Quantitative tuning
# ─────────────────────────────────────────────────────────────────────────────
_registerDouble('lighting-intensity', 1.0, 'Global multiplier on light intensities.')
_registerDouble('lighting-color-temp', 0.0, 'White-balance bias (-1 cool … +1 warm).')
_registerDouble('lighting-exposure', 1.0, 'Post exposure multiplier.')
_registerDouble('lighting-vignette-strength', 0.25, 'Vignette darkness.')
_registerDouble('lighting-shadow-softness', 1.0, 'World shadow PCF softness.')
_registerDouble('lighting-pointlight-shadow-softness', 1.0, 'Lamp shadow softness.')
_registerDouble('lighting-physical-sun-blend', 0.85,
                'How much of the physical sun colour multiplier blends into the key light.')
_registerDouble('lighting-sky-ambient-blend', 0.35,
                'How much sky ambient colour blends into the scene ambient light.')
_registerDouble('lighting-sky-fog-match', 0.70,
                'How strongly the sky horizon locks to the scene fog colour.')
_registerDouble('fog-density-multiplier', 1.0, 'Global fog density scale.')
_registerDouble('drop-shadow-strength', 0.5, 'OTP blob/drop shadow gray level.')
_registerDouble('day-night-speed', 1.0, 'Day/night cycle speed multiplier.')
_registerDouble('day-duration-minutes', 10.0, 'Day length when day-night-mode is Dynamic.')
_registerDouble('night-duration-minutes', 5.0, 'Night length when day-night-mode is Dynamic.')
_registerDouble('motion-blur-strength', 1.0, 'Motion blur intensity.')
_registerDouble('sky-sun-size', 2.0, 'Procedural sky sun disc size.')
_registerDouble('sky-moon-phase', 0.62, 'Moon phase (0 new … 1 full).')
_registerDouble('sky-moon-angular-radius', 0.0105, 'Moon disc angular radius in the sky shader.')
_registerDouble('sky-cloud-shadow-strength', 1.0, 'Strength of cloud shadowing on the sky.')
_registerDouble('sky-milky-way-strength', 1.0, 'Milky-way band brightness at night.')
_registerDouble('sky-exposure', 1.08, 'Exposure of the sky dome shader itself.')

# ─────────────────────────────────────────────────────────────────────────────
# Enumerated / string tuning
# ─────────────────────────────────────────────────────────────────────────────
_registerString('shadow-quality', 'high', 'off | low | medium | high — shadow map resolution.')
_registerString('sky-cloud-quality', 'high', 'off | low | medium | high — procedural cloud detail.')
_registerString('day-night-mode', 'Dynamic',
                'Dynamic | Real-Time Sync | Always Noon | Always Sunset | Always Midnight | Always Dawn (quote values with spaces in prc).')
_registerString('lighting-tonemap-mode', 'ACES', 'ACES | Filmic | Reinhard | None — tonemap curve.')

# ─────────────────────────────────────────────────────────────────────────────
# Game settings provider (duck-typed; never a hard dependency)
# ─────────────────────────────────────────────────────────────────────────────

_provider = None          # callable: key -> value | None
_providerBase = None      # object we read `base.settings` from
_providerReady = False    # True once we found a working provider


def sync() -> None:
    """(Re)discover the game's settings provider from ``base``.

    ``base`` is typically unavailable at import time (ShowBase is constructed
    later), so this is re-run on every read until a live provider is found.
    Once found it is cached: syncing costs one attribute check afterwards.
    """
    global _provider, _providerBase, _providerReady
    if _providerReady:
        try:
            if _providerBase is not None and getattr(_providerBase, 'render', None) is not None:
                return
        except Exception:
            pass
        _providerReady = False

    base = None
    try:
        from direct.showbase import ShowBaseGlobal as _SBG  # type: ignore
        base = getattr(_SBG, 'base', None)
    except Exception:
        base = None
    if base is None:
        try:
            import builtins
            base = getattr(builtins, 'base', None)
        except Exception:
            base = None

    getter = None
    if base is not None:
        try:
            settings = getattr(base, 'settings', None)
            if settings is not None:
                getter = (getattr(settings, 'getSetting', None)
                          or getattr(settings, 'get', None))
                if not callable(getter):
                    getter = None
        except Exception:
            getter = None

    _providerBase = base
    _provider = getter
    _providerReady = getter is not None


def fromSettings(name: str):
    """Read one key from the game settings provider, if any (never raises)."""
    if not _providerReady:
        sync()
    if _provider is None:
        return None
    try:
        value = _provider(name)
    except TypeError:
        try:
            value = _provider(name, None)
        except Exception:
            value = None
    except Exception:
        value = None
    return value


def _coerceBool(value, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('1', 'true', 'yes', 'on', 'enabled'):
            return True
        if normalized in ('0', 'false', 'no', 'off', 'disabled', ''):
            return False
    return bool(value)


def boolVal(name: str, default: bool = False) -> bool:
    """PRC-explicit → game settings → registered/code default."""
    entry = _BOOLS.get(name)
    if entry is not None:
        cv, defv, _desc = entry
        if cv.has_value():
            return _coerceBool(cv.value, defv)
        value = fromSettings(name)
        if value is not None:
            return _coerceBool(value, defv)
        return bool(defv)
    # Unregistered key: fall back to an ad-hoc typed variable (historic behaviour).
    try:
        cv = ConfigVariableBool(name, bool(default))
        if cv.has_value():
            return _coerceBool(cv.value, default)
    except Exception:
        pass
    value = fromSettings(name)
    if value is not None:
        return _coerceBool(value, default)
    return bool(default)


def doubleVal(name: str, default: float = 0.0) -> float:
    """PRC-explicit → game settings → registered/code default (float)."""
    entry = _DOUBLES.get(name)
    if entry is not None:
        cv, defv, _desc = entry
        if cv.has_value():
            return _finite(cv.value, defv)
        value = fromSettings(name)
        if value is not None:
            return _finite(value, defv)
        return float(defv)
    try:
        cv = ConfigVariableDouble(name, float(default))
        if cv.has_value():
            return _finite(cv.value, default)
    except Exception:
        pass
    value = fromSettings(name)
    if value is not None:
        return _finite(value, default)
    return float(default)


def stringVal(name: str, default: str = '') -> str:
    """PRC-explicit → game settings → registered/code default (string)."""
    entry = _STRINGS.get(name)
    if entry is not None:
        cv, defv, _desc = entry
        if cv.has_value():
            return str(cv.value)
        value = fromSettings(name)
        if value is not None:
            return str(value)
        return str(defv)
    try:
        cv = ConfigVariableString(name, str(default))
        if cv.has_value():
            return str(cv.value)
    except Exception:
        pass
    value = fromSettings(name)
    if value is not None:
        return str(value)
    return str(default)


def value(name: str, default=None):
    """Generic typed read (matches the old _getSettingValue signature)."""
    if default is None:
        if name in _BOOLS:
            return boolVal(name)
        if name in _DOUBLES:
            return doubleVal(name)
        if name in _STRINGS:
            return stringVal(name)
        return boolVal(name, False)
    if isinstance(default, bool):
        return boolVal(name, default)
    if isinstance(default, (int, float)):
        return doubleVal(name, float(default))
    return stringVal(name, str(default))


def _finite(value, default: float) -> float:
    try:
        f = float(value)
        return f if math.isfinite(f) else float(default)
    except Exception:
        return float(default)


def hasPrcOverride(name: str) -> bool:
    """True when ``name`` is set explicitly in a prc file / command line."""
    for table in (_BOOLS, _DOUBLES, _STRINGS):
        entry = table.get(name)
        if entry is not None:
            try:
                return entry[0].has_value()
            except Exception:
                return False
    return False


def describe(name: str) -> str:
    """Return the registered description for a knob ('' when unregistered)."""
    for table in (_BOOLS, _DOUBLES, _STRINGS):
        entry = table.get(name)
        if entry is not None:
            return entry[2]
    return ''
