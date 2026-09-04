from __future__ import annotations

import math
import os

from panda3d.core import (
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    NodePath,
    Shader,
    Vec3,
    Vec4,
)

from direct.showbase.ShowBaseGlobal import globalClock
from toontown.hood import LightingConfig


def _setting(name: str, default):
    """PRC-first generic knob read (see LightingConfig for resolution order)."""
    try:
        return LightingConfig.value(name, default)
    except Exception:
        return default


def _settingBool(name: str, default: bool) -> bool:
    """PRC-first bool knob read (see LightingConfig for resolution order)."""
    try:
        return LightingConfig.boolVal(name, default)
    except Exception:
        return default


def _settingFloat(name: str, default: float, lo: float, hi: float) -> float:
    """PRC-first float knob read, clamped to [lo, hi]."""
    try:
        value = LightingConfig.doubleVal(name, default)
        if not math.isfinite(value):
            value = float(default)
    except Exception:
        value = float(default)
    return max(float(lo), min(float(hi), value))


_oslMod = None


def _getOutdoorLightingModule():
    """Lazily import the shared physical-atmosphere helpers.

    OutdoorLighting imports this module at runtime (inside _setupProceduralSky)
    so we must not import it at module scope here — that would be circular.
    """
    global _oslMod
    if _oslMod is not None:
        return _oslMod
    try:
        from toontown.hood import OutdoorLighting as _m
    except ImportError:
        try:
            import OutdoorLighting as _m
        except Exception:
            _m = None
    _oslMod = _m
    return _m

_SHADER_DIR_CANDIDATES = (
    os.path.join(os.path.dirname(__file__), '..', 'shaders'),
    os.path.join(os.path.dirname(__file__), 'shaders'),
    os.path.join(os.path.dirname(__file__), '..', '..', 'shaders'),
)
_SHADER_DIR = next((d for d in _SHADER_DIR_CANDIDATES if os.path.exists(d)), _SHADER_DIR_CANDIDATES[0])
_SKY_VERT   = os.path.join(_SHADER_DIR, 'sky.vert.glsl')
_SKY_FRAG   = os.path.join(_SHADER_DIR, 'sky.frag.glsl')

_SKY_RADIUS       = 950.0
_CLOUD_BASE_SPEED = 0.60

_sky_shader: Shader | None = None


def _loadSkyShader() -> Shader | None:
    global _sky_shader
    if _sky_shader is not None:
        return _sky_shader
    try:
        try:
            from toontown.hood import OutdoorLighting as osl
        except ImportError:
            import OutdoorLighting as osl
        bisect_level = getattr(osl, '_OUTDOOR_SHADER_BISECT_LEVEL', 0)
        if bisect_level < 1:
            return None
    except Exception:
        pass
    try:
        vp_os = os.path.normpath(_SKY_VERT)
        fp_os = os.path.normpath(_SKY_FRAG)
        vp_exists = os.path.isfile(vp_os)
        fp_exists = os.path.isfile(fp_os)
        if not (vp_exists and fp_exists):
            return None
        with open(vp_os, 'r', encoding='utf-8') as vertex_file:
            vertex_source = vertex_file.read()
        with open(fp_os, 'r', encoding='utf-8') as fragment_file:
            fragment_source = fragment_file.read()
        _sky_shader = Shader.make(Shader.SL_GLSL, vertex_source, fragment_source)
        return _sky_shader
    except Exception:
        _sky_shader = None
        return None


