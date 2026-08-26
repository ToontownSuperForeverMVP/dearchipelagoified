from __future__ import annotations

from typing import Any
from toontown.hood import OutdoorLighting


_PROFILES = OutdoorLighting._ZONE_PROFILES


def _syncBase():
    OutdoorLighting._syncBase()


def begin(geom: Any = None, style: str = 'toon', zoneId: int | None = None) -> None:
    OutdoorLighting.begin(geom, style=style, zoneId=zoneId)


def end(geom: Any = None) -> None:
    OutdoorLighting.end(geom)


def shadeExtraSubtree(np: Any) -> None:
    OutdoorLighting.shadeExtraSubtree(np)


def clearExtraSubtree(np: Any) -> None:
    OutdoorLighting.clearExtraSubtree(np)


def refreshSettings() -> None:
    OutdoorLighting.refreshSettings()


def getActiveStyle() -> str:
    return OutdoorLighting._activeStyle


def _resolveStyle(style: str, zoneId: int | None = None) -> str:
    return OutdoorLighting._resolveStyle(style, hoodId=None, zoneId=zoneId)


def _applyProfile() -> None:
    pass


def __getattr__(name: str) -> Any:
    if name == '_refCount':
        return OutdoorLighting._refCount
    if name == '_activeStyle':
        return OutdoorLighting._activeStyle
    if name == '_rig':
        return OutdoorLighting._lightRig
    if name == '_ambientNp':
        for l in (OutdoorLighting._renderLights or []):
            if 'Ambient' in l.getName():
                return l
        return None
    if name == '_keyNp':
        return OutdoorLighting._keyLightNp
    if name == '_fillNp':
        for l in (OutdoorLighting._renderLights or []):
            if 'Fill' in l.getName():
                return l
        return None
    if name == '_fixtureLights':
        return OutdoorLighting._lampLights
    if name == '_fixtureNodes':
        return OutdoorLighting._lampGeomNps
    if name == '_activeRoots':
        return [OutdoorLighting._activeGeom] if OutdoorLighting._activeGeom is not None else []
    if name == '_shaderStates':
        return OutdoorLighting._extraShaderStates
    if hasattr(OutdoorLighting, name):
        return getattr(OutdoorLighting, name)
    raise AttributeError(f"module 'toontown.hood.IndoorLighting' has no attribute '{name}'")