def _makeSkySphereMesh(radius: float = _SKY_RADIUS,
                       latSegs: int = 48,
                       lonSegs: int = 96) -> NodePath:
    vfmt  = GeomVertexFormat.getV3()
    vdata = GeomVertexData('skyDomeMesh', vfmt, Geom.UHStatic)
    vdata.setNumRows((latSegs + 1) * (lonSegs + 1))
    vwrite = GeomVertexWriter(vdata, 'vertex')

    for lat in range(latSegs + 1):
        phi = math.pi * lat / latSegs
        sp  = math.sin(phi)
        cp  = math.cos(phi)
        for lon in range(lonSegs + 1):
            theta = 2.0 * math.pi * lon / lonSegs
            x = radius * sp * math.cos(theta)
            y = radius * sp * math.sin(theta)
            z = radius * cp
            vwrite.addData3(x, y, z)

    tris  = GeomTriangles(Geom.UHStatic)
    stride = lonSegs + 1
    for lat in range(latSegs):
        for lon in range(lonSegs):
            v0 = lat       * stride + lon
            v1 = lat       * stride + lon + 1
            v2 = (lat + 1) * stride + lon
            v3 = (lat + 1) * stride + lon + 1
            tris.addVertices(v0, v2, v1)
            tris.addVertices(v1, v2, v3)
    tris.closePrimitive()

    geom = Geom(vdata)
    geom.addPrimitive(tris)
    gnode = GeomNode('skyDomeGeom')
    gnode.addGeom(geom)
    return NodePath(gnode)


def _hprToDir(h_deg: float, p_deg: float) -> Vec3:
    h = math.radians(h_deg)
    p = math.radians(p_deg)
    x =  math.sin(h) * math.cos(p)
    y = -math.cos(h) * math.cos(p)
    z = -math.sin(p)
    return Vec3(x, y, z)


_ZONE_SKY_DEFAULTS: dict[str, dict] = {
    'tt':          {'cloudCoverage': 0.52, 'cloudSpeed': 0.60, 'cloudSharpness': 0.65,
                    'turbidity': 2.2,  'starBrightness': 0.0, 'moonEnabled': False},
    'dd':          {'cloudCoverage': 0.85, 'cloudSpeed': 0.90, 'cloudSharpness': 0.25,
                    'turbidity': 4.8,  'starBrightness': 0.0, 'moonEnabled': False},
    'dg':          {'cloudCoverage': 0.32, 'cloudSpeed': 0.50, 'cloudSharpness': 0.72,
                    'turbidity': 1.6,  'starBrightness': 0.0, 'moonEnabled': False},
    'mm':          {'cloudCoverage': 0.48, 'cloudSpeed': 0.75, 'cloudSharpness': 0.55,
                    'turbidity': 3.2,  'starBrightness': 0.0, 'moonEnabled': False},
    'br':          {'cloudCoverage': 0.75, 'cloudSpeed': 1.20, 'cloudSharpness': 0.20,
                    'turbidity': 5.5,  'starBrightness': 0.0, 'moonEnabled': False,
                    'auroraEnabled': True},
    'dl':          {'cloudCoverage': 0.18, 'cloudSpeed': 0.20, 'cloudSharpness': 0.50,
                    'turbidity': 1.5,  'starBrightness': 0.95, 'moonEnabled': True,
                    'moonDir': (225, -55, 0), 'auroraEnabled': True},
    'gs':          {'cloudCoverage': 0.32, 'cloudSpeed': 0.90, 'cloudSharpness': 0.50,
                    'turbidity': 2.8,  'starBrightness': 0.0, 'moonEnabled': False},
    'estate':      {'cloudCoverage': 0.45, 'cloudSpeed': 0.55, 'cloudSharpness': 0.60,
                    'turbidity': 2.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'playground':  {'cloudCoverage': 0.45, 'cloudSpeed': 0.60, 'cloudSharpness': 0.55,
                    'turbidity': 2.2,  'starBrightness': 0.0, 'moonEnabled': False},
    'sellbot_hq':  {'cloudCoverage': 0.88, 'cloudSpeed': 0.30, 'cloudSharpness': 0.20,
                    'turbidity': 6.5,  'starBrightness': 0.0, 'moonEnabled': False},
    'cashbot_hq':  {'cloudCoverage': 0.85, 'cloudSpeed': 0.35, 'cloudSharpness': 0.20,
                    'turbidity': 6.8,  'starBrightness': 0.0, 'moonEnabled': False},
    'lawbot_hq':   {'cloudCoverage': 0.90, 'cloudSpeed': 0.25, 'cloudSharpness': 0.15,
                    'turbidity': 6.2,  'starBrightness': 0.0, 'moonEnabled': False},
    'bossbot_hq':  {'cloudCoverage': 0.92, 'cloudSpeed': 0.20, 'cloudSharpness': 0.15,
                    'turbidity': 7.2,  'starBrightness': 0.0, 'moonEnabled': False},
    'cog':         {'cloudCoverage': 0.85, 'cloudSpeed': 0.30, 'cloudSharpness': 0.18,
                    'turbidity': 6.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'tt_street':   {'cloudCoverage': 0.50, 'cloudSpeed': 0.58, 'cloudSharpness': 0.65,
                    'turbidity': 2.2,  'starBrightness': 0.0, 'moonEnabled': False},
    'dd_street':   {'cloudCoverage': 0.85, 'cloudSpeed': 0.95, 'cloudSharpness': 0.25,
                    'turbidity': 5.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'dg_street':   {'cloudCoverage': 0.30, 'cloudSpeed': 0.48, 'cloudSharpness': 0.74,
                    'turbidity': 1.6,  'starBrightness': 0.0, 'moonEnabled': False},
    'mm_street':   {'cloudCoverage': 0.46, 'cloudSpeed': 0.78, 'cloudSharpness': 0.52,
                    'turbidity': 3.2,  'starBrightness': 0.0, 'moonEnabled': False},
    'br_street':   {'cloudCoverage': 0.78, 'cloudSpeed': 1.25, 'cloudSharpness': 0.18,
                    'turbidity': 5.6,  'starBrightness': 0.0, 'moonEnabled': False,
                    'auroraEnabled': True},
    'dl_street':   {'cloudCoverage': 0.18, 'cloudSpeed': 0.18, 'cloudSharpness': 0.50,
                    'turbidity': 1.5,  'starBrightness': 0.95, 'moonEnabled': True,
                    'moonDir': (225, -55, 0), 'auroraEnabled': True},
    'golf_course': {'cloudCoverage': 0.35, 'cloudSpeed': 0.60, 'cloudSharpness': 0.62,
                    'turbidity': 2.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'oz':          {'cloudCoverage': 0.40, 'cloudSpeed': 0.55, 'cloudSharpness': 0.60,
                    'turbidity': 2.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'oz_street':   {'cloudCoverage': 0.38, 'cloudSpeed': 0.52, 'cloudSharpness': 0.62,
                    'turbidity': 2.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'gz':          {'cloudCoverage': 0.35, 'cloudSpeed': 0.60, 'cloudSharpness': 0.62,
                    'turbidity': 2.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'party':       {'cloudCoverage': 0.42, 'cloudSpeed': 0.55, 'cloudSharpness': 0.60,
                    'turbidity': 2.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'tutorial':    {'cloudCoverage': 0.48, 'cloudSpeed': 0.55, 'cloudSharpness': 0.65,
                    'turbidity': 2.2,  'starBrightness': 0.0, 'moonEnabled': False},
    'bossbot_cc':  {'cloudCoverage': 0.00, 'cloudSpeed': 0.00, 'cloudSharpness': 0.00,
                    'turbidity': 1.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'factory_int': {'cloudCoverage': 0.88, 'cloudSpeed': 0.25, 'cloudSharpness': 0.20,
                    'turbidity': 6.5,  'starBrightness': 0.0, 'moonEnabled': False},
    'mint_int':    {'cloudCoverage': 0.00, 'cloudSpeed': 0.00, 'cloudSharpness': 0.00,
                    'turbidity': 1.0,  'starBrightness': 0.0, 'moonEnabled': False},
    'office_int':  {'cloudCoverage': 0.00, 'cloudSpeed': 0.00, 'cloudSharpness': 0.00,
                    'turbidity': 1.0,  'starBrightness': 0.0, 'moonEnabled': False},
}


class ProceduralSky:
    _TASK_NAME = 'proceduralSkyTask'

    def __init__(self) -> None:
        self._skyNp: NodePath | None = None
        self._time: float = 0.0
        self._timeOrigin: float | None = None
        self._activeStyle: str = 'playground'
        self._attached: bool = False
        # Cache of the last pushed shader uniforms.  update() runs every frame
        # but only a couple of values (time, timeOfDay) change per frame, so we
        # skip the C++ setShaderInput call whenever a value is unchanged.  This
        # avoids ~30 redundant driver/state updates per frame with zero impact
        # on the rendered image.
        self._lastInputs: dict[str, tuple] = {}

    def attach(self, parent: NodePath, style: str = 'playground') -> None:
        if self._attached:
            return
        if parent is None or (hasattr(parent, 'isEmpty') and parent.isEmpty()):
            return
        shader = _loadSkyShader()
        if shader is None:
            return

        try:
            self._skyNp = _makeSkySphereMesh()
            self._skyNp.reparentTo(parent)
            self._skyNp.setDepthTest(False)
            self._skyNp.setDepthWrite(False)
            self._skyNp.setLightOff(1)
            self._skyNp.setFogOff()
            self._skyNp.setBin('background', 1000)
            self._skyNp.setTwoSided(True)
            self._skyNp.setShader(shader)

            self._activeStyle = style if style in _ZONE_SKY_DEFAULTS else 'playground'
            self._time = 0.0
            try:
                self._timeOrigin = float(globalClock.getFrameTime())
            except Exception:
                self._timeOrigin = None
            self._attached = True
            self._lastInputs.clear()
            self.update(_ZONE_SKY_DEFAULTS.get(self._activeStyle, _ZONE_SKY_DEFAULTS['playground']), 12.0)
        except Exception:
            try:
                if self._skyNp is not None and not self._skyNp.isEmpty():
                    self._skyNp.removeNode()
            except Exception:
                pass
            self._skyNp = None
            self._attached = False
            self._timeOrigin = None

    def update(self, spec: dict, timeOfDay: float = 12.0) -> None:
        if not self._attached or self._skyNp is None or self._skyNp.isEmpty():
            return

        try:
            now = float(globalClock.getFrameTime())
            if self._timeOrigin is None:
                self._timeOrigin = now
            self._time = max(0.0, now - self._timeOrigin)
        except Exception:
            self._time += max(0.0, float(globalClock.getDt()))

        style  = self._activeStyle
        skyDef = _ZONE_SKY_DEFAULTS.get(style, _ZONE_SKY_DEFAULTS['playground'])
        cov    = max(0.0, min(1.0, float(spec.get('cloudCoverage') if spec.get('cloudCoverage') is not None else skyDef.get('cloudCoverage', 0.4))))
        spd    = max(0.0, min(4.0, float(spec.get('cloudSpeed') if spec.get('cloudSpeed') is not None else skyDef.get('cloudSpeed', 0.6))))
        sharp  = max(0.0, min(1.0, float(spec.get('cloudSharpness') if spec.get('cloudSharpness') is not None else skyDef.get('cloudSharpness', 0.5))))
        turb   = max(1.0, min(10.0, float(spec.get('turbidity') if spec.get('turbidity') is not None else skyDef.get('turbidity', 2.5))))
        stars  = max(0.0, min(2.0, float(spec.get('starBrightness') if spec.get('starBrightness') is not None else skyDef.get('starBrightness', 0.0))))
        moonOn = float(1 if (spec.get('moonEnabled') if spec.get('moonEnabled') is not None else skyDef.get('moonEnabled', False)) else 0)
        moonHpr = spec.get('moonDir') if spec.get('moonDir') is not None else skyDef.get('moonDir', (225, -55, 0))

        auroraSetting = _settingBool('want-aurora-borealis', True)
        auroraOn = float(1.0 if (auroraSetting and ((spec.get('auroraEnabled') if spec.get('auroraEnabled') is not None else skyDef.get('auroraEnabled', False)) or style in ('br', 'br_street', 'dl', 'dl_street'))) else 0.0)
        moonPhaseVal = float(spec.get('moonPhase') if spec.get('moonPhase') is not None else _settingFloat('sky-moon-phase', 0.62, 0.0, 1.0))

        qualityName = str(_setting('sky-cloud-quality', 'high')).strip().lower()
        qualityLevel = {'off': 0.0, 'low': 1.0, 'medium': 2.0, 'high': 3.0}.get(
            qualityName, 3.0)
        if qualityLevel < 0.5:
            cov = 0.0

        isPerpetualNight = style in ('dl', 'dl_street', 'sellbot_hq', 'cashbot_hq', 'lawbot_hq', 'bossbot_hq', 'cog', 'factory_int')
        h = float(timeOfDay) % 24.0
        if isPerpetualNight:
            nightFactor = 1.0
        elif h >= 20.0 or h <= 5.0:
            nightFactor = 1.0
        elif 18.0 < h < 20.0:
            t = max(0.0, min(1.0, (h - 18.0) / 2.0))
            nightFactor = t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
        elif 5.0 < h < 7.0:
            t = max(0.0, min(1.0, (7.0 - h) / 2.0))
            nightFactor = t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
        else:
            nightFactor = 0.0

        keyColor   = Vec4(*(spec.get('key') or (1, 1, 1, 1)))
        clearColor = spec.get('clearColor') or (0.4, 0.6, 0.85, 1.0)
        fogColorV  = spec.get('fogColor') or (0.6, 0.7, 0.85, 1.0)
        skyScaleV  = spec.get('skyScale') or (1, 1, 1, 1)
        ambientV   = spec.get('ambient') or (0.25, 0.30, 0.45, 1.0)
        fillV      = spec.get('fill') or (0.30, 0.40, 0.60, 1.0)
        rimV       = spec.get('rim') or (0.15, 0.20, 0.35, 1.0)
        rayColorV  = spec.get('rayColor') or (1.0, 0.95, 0.75, 1.0)
        rayInt     = float(spec.get('rayIntensity') or 0.0)

        keyHpr     = spec.get('keyHpr') or (135, -42, 0)
        sunDirWorld = _hprToDir(keyHpr[0], keyHpr[1])

        if isPerpetualNight or nightFactor >= 0.99:
            sunDirWorld.z = -abs(sunDirWorld.z)
            if sunDirWorld.z > -0.5:
                sunDirWorld.z = -0.707
            try:
                sunDirWorld.normalize()
            except Exception:
                pass
            sunElev = float(sunDirWorld.z)
            sunDiscOn = 0.0
            sunKeyColor = Vec4(0.0, 0.0, 0.0, 1.0)
        elif nightFactor > 0.0:
            sunDirWorld.z = abs(sunDirWorld.z) * (1.0 - nightFactor * 2.0)
            try:
                sunDirWorld.normalize()
            except Exception:
                pass
            sunElev = float(sunDirWorld.z)
            sunDiscOn = 1.0 if (sunElev > -0.04 and nightFactor < 0.35) else 0.0
            sunKeyColor = keyColor * max(0.0, 1.0 - nightFactor)
        else:
            sunElev = float(sunDirWorld.z)
            sunDiscOn = 1.0 if sunElev > -0.04 else 0.0
            sunKeyColor = keyColor

        # Physical sunlight after Rayleigh + Mie extinction.  The scene's key
        # light is tinted with the same multiplier (see OutdoorLighting), so the
        # sun disc / mie glow / clouds here and the light hitting the ground all
        # warm together as the sun drops.
        physMul = (1.0, 1.0, 1.0)
        fogDensity = 0.4
        fogMatch = 0.0
        sunAngularRadius = 0.012
        _osl = _getOutdoorLightingModule()
        if _osl is not None:
            try:
                physMul = _osl._physSunColorMul(float(sunDirWorld.z), turb, nightFactor)
                fogDensity = _osl._physFogDensity(spec)
                fogMatch = _osl._physFogMatch()
                sunAngularRadius = _osl._skySunAngularRadius()
            except Exception:
                pass
        sunLightColor = Vec3(
            sunKeyColor.x * physMul[0],
            sunKeyColor.y * physMul[1],
            sunKeyColor.z * physMul[2],
        )

        moonDirWorld = Vec3(0, 0, 1)
        if moonOn > 0.5 or isPerpetualNight or nightFactor > 0.1:
            moonDirWorld = _hprToDir(moonHpr[0], moonHpr[1])
            if moonDirWorld.z < 0.05:
                moonDirWorld.z = 0.65
                try:
                    moonDirWorld.normalize()
                except Exception:
                    pass

        dayStrength = max(0.0, min(1.0, (float(sunDirWorld.z) + 0.08) * 2.6)) if not isPerpetualNight else 0.0
        twilightFactor = max(0.0, min(1.0, 1.0 - abs(float(sunDirWorld.z)) * 5.5))
        if isPerpetualNight:
            twilightFactor = 0.0

        nightSignal = max(stars, moonOn * 0.85, 1.0 if isPerpetualNight else 0.0)
        zenithOverride = spec.get('skyZenithColor') if (nightSignal < 0.08 and not isPerpetualNight) else None
        if zenithOverride is not None:
            zenith = Vec3(zenithOverride[0], zenithOverride[1], zenithOverride[2])
        else:
            zenith = Vec3(clearColor[0], clearColor[1], clearColor[2])
        vividZenith = Vec3(0.08, 0.48, 1.22)
        vividHorizon = Vec3(0.48, 0.76, 1.08)
        zenith = zenith * (1.0 - dayStrength * 0.45) + vividZenith * (dayStrength * 0.45)
        cleanHorizon = Vec3(clearColor[0] * 0.90 + 0.08, clearColor[1] * 0.90 + 0.08, clearColor[2] * 0.90 + 0.08)
        horizon = cleanHorizon * (1.0 - dayStrength * 0.55) + vividHorizon * (dayStrength * 0.55)
        # Lock the sky's horizon to the scene fog colour so distant geometry
        # fades into exactly the colour the sky shows at the horizon (aerial
        # perspective).  The shader still adds its own glow on top.
        if fogMatch > 0.0:
            fogRGB = Vec3(fogColorV[0], fogColorV[1], fogColorV[2])
            horizon = horizon * (1.0 - fogMatch) + fogRGB * fogMatch

        if style in ('sellbot_hq', 'factory_int'):
            deptSmogTint = Vec3(0.32, 0.24, 0.44)
        elif style in ('cashbot_hq', 'mint_int'):
            deptSmogTint = Vec3(0.24, 0.34, 0.22)
        elif style in ('lawbot_hq', 'office_int'):
            deptSmogTint = Vec3(0.22, 0.28, 0.38)
        elif style in ('bossbot_hq', 'bossbot_cc'):
            deptSmogTint = Vec3(0.35, 0.28, 0.22)
        else:
            deptSmogTint = Vec3(0.0, 0.0, 0.0)

        if isPerpetualNight:
            effectiveStars = max(stars, 0.95 if style in ('dl', 'dl_street') else 0.0)
        else:
            isDaytime = max(0.0, min(1.0, (sunElev + 0.1) * 3.0))
            effectiveStars = stars * (1.0 - isDaytime)

        # The sky shader is display-referred, so a broad HDR glare term easily
        # turns the low sun into a giant white/orange ball.  Keep a restrained
        # default and leave the small physical disc readable at the horizon.
        blindStr = float(spec.get('sunBlindStrength', 0.28))
        blindStr = max(0.0, min(0.40, blindStr)) if nightFactor < 0.3 else 0.0

        moonEnabled = 1.0 if (moonOn > 0.5 or (isPerpetualNight and style in ('dl', 'dl_street')) or (nightFactor > 0.1 and spec.get('moonEnabled', False))) else 0.0
        milkyWay = _settingFloat('sky-milky-way-strength', 1.0, 0.0, 2.0) if effectiveStars > 0.01 else 0.0
        cloudShadowStrength = _settingFloat('sky-cloud-shadow-strength', 1.0, 0.0, 2.0)
        skyExposure = _settingFloat('sky-exposure', 1.08, 0.55, 1.75)
        moonAngularRadius = _settingFloat('sky-moon-angular-radius', 0.0105, 0.004, 0.025)
        timeOfDayVal = float(timeOfDay) % 24.0

        inputs = {
            'sunDir':            (round(sunDirWorld.x, 5), round(sunDirWorld.y, 5), round(sunDirWorld.z, 5)),
            'sunWorldElev':      (sunElev,),
            'sunColor':          (round(sunKeyColor.x, 4), round(sunKeyColor.y, 4), round(sunKeyColor.z, 4)),
            'sunLightColor':     (round(sunLightColor.x, 4), round(sunLightColor.y, 4), round(sunLightColor.z, 4)),
            'zenithColor':       (round(zenith.x, 4), round(zenith.y, 4), round(zenith.z, 4)),
            'horizonColor':      (round(horizon.x, 4), round(horizon.y, 4), round(horizon.z, 4)),
            'fogColor':          (round(fogColorV[0], 4), round(fogColorV[1], 4), round(fogColorV[2], 4)),
            'ambientColor':      (round(ambientV[0], 4), round(ambientV[1], 4), round(ambientV[2], 4)),
            'fillColor':         (round(fillV[0], 4), round(fillV[1], 4), round(fillV[2], 4)),
            'rimColor':          (round(rimV[0], 4), round(rimV[1], 4), round(rimV[2], 4)),
            'rayColor':          (round(rayColorV[0], 4), round(rayColorV[1], 4), round(rayColorV[2], 4)),
            'rayIntensity':      (rayInt,),
            'deptSmogTint':      (round(deptSmogTint.x, 4), round(deptSmogTint.y, 4), round(deptSmogTint.z, 4)),
            'cloudCoverage':     (cov,),
            'cloudSpeed':        (spd * _CLOUD_BASE_SPEED,),
            'cloudSharpness':    (sharp,),
            'cloudQuality':      (qualityLevel,),
            'turbidity':         (turb,),
            'fogDensity':        (fogDensity,),
            'starBrightness':    (effectiveStars,),
            'moonEnabled':       (moonEnabled,),
            'moonDir':           (round(moonDirWorld.x, 5), round(moonDirWorld.y, 5), round(moonDirWorld.z, 5)),
            'moonColor':         (round(keyColor.x, 4), round(keyColor.y, 4), round(keyColor.z, 4)),
            'moonPhase':         (moonPhaseVal,),
            'auroraEnabled':     (auroraOn,),
            'milkyWayStrength':  (milkyWay,),
            'nightFactor':       (nightFactor,),
            'twilightFactor':    (twilightFactor,),
            'cloudShadowStrength': (cloudShadowStrength,),
            'skyExposure':         (skyExposure,),
            'moonAngularRadius':   (moonAngularRadius,),
            'sunAngularRadius':    (sunAngularRadius,),
            'time':                (self._time,),
            'skyScale':            (round(skyScaleV[0], 4), round(skyScaleV[1], 4), round(skyScaleV[2], 4), round(skyScaleV[3], 4)),
            'sunDiscEnabled':      (sunDiscOn,),
            'sunBlindStrength':    (blindStr,),
            'timeOfDay':           (timeOfDayVal,),
        }

        np = self._skyNp
        for name, key in inputs.items():
            prev = self._lastInputs.get(name)
            if prev == key:
                continue
            self._lastInputs[name] = key
            try:
                if name == 'skyScale':
                    np.setShaderInput(name, Vec4(*key))
                elif name in ('sunDir', 'moonDir'):
                    np.setShaderInput(name, Vec3(*key))
                elif name in ('sunColor', 'sunLightColor', 'zenithColor', 'horizonColor', 'fogColor',
                              'ambientColor', 'fillColor', 'rimColor', 'rayColor',
                              'moonColor'):
                    np.setShaderInput(name, Vec3(*key))
                else:
                    np.setShaderInput(name, key[0])
            except Exception:
                pass

    def setStyle(self, style: str) -> None:
        newStyle = style if style in _ZONE_SKY_DEFAULTS else 'playground'
        if newStyle != self._activeStyle:
            self._activeStyle = newStyle
            # Force a full re-push on the next update so a changed style can
            # never ride on cached uniforms from the previous sky.
            self._lastInputs.clear()

    def isActive(self) -> bool:
        return self._attached and self._skyNp is not None and not self._skyNp.isEmpty()

    def detach(self) -> None:
        if self._skyNp and not self._skyNp.isEmpty():
            self._skyNp.removeNode()
        self._skyNp   = None
        self._attached = False
        self._time     = 0.0
        self._timeOrigin = None
        self._lastInputs.clear()
