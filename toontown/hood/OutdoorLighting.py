"""Advanced cinematic outdoor lighting system for Toontown.

Zone lighting profiles – each zone (and street variant) captures a real-world
atmospheric feel:
  tt / tt_street – Toontown Central:    warm Nebraska afternoon ~3 PM golden hour
  dd / dd_street – Donald's Dock:       heavy overcast maritime fog, diffuse daylight
  dg / dg_street – Daisy's Gardens:     blazing Virginia spring noon, intense blue sky
  mm / mm_street – Minnie's Melodyland: deep California sunset, orange-purple sky
  br / br_street – The Brrrgh:          arctic blizzard, pale powder-blue light
  dl / dl_street – Donald's Dreamland:  moonlit midnight, cool blue-purple starry sky
  gs             – Goofy Speedway:      hazy mid-morning race day, light dusty haze
  sellbot_hq     – dark purple factory smog
  cashbot_hq     – sickly green money trainyard
  lawbot_hq      – cold steel-blue overcast courtroom evening
  bossbot_hq     – near-pitch-black boardroom with brown ember undertones
  factory_int    – Sellbot / Lawbot factory interior: dim green-tinted machinery light
  mint_int       – Cashbot Mint interior: cold metallic pale-green vaults
  office_int     – Lawbot Office interior: harsh white corridor fluorescents
  bossbot_cc     – Bossbot Country Club interior: mahogany dimness + warm lamp fill
  golf_course    – Bossbot Golf Course exterior: verdant sunlit fairway

New / upgraded systems
────────────────────────
POST-PROCESS (no full-scene RTT compositor)
  • CommonFilters bloom on the main window; near-contact definition is handled
    in the world receiver so sky pixels never enter a depth-based AO pass.
  • Screen-space sun shafts via a lightweight render2dp overlay (sunrays shaders).
  • Avoids the custom FilterManager “scene → texture → fullscreen quad” path that
    triggered bottom-left viewport bugs on some drivers.
  Controlled by lighting-bloom-enabled, lighting-god-rays, zone bloom/ray params.

PROCEDURAL SKY
  • ProceduralSky replaces hood.sky model with a shader-driven atmospheric sky
    dome: Rayleigh + Mie scattering, FBM cumulus clouds that react to sunlight,
    night-time star field, optional moon disc (Donald's Dreamland).
  • Toggle: want-procedural-sky (default True).  Falls back to model sky if
    shaders unavailable.

SHADOW SYSTEM
  • Full-zone coverage: shadow film size computed from actual scene geometry.
  • Compact 3x3 Gaussian PCF keeps shadows sharp and temporally stable without
    rotated Poisson grain or hard pixel stair-stepping.
  • Every outdoor Geom participates in the two-sided shadow pass, including
    foliage cards, signs, fences, facades, and mixed-winding legacy props.
  • Transparent texture planes (foliage, building facades) receive and cast
    shadows correctly via clearLightOff + a dedicated shadow mask BitMask32.
  • Per-zone shadowDarknessFloor prevents pitch-black even in deep shadow.

DAY / NIGHT CYCLE
  • Per-zone keyframe tables; all parameters smoothly interpolated.
  • All new parameters (cloud coverage, star brightness, Mie turbidity, etc.)
    are also interpolated across the day/night cycle.
  • Toggle: want-day-night-cycle.

BACKWARD COMPATIBILITY
  Public API unchanged: begin(), end(), shadeExtraSubtree(),
  clearExtraSubtree(), refreshSettings().
  New: setTimeOfDay(), setupWaterReflection(), setWaterReflectionQuality().
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any

from panda3d.core import (
    AmbientLight,
    BitMask32,
    BillboardEffect,
    CardMaker,
    ColorBlendAttrib,
    ColorWriteAttrib,
    CollisionHandlerQueue,
    CollisionNode,
    CollisionRay,
    CollisionTraverser,
    CullFaceAttrib,
    ConfigVariableBool,
    ConfigVariableDouble,
    ConfigVariableString,
    DepthOffsetAttrib,
    DirectionalLight,
    Filename,
    Fog,
    FrameBufferProperties,
    Geom,
    GeomEnums,
    GeomNode,
    GeomPrimitive,
    GeomTriangles,
    GeomVertexArrayFormat,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexReader,
    GeomVertexWriter,
    GraphicsOutput,
    GraphicsPipe,
    LightAttrib,
    LMatrix4,
    Material,
    NodePath,
    OrthographicLens,
    PTA_LVecBase4f,
    PTA_LVecBase2f,
    LVecBase4f,
    LVecBase2f,
    PlaneNode,
    PNMImage,
    PointLight,
    PandaSystem,
    Point2,
    Point3,
    Quat,
    RenderState,
    Shader,
    ShaderAttrib,
    Texture,
    TextureAttrib,
    TextureStage,
    TransparencyAttrib,
    Vec2,
    Vec3,
    Vec4,
    WindowProperties,
)

try:
    from otp.avatar import ShadowCaster as _OTPDropshadow
except Exception:
    _OTPDropshadow = None

from direct.showbase.ShowBaseGlobal import globalClock
from direct.task.TaskManagerGlobal import taskMgr
try:
    # Optional: for debug hotkeys (ShowBase provides .accept)
    from direct.showbase.DirectObject import DirectObject  # type: ignore
except Exception:
    DirectObject = None  # type: ignore
try:
    from otp.otpbase import OTPRender as _OTPRender  # type: ignore
except Exception:
    _OTPRender = None  # type: ignore

# `base` can be unavailable early in boot. Resolve safely.
try:
    from direct.showbase.ShowBaseGlobal import base  # type: ignore
except Exception:
    base = None  # type: ignore

if base is None:
    try:
        import builtins
        base = getattr(builtins, 'base', None)  # type: ignore
    except Exception:
        base = None  # type: ignore


def _syncBase() -> None:
    """Refresh the module-global `base` reference.

    This module can be imported before ShowBase/ToonBase is constructed, in which
    case `base` is None at import time. Later, callers wrap begin()/end() in
    try/except (to keep loading robust), which can silently swallow AttributeError
    and make it look like OutdoorLighting never ran. Keep `base` synced at runtime.
    """
    global base
    try:
        if base is not None and getattr(base, 'render', None) is not None:
            return
    except Exception:
        pass
    try:
        from direct.showbase import ShowBaseGlobal as _SBG  # type: ignore
        b = getattr(_SBG, 'base', None)
        if b is not None:
            base = b  # type: ignore
            return
    except Exception:
        pass
    try:
        import builtins
        b = getattr(builtins, 'base', None)
        if b is not None:
            base = b  # type: ignore
    except Exception:
        pass


def _getSettingValue(key: str, default):
    """Fetch a setting from either Settings.getSetting(key, default) or Settings.get(key).
    Falls back to Panda3D ConfigVariable if base.settings is missing or does not have the key.
    """
    _syncBase()
    settings = getattr(base, 'settings', None)
    if settings is not None:
        getter = getattr(settings, 'getSetting', None) or getattr(settings, 'get', None)
        if getter is not None:
            try:
                # Try getting the setting directly first
                v = getter(key)
                if v is not None:
                    return v
            except TypeError:
                try:
                    v = getter(key, default)
                    if v is not None:
                        return v
                except Exception:
                    pass
            except Exception:
                pass
    
    # Fallback to Panda3D ConfigVariable
    if isinstance(default, bool):
        return ConfigVariableBool(key, default).value
    elif isinstance(default, (int, float)):
        return ConfigVariableDouble(key, float(default)).value
    else:
        return ConfigVariableString(key, str(default)).value


def _debugEnabled() -> bool:
    try:
        return _coerceBool(_getSettingValue('lighting-debug', False), False)
    except Exception:
        return bool(ConfigVariableBool('lighting-debug', False).value)


def _dbg(msg: str) -> None:
    if not _debugEnabled():
        return
    try:
        print(f"[OutdoorLighting] {msg}")
    except Exception:
        pass


# Bisect defaults back to normal behavior: everything enabled.
# Step ladder (see _dbgBisectState):
#   1 shader-auto  2 +lights(ambient)  3 +sun(no shadows)  4 +sun shadows
#   5 +fog/sky/bg  6 +procedural sky   7 +bloom            8 +godrays  9 +water
_DEFAULT_BISECT_STEP = 9
_BISECT_STEP = 9
_DIR_LIGHT_MODE = 'all'  # 'key' | 'key_fill' | 'all'


def setBisectStep(step: int) -> None:
    """Set the runtime feature-bisect step (script-controlled).

    This intentionally does NOT read PRC/config, so it cannot be overridden by
    external settings while we're isolating artifacts.
    """
    global _BISECT_STEP
    try:
        _BISECT_STEP = max(0, int(step))
    except Exception:
        _BISECT_STEP = 0


def setDirectionalMode(mode: str) -> None:
    """Control which directional lights are spawned (bisect within step 3/4).

    - 'key': key directional only
    - 'key_fill': key + fill
    - 'all': key + fill + rim (if profile has rim)
    """
    global _DIR_LIGHT_MODE
    m = (mode or '').strip().lower()
    _DIR_LIGHT_MODE = m if m in ('key', 'key_fill', 'all') else 'all'


_HOTKEYS_TAG = 'osl_bisect_hotkeys_bound'
_HOTKEYS_ENABLED = False


def _maybeBindBisectHotkeys() -> None:
    """Bind debug hotkeys to control bisect at runtime.

    This avoids needing any external injector. Only binds when lighting-debug is on.
    """
    if (not _HOTKEYS_ENABLED) or (not _debugEnabled()):
        return
    _syncBase()
    if base is None:
        return
    try:
        if getattr(base, 'render', None) is None:
            return
        if base.render.getPythonTag(_HOTKEYS_TAG):
            return
    except Exception:
        pass

    # ShowBase implements .accept. Bind on base itself.
    try:
        accept = getattr(base, 'accept', None)
        if accept is None:
            return
    except Exception:
        return

    def _set_step(s: int):
        setBisectStep(s)
        _dbg(f"hotkey: setBisectStep({s}) (re-enter zone to apply)")

    def _cycle_dir():
        global _DIR_LIGHT_MODE
        order = ('key', 'key_fill', 'all')
        try:
            idx = order.index(_DIR_LIGHT_MODE)
        except Exception:
            idx = 2
        _DIR_LIGHT_MODE = order[(idx + 1) % len(order)]
        _dbg(f"hotkey: setDirectionalMode('{_DIR_LIGHT_MODE}') (re-enter zone to apply)")

    try:
        # Bisect steps 0-9 on number keys.
        for s in range(10):
            accept(str(s), _set_step, [s])
        # Directional mode cycle.
        accept('f6', _cycle_dir)
        accept('shift-f6', _cycle_dir)
        try:
            base.render.setPythonTag(_HOTKEYS_TAG, True)
        except Exception:
            pass
        _dbg("bound bisect hotkeys: [0-9]=step, [F6]=cycle dir mode key->key_fill->all")
    except Exception:
        pass


def _bisectStep() -> int:
    """Runtime subsystem bisect for isolating rendering artifacts.

    Default: `_DEFAULT_BISECT_STEP` (script-controlled).

    Enable order:
      0: fully off
      1: shader-auto only
      2: + lights (ambient only)
      3: + sun key light (no shadows)
      4: + sun shadows
      5: + fog + sky tint + background clear color
      6: + procedural sky
      7: + postprocess bloom
      8: + godrays overlay
      9: + water shader/reflection
    """
    try:
        return int(_BISECT_STEP)
    except Exception:
        return int(_DEFAULT_BISECT_STEP)


def _bisectAllows(step: int) -> bool:
    return _bisectStep() >= step


def _dbgBisectState() -> None:
    if not _debugEnabled():
        return
    s = _bisectStep()
    _dbg(
        "bisect "
        f"step={s} "
        f"shaderAuto={_bisectAllows(1)} "
        f"lights={_bisectAllows(2)} "
        f"sunNoShadows={_bisectAllows(3)} "
        f"sunShadows={_bisectAllows(4)} "
        f"fog_bg_tint={_bisectAllows(5)} "
        f"proceduralSky={_bisectAllows(6)} "
        f"bloom={_bisectAllows(7)} "
        f"godrays={_bisectAllows(8)} "
        f"water={_bisectAllows(9)}"
    )


def _spawnShadowTestScene() -> None:
    """Spawn a simple cube+ground that should cast a visible shadow."""
    if not _debugEnabled():
        return
    # The shadow test scene is useful while developing shadow-map logic, but
    # should never appear in normal gameplay even if lighting-debug is enabled.
    # Enable explicitly via PRC or settings.
    try:
        if not ConfigVariableBool('lighting-shadow-test-scene', False).value:
            return
    except Exception:
        return
    _syncBase()
    try:
        if base is None or getattr(base, 'render', None) is None:
            return
    except Exception:
        return
    try:
        if base.render.getPythonTag('osl_shadow_test_spawned'):
            return
    except Exception:
        pass

    try:
        root = base.render.attachNewNode('OutdoorLightingShadowTest')
        try:
            root.wrtReparentTo(base.camera)
        except Exception:
            root.reparentTo(base.camera)
        root.setPos(0, 25, -2)

        cm = CardMaker('shadowTestGround')
        cm.setFrame(-10, 10, -10, 10)
        ground = root.attachNewNode(cm.generate())
        ground.setP(-90)
        ground.setZ(0)
        ground.setColor(0.6, 0.6, 0.6, 1)
        ground.setShaderAuto()

        # Procedural cube (no external model dependency).
        try:
            fmt = GeomVertexFormat.getV3n3()
            vdata = GeomVertexData('shadowTestCube', fmt, Geom.UHStatic)
            vwriter = GeomVertexWriter(vdata, 'vertex')
            nwriter = GeomVertexWriter(vdata, 'normal')

            # Build a cube with 6 faces (24 verts so normals are per-face).
            # Cube centered at origin, size 2.
            faces = [
                (Vec3(0, 0, 1), [Vec3(-1, -1, 1), Vec3(1, -1, 1), Vec3(1, 1, 1), Vec3(-1, 1, 1)]),   # top
                (Vec3(0, 0, -1), [Vec3(-1, 1, -1), Vec3(1, 1, -1), Vec3(1, -1, -1), Vec3(-1, -1, -1)]), # bottom
                (Vec3(0, 1, 0), [Vec3(-1, 1, 1), Vec3(1, 1, 1), Vec3(1, 1, -1), Vec3(-1, 1, -1)]),   # front
                (Vec3(0, -1, 0), [Vec3(1, -1, 1), Vec3(-1, -1, 1), Vec3(-1, -1, -1), Vec3(1, -1, -1)]), # back
                (Vec3(1, 0, 0), [Vec3(1, 1, 1), Vec3(1, -1, 1), Vec3(1, -1, -1), Vec3(1, 1, -1)]),   # right
                (Vec3(-1, 0, 0), [Vec3(-1, -1, 1), Vec3(-1, 1, 1), Vec3(-1, 1, -1), Vec3(-1, -1, -1)]), # left
            ]

            tris = GeomTriangles(Geom.UHStatic)
            vidx = 0
            for nrm, verts in faces:
                for v in verts:
                    vwriter.addData3(v)
                    nwriter.addData3(nrm)
                # two triangles per quad (0,1,2) and (0,2,3)
                tris.addVertices(vidx + 0, vidx + 1, vidx + 2)
                tris.addVertices(vidx + 0, vidx + 2, vidx + 3)
                vidx += 4

            geom = Geom(vdata)
            geom.addPrimitive(tris)
            gnode = GeomNode('shadowTestCubeGeom')
            gnode.addGeom(geom)
            cube = root.attachNewNode(gnode)
            cube.setPos(0, 0, 2.5)
            cube.setScale(2)
            cube.setColor(1, 0.2, 0.2, 1)
            cube.setShaderAuto()
        except Exception as e:
            _dbg(f"shadow test cube build failed: {e!r}")

        base.render.setShaderAuto()
        base.render.setPythonTag('osl_shadow_test_spawned', True)
        _dbg("spawned shadow test scene near camera")
    except Exception as e:
        _dbg(f"shadow test spawn failed: {e!r}")


_NORMALS_TAG = 'osl_normals_generated'


def preprocessGeometry(root: NodePath) -> None:
    """Prepare legacy geometry for lighting without forcing it unlit.

    The hook is intentionally explicit: loaders may call it for problem props or
    zone meshes that lack usable normals.  It generates only missing/zero normals
    and leaves lighting state to ``begin()``.
    """
    if root is None or root.isEmpty():
        return
    _ensureNormals(root, force=False)


def _ensureNormals(root: NodePath, force: bool = False) -> None:
    """Generate vertex normals for geometry that has none or has zeroed normals.

    Some zone meshes in this project ship without a 'normal' column or have zeroed
    normals after flattening, which makes all lighting/shadows appear flat or pitch black.
    This generates/recomputes normals for those geoms.
    """
    if root is None or root.isEmpty():
        return
    try:
        if root.getPythonTag(_NORMALS_TAG) and not force:
            return
    except Exception:
        pass

    nodes = root.findAllMatches('**/+GeomNode;+s')
    if nodes.getNumPaths() <= 0:
        return

    _dbg(f"Scanning and generating normals for {nodes.getNumPaths()} GeomNodes...")

    def _formatWithNormals(old_fmt: GeomVertexFormat) -> GeomVertexFormat:
        # Copy all arrays/columns and append a normal column to the first array.
        nf = GeomVertexFormat()
        added = False
        for ai in range(old_fmt.getNumArrays()):
            old_arr = old_fmt.getArray(ai)
            arr = GeomVertexArrayFormat()
            for ci in range(old_arr.getNumColumns()):
                col = old_arr.getColumn(ci)
                arr.addColumn(col.getName(), col.getNumComponents(),
                               col.getNumericType(), col.getContents())
            if not added:
                arr.addColumn('normal', 3, GeomEnums.NT_float32, GeomEnums.C_normal)
                added = True
            nf.addArray(arr)
        return GeomVertexFormat.registerFormat(nf)

    def _vdataWithFormat(old_vdata: GeomVertexData, new_fmt: GeomVertexFormat) -> GeomVertexData | None:
        try:
            if hasattr(old_vdata, 'convertTo'):
                return old_vdata.convertTo(new_fmt)
        except Exception:
            pass

        try:
            num_rows = old_vdata.getNumRows()
        except Exception:
            return None

        try:
            new_vdata = GeomVertexData(old_vdata)
            new_vdata.setFormat(new_fmt)
            new_vdata.setNumRows(num_rows)
        except Exception:
            try:
                new_vdata = GeomVertexData('with_normals', new_fmt, old_vdata.getUsageHint())
                new_vdata.setNumRows(num_rows)
            except Exception:
                return None

        try:
            old_fmt = old_vdata.getFormat()
            for ai in range(old_fmt.getNumArrays()):
                arr = old_fmt.getArray(ai)
                for ci in range(arr.getNumColumns()):
                    col = arr.getColumn(ci)
                    name = col.getName()
                    if name == 'normal':
                        continue
                    try:
                        if not old_vdata.hasColumn(name) or not new_vdata.hasColumn(name):
                            continue
                    except Exception:
                        continue

                    r = GeomVertexReader(old_vdata, name)
                    w = GeomVertexWriter(new_vdata, name)
                    comps = int(col.getNumComponents())
                    for row in range(num_rows):
                        r.setRow(row)
                        w.setRow(row)
                        if comps == 1:
                            w.setData1(r.getData1())
                        elif comps == 2:
                            w.setData2(r.getData2())
                        elif comps == 3:
                            w.setData3(r.getData3())
                        else:
                            w.setData4(r.getData4())
        except Exception:
            pass

        return new_vdata

    # Iterate each Geom and compute normals.
    for ni in range(nodes.getNumPaths()):
        np = nodes.getPath(ni)
        gnode = np.node()
        if not isinstance(gnode, GeomNode):
            continue
        for gi in range(gnode.getNumGeoms()):
            try:
                geom = gnode.modifyGeom(gi)
            except Exception:
                continue
            if not isinstance(geom, Geom):
                continue
            try:
                vdata = geom.modifyVertexData()
            except Exception:
                continue
            if vdata is None:
                continue

            needs_generation = bool(force)
            try:
                if not vdata.hasColumn('normal'):
                    needs_generation = True
                elif not force:
                    nr = GeomVertexReader(vdata, 'normal')
                    has_nonzero = False
                    check_count = min(vdata.getNumRows(), 30)
                    for r in range(check_count):
                        nr.setRow(r)
                        if nr.getData3().lengthSquared() > 1e-6:
                            has_nonzero = True
                            break
                    if not has_nonzero and check_count > 0:
                        needs_generation = True
            except Exception:
                continue

            if not needs_generation:
                continue

            if not vdata.hasColumn('normal'):
                try:
                    new_fmt = _formatWithNormals(vdata.getFormat())
                    new_vdata = _vdataWithFormat(vdata, new_fmt)
                    if new_vdata is None:
                        continue
                    geom.setVertexData(new_vdata)
                    vdata = geom.modifyVertexData()
                except Exception:
                    continue

            try:
                num_rows = vdata.getNumRows()
            except Exception:
                continue
            if num_rows <= 0:
                continue

            # Accumulate normals in Python list.
            acc = [Vec3(0, 0, 0) for _ in range(num_rows)]
            vr = GeomVertexReader(vdata, 'vertex')

            def _getPos(row: int) -> Vec3:
                vr.setRow(row)
                return vr.getData3()

            try:
                for pi in range(geom.getNumPrimitives()):
                    prim = geom.getPrimitive(pi)
                    if prim is None:
                        continue
                    try:
                        decomp = prim.decompose()
                    except Exception:
                        decomp = prim
                    try:
                        decomp = decomp.decompose()
                    except Exception:
                        pass
                    try:
                        npr = decomp.getNumPrimitives()
                    except Exception:
                        npr = 0
                    for t in range(npr):
                        start = decomp.getPrimitiveStart(t)
                        end = decomp.getPrimitiveEnd(t)
                        if end - start < 3:
                            continue
                        try:
                            i0 = decomp.getVertex(start + 0)
                            i1 = decomp.getVertex(start + 1)
                            i2 = decomp.getVertex(start + 2)
                        except Exception:
                            continue
                        if i0 >= num_rows or i1 >= num_rows or i2 >= num_rows:
                            continue
                        p0 = _getPos(i0)
                        p1 = _getPos(i1)
                        p2 = _getPos(i2)
                        n = (p1 - p0).cross(p2 - p0)
                        if n.lengthSquared() <= 1e-12:
                            continue
                        acc[i0] += n
                        acc[i1] += n
                        acc[i2] += n
            except Exception:
                continue

            vw = GeomVertexWriter(vdata, 'normal')
            try:
                for r in range(num_rows):
                    n = acc[r]
                    if n.lengthSquared() <= 1e-12:
                        n = Vec3(0, 0, 1)
                    else:
                        n.normalize()
                    vw.setRow(r)
                    vw.setData3(n)
            except Exception:
                pass

    try:
        root.setPythonTag(_NORMALS_TAG, True)
    except Exception:
        pass


def _forceShaderRegen(np: NodePath) -> None:
    """Force shader generator to rebuild shaders after vertex format changes."""
    if np is None or np.isEmpty():
        return
    try:
        np.clearShader()
    except Exception:
        pass
    try:
        np.setShaderAuto()
    except Exception:
        pass


def _forceShadersOnSubtree(root: NodePath) -> None:
    """Clear shader/shader-off attribs that can block auto-shaders (and shadows)."""
    if root is None or root.isEmpty():
        return
    try:
        nodes = root.findAllMatches('**;+s')
        for i in range(nodes.getNumPaths()):
            np = nodes.getPath(i)
            try:
                # Clear explicit shader attribs (including shader-off).
                try:
                    np.clearAttrib(ShaderAttrib.getClassType())
                except Exception:
                    pass
                # Clear fixed/per-node shader states.  Auto-shading is enabled
                # once on the root below so every child inherits the same
                # generated light list.  Calling setShaderAuto on each DNA
                # batch specializes hundreds of separate shaders before night
                # lamps are attached; those batches then never see the lamps.
                try:
                    np.clearShader()
                except Exception:
                    pass
            except Exception:
                pass
    except Exception:
        pass
    try:
        root.setShaderAuto()
    except Exception:
        pass


# Shader bisect — increment to add the next custom GLSL subsystem:
#   0 = modern outdoor stack off (begin() no-ops; legacy model sky in SkyUtil).
#   1 = procedural sky GLSL only (light rig + fog/tint/bg + sky dome; no HDR post,
#       bloom, god rays, or water GLSL).
#   2 = + cinematic post (stable CommonFilters bloom + render2dp sun shafts;
#       experimental full-scene RTT compositor remains opt-in).
#   3 = + water reflection GLSL (full stable pipeline).
_OUTDOOR_SHADER_BISECT_LEVEL = 3

# When bisect level is 2, enable exactly one post FX:
#   'full' / 'stack' — bloom + sun-ray overlay (default cinematic post).
#   'bloom'          — CommonFilters bloom only.
#   'godrays'        — sunrays card on render2dp only.
# Level 3+ always uses full post (bloom + god rays).
_LEVEL2_POST_SINGLE = 'full'


def _fixPostProcessRttViewports() -> None:
    """Normalize every DisplayRegion on FilterManager offscreen buffers.

    Stock FilterManager.renderSceneInto uses ``buffer.makeDisplayRegion()`` with no
    bounds; on some GL drivers that yields a sub-rectangle (often bottom-left
    quarter).  The scene is then rendered into only part of the colour texture,
    while the compositing quad still samples the full texture — exactly the
    broken layout players see.  Forcing (0,1)×(0,1) on those regions fixes it.

    NOTE: _bloomFilters is a CommonFilters instance, NOT a FilterManager.
    CommonFilters stores its FilterManager at _bloomFilters.manager — so
    buffers live at _bloomFilters.manager.buffers, not _bloomFilters.buffers.
    Getting this wrong means the bloom RTT buffers are never repaired.
    """
    # Collect FilterManager instances that own offscreen buffers (bloom, etc.).
    fms_to_fix = []
    if _postFilterManager is not None:
        fms_to_fix.append(_postFilterManager)
    if _bloomFilters is not None:
        # _bloomFilters is a CommonFilters whose internal FilterManager is at .manager
        inner = getattr(_bloomFilters, 'manager', None)
        if inner is not None:
            fms_to_fix.append(inner)

    for fm in fms_to_fix:
        try:
            fm.resizeBuffers()
        except Exception:
            pass
        try:
            for buf in getattr(fm, 'buffers', None) or []:
                if buf is None:
                    continue
                n = buf.getNumDisplayRegions()
                for i in range(n):
                    dr = buf.getDisplayRegion(i)
                    if not dr:
                        continue
                    dr.setDimensions(0.0, 1.0, 0.0, 1.0)
                    if dr.supportsPixelZoom():
                        dr.setPixelZoom(1)
                    try:
                        dr.setScissorEnabled(False)
                    except Exception:
                        pass
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────────────────

_INDOOR_STYLES: set[str] = {
    'toon', 'estate_house', 'cog', 'cogdo',
    'sellbot_lobby', 'sellbot_building',
    'cashbot_lobby', 'cashbot_building', 'mint_int', 'cashbot_mint',
    'lawbot_lobby', 'lawbot_building', 'office_int', 'lawbot_office',
    'bossbot_lobby', 'bossbot_building', 'bossbot_cc',
}

# Zone lighting profiles
# ─────────────────────────────────────────────────────────────────────────────
_ZONE_PROFILES: dict[str, dict] = {

    'tt': {
        'ambient':          (0.28, 0.34, 0.48, 1.0),
        'key':              (1.55, 1.40, 1.10, 1.0),
        'keyHpr':           (135, -42, 0),
        'fill':             (0.32, 0.42, 0.62, 1.0),
        'fillHpr':          (-45, -18, 0),
        'rim':              (0.18, 0.24, 0.40, 1.0),
        'rimHpr':           (315, -30, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       550,
        'shadowFollowDist': 450.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  0.90,
        'shadowDarknessFloor': 0.24,
        'fogColor':         (0.68, 0.80, 0.96, 1.0),
        'fogNear':          110.0,
        'fogFar':           580.0,
        'fogExponent':      None,
        'skyScale':         (1.04, 1.01, 0.91, 1.0),
        'clearColor':       (0.48, 0.70, 0.96, 1.0),
        'skyZenithColor':   (0.10, 0.48, 1.05),
        'sunUV':            (0.65, 0.78),
        'rayColor':         (1.00, 0.94, 0.70, 1.0),
        'rayIntensity':     0.55,
        'bloomIntensity':   0.28,
        'bloomThreshold':   0.70,
        'exposure':         1.00,
        'hasWater':         True,
        'waterColor':       (0.28, 0.52, 0.72, 0.88),
        'waterReflQuality': 'medium',
        'dayNightEnabled':  True,
        'cloudCoverage':    0.52,
        'cloudSpeed':       0.60,
        'cloudSharpness':   0.65,
        'turbidity':        2.2,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'tt_street': {
        'ambient':          (0.26, 0.32, 0.46, 1.0),
        'key':              (1.48, 1.34, 1.05, 1.0),
        'keyHpr':           (140, -40, 0),
        'fill':             (0.30, 0.40, 0.58, 1.0),
        'fillHpr':          (-50, -20, 0),
        'rim':              (0.15, 0.22, 0.38, 1.0),
        'rimHpr':           (310, -28, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       500,
        'shadowFollowDist': 420.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.10,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.66, 0.78, 0.94, 1.0),
        'fogNear':          95.0,
        'fogFar':           500.0,
        'fogExponent':      None,
        'skyScale':         (1.04, 1.01, 0.91, 1.0),
        'clearColor':       (0.46, 0.68, 0.94, 1.0),
        'skyZenithColor':   (0.10, 0.46, 1.02),
        'sunUV':            (0.65, 0.78),
        'rayColor':         (1.00, 0.92, 0.68, 1.0),
        'rayIntensity':     0.45,
        'bloomIntensity':   0.32,
        'bloomThreshold':   0.62,
        'exposure':         1.00,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.50,
        'cloudSpeed':       0.58,
        'cloudSharpness':   0.65,
        'turbidity':        2.2,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'dd': {
        'ambient':          (0.38, 0.44, 0.58, 1.0),
        'key':              (1.05, 1.02, 0.95, 1.0),
        'keyHpr':           (0,   -80,  0),
        'fill':             (0.30, 0.38, 0.54, 1.0),
        'fillHpr':          (180, -55,  0),
        'rim':              None,
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       580,
        'shadowFollowDist': 460.0,
        'shadowAreaScale':  0.90,
        'shadowDarknessFloor': 0.24,
        'fogColor':         (0.62, 0.74, 0.88, 1.0),
        'fogNear':          0.0,
        'fogFar':           None,
        'fogExponent':      0.005,
        'skyScale':         (0.94, 0.98, 1.06, 1.0),
        'clearColor':       (0.42, 0.64, 0.90, 1.0),
        'skyZenithColor':   (0.14, 0.45, 0.92),
        'sunUV':            (0.50, 0.88),
        'rayColor':         (0.92, 0.95, 1.00, 1.0),
        'rayIntensity':     0.05,
        'bloomIntensity':   0.18,
        'bloomThreshold':   0.68,
        'exposure':         0.96,
        'hasWater':         True,
        'waterColor':       (0.22, 0.32, 0.44, 0.90),
        'waterReflQuality': 'high',
        'dayNightEnabled':  True,
        'cloudCoverage':    0.85,
        'cloudSpeed':       0.90,
        'cloudSharpness':   0.25,
        'turbidity':        4.8,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'dd_street': {
        'ambient':          (0.36, 0.42, 0.56, 1.0),
        'key':              (1.02, 1.00, 0.92, 1.0),
        'keyHpr':           (0,   -78,  0),
        'fill':             (0.28, 0.36, 0.52, 1.0),
        'fillHpr':          (180, -52,  0),
        'rim':              None,
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       500,
        'shadowFollowDist': 420.0,
        'shadowAreaScale':  0.90,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.60, 0.72, 0.86, 1.0),
        'fogNear':          0.0,
        'fogFar':           None,
        'fogExponent':      0.006,
        'skyScale':         (0.94, 0.98, 1.06, 1.0),
        'clearColor':       (0.40, 0.62, 0.88, 1.0),
        'skyZenithColor':   (0.12, 0.42, 0.90),
        'sunUV':            (0.50, 0.85),
        'rayColor':         (0.90, 0.94, 0.98, 1.0),
        'rayIntensity':     0.05,
        'bloomIntensity':   0.16,
        'bloomThreshold':   0.70,
        'exposure':         0.95,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.85,
        'cloudSpeed':       0.95,
        'cloudSharpness':   0.25,
        'turbidity':        5.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'dg': {
        'ambient':          (0.26, 0.38, 0.52, 1.0),
        'key':              (1.52, 1.45, 1.15, 1.0),
        'keyHpr':           (170, -68, 0),
        'fill':             (0.34, 0.46, 0.68, 1.0),
        'fillHpr':          (-10, -22, 0),
        'rim':              (0.18, 0.28, 0.48, 1.0),
        'rimHpr':           (0,    55,  0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       550,
        'shadowFollowDist': 450.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  0.95,
        'shadowDarknessFloor': 0.24,
        'fogColor':         (0.64, 0.82, 1.00, 1.0),
        'fogNear':          145.0,
        'fogFar':           660.0,
        'fogExponent':      None,
        'skyScale':         (0.92, 0.98, 1.12, 1.0),
        'clearColor':       (0.42, 0.72, 1.00, 1.0),
        'skyZenithColor':   (0.05, 0.55, 1.15),
        'sunUV':            (0.54, 0.90),
        'rayColor':         (1.00, 1.00, 0.85, 1.0),
        'rayIntensity':     0.72,
        'bloomIntensity':   0.45,
        'bloomThreshold':   0.55,
        'exposure':         1.05,
        'hasWater':         True,
        'waterColor':       (0.24, 0.54, 0.36, 0.84),
        'waterReflQuality': 'medium',
        'dayNightEnabled':  True,
        'cloudCoverage':    0.32,
        'cloudSpeed':       0.50,
        'cloudSharpness':   0.72,
        'turbidity':        1.6,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'dg_street': {
        'ambient':          (0.24, 0.34, 0.48, 1.0),
        'key':              (1.45, 1.35, 1.08, 1.0),
        'keyHpr':           (172, -66, 0),
        'fill':             (0.30, 0.42, 0.62, 1.0),
        'fillHpr':          (-12, -20, 0),
        'rim':              (0.15, 0.24, 0.42, 1.0),
        'rimHpr':           (0,    52,  0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       500,
        'shadowFollowDist': 420.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.05,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.62, 0.80, 0.98, 1.0),
        'fogNear':          125.0,
        'fogFar':           580.0,
        'fogExponent':      None,
        'skyScale':         (0.92, 0.98, 1.12, 1.0),
        'clearColor':       (0.40, 0.70, 0.98, 1.0),
        'skyZenithColor':   (0.06, 0.52, 1.10),
        'sunUV':            (0.54, 0.88),
        'rayColor':         (1.00, 1.00, 0.85, 1.0),
        'rayIntensity':     0.62,
        'bloomIntensity':   0.40,
        'bloomThreshold':   0.58,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.30,
        'cloudSpeed':       0.48,
        'cloudSharpness':   0.74,
        'turbidity':        1.6,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'mm': {
        'ambient':          (0.38, 0.24, 0.34, 1.0),
        'key':              (1.35, 0.75, 0.35, 1.0),
        'keyHpr':           (260, -18, 0),
        'fill':             (0.24, 0.18, 0.44, 1.0),
        'fillHpr':          (80,  -28, 0),
        'rim':              (0.85, 0.42, 0.18, 1.0),
        'rimHpr':           (258, -10,  0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       550,
        'shadowFollowDist': 450.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.10,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.82, 0.46, 0.22, 1.0),
        'fogNear':          15.0,
        'fogFar':           380.0,
        'fogExponent':      None,
        'skyScale':         (1.24, 0.80, 0.58, 1.0),
        'clearColor':       (0.85, 0.45, 0.20, 1.0),
        'skyZenithColor':   (0.35, 0.22, 0.65),
        'sunUV':            (0.15, 0.48),
        'rayColor':         (1.00, 0.65, 0.28, 1.0),
        'rayIntensity':     0.65,
        'bloomIntensity':   0.48,
        'bloomThreshold':   0.58,
        'exposure':         1.02,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.48,
        'cloudSpeed':       0.75,
        'cloudSharpness':   0.55,
        'turbidity':        3.2,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'mm_street': {
        'ambient':          (0.36, 0.22, 0.32, 1.0),
        'key':              (1.30, 0.70, 0.32, 1.0),
        'keyHpr':           (258, -16, 0),
        'fill':             (0.22, 0.16, 0.42, 1.0),
        'fillHpr':          (78,  -26, 0),
        'rim':              (0.80, 0.40, 0.16, 1.0),
        'rimHpr':           (256, -8,  0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       500,
        'shadowFollowDist': 420.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.05,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.80, 0.44, 0.20, 1.0),
        'fogNear':          12.0,
        'fogFar':           320.0,
        'fogExponent':      None,
        'skyScale':         (1.24, 0.80, 0.58, 1.0),
        'clearColor':       (0.82, 0.42, 0.18, 1.0),
        'skyZenithColor':   (0.32, 0.20, 0.62),
        'sunUV':            (0.15, 0.46),
        'rayColor':         (1.00, 0.62, 0.25, 1.0),
        'rayIntensity':     0.58,
        'bloomIntensity':   0.44,
        'bloomThreshold':   0.60,
        'exposure':         1.02,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.46,
        'cloudSpeed':       0.78,
        'cloudSharpness':   0.52,
        'turbidity':        3.2,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'br': {
        'ambient':          (0.46, 0.54, 0.72, 1.0),
        'key':              (0.75, 0.85, 1.05, 1.0),
        'keyHpr':           (180, -22, 0),
        'fill':             (0.38, 0.46, 0.62, 1.0),
        'fillHpr':          (0,   -12, 0),
        'rim':              (0.48, 0.56, 0.74, 1.0),
        'rimHpr':           (178, -6,   0),
        'shadowCaster':     False,
        'shadowRes':        512,
        'shadowArea':       580,
        'shadowFollowDist': 460.0,
        'shadowDarknessFloor': 0.26,
        'fogColor':         (0.72, 0.82, 0.96, 1.0),
        'fogNear':          0.0,
        'fogFar':           None,
        'fogExponent':      0.012,
        'skyScale':         (0.84, 0.92, 1.10, 1.0),
        'clearColor':       (0.68, 0.80, 0.96, 1.0),
        'skyZenithColor':   (0.24, 0.52, 0.95),
        'sunUV':            (0.50, 0.44),
        'rayColor':         (0.88, 0.94, 1.00, 1.0),
        'rayIntensity':     0.10,
        'bloomIntensity':   0.20,
        'bloomThreshold':   0.70,
        'exposure':         0.94,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.75,
        'cloudSpeed':       1.20,
        'cloudSharpness':   0.20,
        'turbidity':        5.5,
        'starBrightness':   0.0,
        'moonEnabled':      False,
        'auroraEnabled':    True,
    },

    'br_street': {
        'ambient':          (0.44, 0.52, 0.70, 1.0),
        'key':              (0.72, 0.82, 1.02, 1.0),
        'keyHpr':           (180, -20, 0),
        'fill':             (0.36, 0.44, 0.60, 1.0),
        'fillHpr':          (0,   -10, 0),
        'rim':              (0.46, 0.54, 0.72, 1.0),
        'rimHpr':           (178, -4,   0),
        'shadowCaster':     False,
        'shadowRes':        512,
        'shadowArea':       500,
        'shadowFollowDist': 420.0,
        'shadowDarknessFloor': 0.24,
        'fogColor':         (0.70, 0.80, 0.94, 1.0),
        'fogNear':          0.0,
        'fogFar':           None,
        'fogExponent':      0.014,
        'skyScale':         (0.84, 0.92, 1.10, 1.0),
        'clearColor':       (0.66, 0.78, 0.94, 1.0),
        'skyZenithColor':   (0.22, 0.50, 0.92),
        'sunUV':            (0.50, 0.42),
        'rayColor':         (0.86, 0.92, 1.00, 1.0),
        'rayIntensity':     0.08,
        'bloomIntensity':   0.18,
        'bloomThreshold':   0.72,
        'exposure':         0.92,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.78,
        'cloudSpeed':       1.25,
        'cloudSharpness':   0.18,
        'turbidity':        5.6,
        'starBrightness':   0.0,
        'moonEnabled':      False,
        'auroraEnabled':    True,
    },

    'dl': {
        'ambient':          (0.18, 0.16, 0.32, 1.0),
        'key':              (0.45, 0.52, 0.85, 1.0),
        'keyHpr':           (225, -55, 0),
        'fill':             (0.06, 0.08, 0.18, 1.0),
        'fillHpr':          (45,  -15, 0),
        'rim':              (0.30, 0.38, 0.65, 1.0),
        'rimHpr':           (220, -50, 0),
        'shadowCaster':     False,
        'shadowRes':        1024,
        'shadowArea':       550,
        'shadowFollowDist': 450.0,
        'shadowDarknessFloor': 0.12,
        'fogColor':         (0.05, 0.07, 0.14, 1.0),
        'fogNear':          0.0,
        'fogFar':           None,
        'fogExponent':      0.005,
        'skyScale':         (0.45, 0.50, 0.80, 1.0),
        'clearColor':       (0.03, 0.04, 0.10, 1.0),
        'skyZenithColor':   (0.02, 0.04, 0.12),
        'sunUV':            (0.30, 0.82),
        'rayColor':         (0.58, 0.70, 1.00, 1.0),
        'rayIntensity':     0.0,
        'bloomIntensity':   0.28,
        'bloomThreshold':   0.65,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.18,
        'cloudSpeed':       0.20,
        'cloudSharpness':   0.50,
        'turbidity':        1.5,
        'starBrightness':   0.95,
        'moonEnabled':      True,
        'moonDir':          (225, -55, 0),
        'auroraEnabled':    True,
    },

    'dl_street': {
        'ambient':          (0.16, 0.14, 0.30, 1.0),
        'key':              (0.42, 0.48, 0.80, 1.0),
        'keyHpr':           (225, -52, 0),
        'fill':             (0.05, 0.07, 0.16, 1.0),
        'fillHpr':          (42,  -14, 0),
        'rim':              (0.28, 0.35, 0.60, 1.0),
        'rimHpr':           (222, -48, 0),
        'shadowCaster':     False,
        'shadowRes':        1024,
        'shadowArea':       500,
        'shadowFollowDist': 420.0,
        'shadowDarknessFloor': 0.12,
        'fogColor':         (0.05, 0.06, 0.12, 1.0),
        'fogNear':          0.0,
        'fogFar':           None,
        'fogExponent':      0.005,
        'skyScale':         (0.45, 0.50, 0.80, 1.0),
        'clearColor':       (0.03, 0.04, 0.10, 1.0),
        'skyZenithColor':   (0.02, 0.04, 0.12),
        'sunUV':            (0.30, 0.80),
        'rayColor':         (0.56, 0.68, 1.00, 1.0),
        'rayIntensity':     0.00,
        'bloomIntensity':   0.26,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.18,
        'cloudSpeed':       0.18,
        'cloudSharpness':   0.50,
        'turbidity':        1.5,
        'starBrightness':   0.95,
        'moonEnabled':      True,
        'moonDir':          (225, -55, 0),
        'auroraEnabled':    True,
    },

    'gs': {
        'ambient':          (0.30, 0.32, 0.40, 1.0),
        'key':              (1.10, 1.00, 0.82, 1.0),
        'keyHpr':           (130, -48, 0),
        'fill':             (0.24, 0.28, 0.40, 1.0),
        'fillHpr':          (-50, -30, 0),
        'rim':              None,
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       650,
        'shadowFollowDist': 520.0,
        'shadowResScale':   0.90,
        'shadowAreaScale':  1.20,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.78, 0.76, 0.68, 1.0),
        'fogNear':          60.0,
        'fogFar':           380.0,
        'fogExponent':      None,
        'skyScale':         (1.06, 1.02, 0.84, 1.0),
        'clearColor':       (0.56, 0.64, 0.72, 1.0),
        'sunUV':            (0.60, 0.74),
        'rayColor':         (1.00, 0.92, 0.70, 1.0),
        'rayIntensity':     0.26,
        'bloomIntensity':   0.24,
        'bloomThreshold':   0.64,
        'exposure':         1.00,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.32,
        'cloudSpeed':       0.90,
        'cloudSharpness':   0.50,
        'turbidity':        3.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'golf_course': {
        'ambient':          (0.24, 0.32, 0.28, 1.0),
        'key':              (1.20, 1.12, 0.88, 1.0),
        'keyHpr':           (160, -58, 0),
        'fill':             (0.20, 0.30, 0.22, 1.0),
        'fillHpr':          (-20, -28, 0),
        'rim':              (0.12, 0.18, 0.10, 1.0),
        'rimHpr':           (0,    48,  0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       650,
        'shadowFollowDist': 520.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.30,
        'shadowDarknessFloor': 0.18,
        'fogColor':         (0.60, 0.78, 0.60, 1.0),
        'fogNear':          100.0,
        'fogFar':           520.0,
        'fogExponent':      None,
        'skyScale':         (0.88, 1.00, 0.82, 1.0),
        'clearColor':       (0.40, 0.68, 0.44, 1.0),
        'sunUV':            (0.56, 0.88),
        'rayColor':         (1.00, 1.00, 0.80, 1.0),
        'rayIntensity':     0.50,
        'bloomIntensity':   0.38,
        'bloomThreshold':   0.60,
        'exposure':         1.05,
        'hasWater':         True,
        'waterColor':       (0.28, 0.60, 0.40, 0.82),
        'waterReflQuality': 'medium',
        'dayNightEnabled':  False,
        'cloudCoverage':    0.30,
        'cloudSpeed':       0.65,
        'cloudSharpness':   0.60,
        'turbidity':        2.2,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'oz': {
        'ambient':          (0.28, 0.38, 0.32, 1.0),
        'key':              (1.45, 1.35, 1.05, 1.0),
        'keyHpr':           (150, -52, 0),
        'fill':             (0.30, 0.40, 0.36, 1.0),
        'fillHpr':          (-30, -25, 0),
        'rim':              (0.18, 0.26, 0.22, 1.0),
        'rimHpr':           (330, -35, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       600,
        'shadowFollowDist': 500.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  0.95,
        'shadowDarknessFloor': 0.24,
        'fogColor':         (0.68, 0.82, 0.74, 1.0),
        'fogNear':          110.0,
        'fogFar':           600.0,
        'fogExponent':      None,
        'skyScale':         (0.96, 1.02, 0.92, 1.0),
        'clearColor':       (0.44, 0.70, 0.92, 1.0),
        'skyZenithColor':   (0.08, 0.50, 1.02),
        'sunUV':            (0.60, 0.82),
        'rayColor':         (1.00, 0.96, 0.78, 1.0),
        'rayIntensity':     0.55,
        'bloomIntensity':   0.32,
        'bloomThreshold':   0.65,
        'exposure':         1.00,
        'hasWater':         True,
        'waterColor':       (0.22, 0.52, 0.44, 0.88),
        'waterReflQuality': 'medium',
        'dayNightEnabled':  True,
        'cloudCoverage':    0.40,
        'cloudSpeed':       0.55,
        'cloudSharpness':   0.60,
        'turbidity':        2.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'oz_street': {
        'ambient':          (0.26, 0.36, 0.30, 1.0),
        'key':              (1.40, 1.30, 1.00, 1.0),
        'keyHpr':           (152, -50, 0),
        'fill':             (0.28, 0.38, 0.34, 1.0),
        'fillHpr':          (-32, -22, 0),
        'rim':              (0.16, 0.24, 0.20, 1.0),
        'rimHpr':           (328, -32, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       520,
        'shadowFollowDist': 420.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.05,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.66, 0.80, 0.72, 1.0),
        'fogNear':          95.0,
        'fogFar':           520.0,
        'fogExponent':      None,
        'skyScale':         (0.96, 1.02, 0.92, 1.0),
        'clearColor':       (0.42, 0.68, 0.90, 1.0),
        'skyZenithColor':   (0.08, 0.48, 1.00),
        'sunUV':            (0.60, 0.80),
        'rayColor':         (1.00, 0.94, 0.75, 1.0),
        'rayIntensity':     0.48,
        'bloomIntensity':   0.30,
        'bloomThreshold':   0.65,
        'exposure':         1.00,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.38,
        'cloudSpeed':       0.52,
        'cloudSharpness':   0.62,
        'turbidity':        2.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'gz': {
        'ambient':          (0.26, 0.34, 0.30, 1.0),
        'key':              (1.35, 1.25, 0.95, 1.0),
        'keyHpr':           (155, -55, 0),
        'fill':             (0.24, 0.32, 0.26, 1.0),
        'fillHpr':          (-25, -25, 0),
        'rim':              (0.14, 0.20, 0.14, 1.0),
        'rimHpr':           (5,    45,  0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       600,
        'shadowFollowDist': 500.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.10,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.62, 0.80, 0.66, 1.0),
        'fogNear':          100.0,
        'fogFar':           540.0,
        'fogExponent':      None,
        'skyScale':         (0.92, 1.02, 0.88, 1.0),
        'clearColor':       (0.42, 0.70, 0.50, 1.0),
        'sunUV':            (0.58, 0.86),
        'rayColor':         (1.00, 1.00, 0.80, 1.0),
        'rayIntensity':     0.50,
        'bloomIntensity':   0.35,
        'bloomThreshold':   0.60,
        'exposure':         1.02,
        'hasWater':         True,
        'waterColor':       (0.26, 0.58, 0.42, 0.84),
        'waterReflQuality': 'medium',
        'dayNightEnabled':  True,
        'cloudCoverage':    0.32,
        'cloudSpeed':       0.60,
        'cloudSharpness':   0.62,
        'turbidity':        2.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'party': {
        'ambient':          (0.32, 0.36, 0.48, 1.0),
        'key':              (1.50, 1.38, 1.12, 1.0),
        'keyHpr':           (125, -45, 0),
        'fill':             (0.32, 0.40, 0.58, 1.0),
        'fillHpr':          (-55, -20, 0),
        'rim':              (0.20, 0.26, 0.42, 1.0),
        'rimHpr':           (305, -30, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       560,
        'shadowFollowDist': 460.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  0.95,
        'shadowDarknessFloor': 0.24,
        'fogColor':         (0.70, 0.82, 0.98, 1.0),
        'fogNear':          120.0,
        'fogFar':           600.0,
        'fogExponent':      None,
        'skyScale':         (1.04, 1.02, 0.94, 1.0),
        'clearColor':       (0.50, 0.72, 0.98, 1.0),
        'skyZenithColor':   (0.12, 0.50, 1.08),
        'sunUV':            (0.62, 0.78),
        'rayColor':         (1.00, 0.95, 0.76, 1.0),
        'rayIntensity':     0.55,
        'bloomIntensity':   0.35,
        'bloomThreshold':   0.65,
        'exposure':         1.00,
        'hasWater':         True,
        'waterColor':       (0.26, 0.50, 0.70, 0.88),
        'waterReflQuality': 'medium',
        'dayNightEnabled':  True,
        'cloudCoverage':    0.42,
        'cloudSpeed':       0.55,
        'cloudSharpness':   0.60,
        'turbidity':        2.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'tutorial': {
        'ambient':          (0.28, 0.34, 0.48, 1.0),
        'key':              (1.52, 1.38, 1.08, 1.0),
        'keyHpr':           (135, -42, 0),
        'fill':             (0.32, 0.42, 0.60, 1.0),
        'fillHpr':          (-45, -18, 0),
        'rim':              (0.18, 0.24, 0.40, 1.0),
        'rimHpr':           (315, -30, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       500,
        'shadowFollowDist': 420.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.00,
        'shadowDarknessFloor': 0.24,
        'fogColor':         (0.68, 0.80, 0.96, 1.0),
        'fogNear':          100.0,
        'fogFar':           520.0,
        'fogExponent':      None,
        'skyScale':         (1.04, 1.01, 0.91, 1.0),
'sunUV':            (0.65, 0.78),
        'rayColor':         (1.00, 0.94, 0.70, 1.0),
        'rayIntensity':     0.50,
        'bloomIntensity':   0.30,
        'bloomThreshold':   0.65,
        'exposure':         1.00,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.48,
        'cloudSpeed':       0.55,
        'cloudSharpness':   0.65,
        'turbidity':        2.2,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Sellbot HQ (exterior) ─────────────────────────────────────────────────
    'sellbot_hq': {
        'ambient':          (0.24, 0.20, 0.28, 1.0),
        'key':              (0.95, 0.82, 1.05, 1.0),
        'keyHpr':           (135, -55, 0),
        'fill':             (0.20, 0.17, 0.25, 1.0),
        'fillHpr':          (-45, -25, 0),
        'rim':              (0.28, 0.22, 0.38, 1.0),
        'rimHpr':           (315, -45, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       620,
        'shadowFollowDist': 500.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.00,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.44, 0.38, 0.50, 1.0),
        'fogNear':          80.0,
        'fogFar':           480.0,
        'fogExponent':      None,
        'skyScale':         (0.75, 0.65, 0.90, 1.0),
        'clearColor':       (0.34, 0.28, 0.40, 1.0),
        'skyZenithColor':   (0.22, 0.18, 0.32),
        'sunUV':            (0.50, 0.70),
        'rayColor':         (0.72, 0.55, 0.90, 1.0),
        'rayIntensity':     0.20,
        'bloomIntensity':   0.20,
        'bloomThreshold':   0.70,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.88,
        'cloudSpeed':       0.30,
        'cloudSharpness':   0.20,
        'turbidity':        6.5,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Sellbot Factory interior ──────────────────────────────────────────────
    'factory_int': {
        'ambient':          (0.24, 0.20, 0.28, 1.0),
        'key':              (0.95, 0.82, 1.05, 1.0),
        'keyHpr':           (135, -55, 0),
        'fill':             (0.20, 0.17, 0.25, 1.0),
        'fillHpr':          (-45, -25, 0),
        'rim':              (0.28, 0.22, 0.38, 1.0),
        'rimHpr':           (315, -45, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       620,
        'shadowFollowDist': 500.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.00,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.44, 0.38, 0.50, 1.0),
        'fogNear':          80.0,
        'fogFar':           480.0,
        'fogExponent':      None,
        'skyScale':         (0.75, 0.65, 0.90, 1.0),
        'clearColor':       (0.34, 0.28, 0.40, 1.0),
        'skyZenithColor':   (0.22, 0.18, 0.32),
        'sunUV':            (0.50, 0.70),
        'rayColor':         (0.00, 0.00, 0.00, 0.0),
        'rayIntensity':     0.00,
        'bloomIntensity':   0.20,
        'bloomThreshold':   0.70,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.88,
        'cloudSpeed':       0.30,
        'cloudSharpness':   0.20,
        'turbidity':        6.5,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Cashbot HQ (exterior) ─────────────────────────────────────────────────
    'cashbot_hq': {
        'ambient':          (0.22, 0.28, 0.22, 1.0),
        'key':              (0.88, 1.02, 0.82, 1.0),
        'keyHpr':           (120, -50, 0),
        'fill':             (0.18, 0.24, 0.18, 1.0),
        'fillHpr':          (-60, -25, 0),
        'rim':              (0.26, 0.36, 0.24, 1.0),
        'rimHpr':           (300, -40, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       620,
        'shadowFollowDist': 500.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.00,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.40, 0.48, 0.38, 1.0),
        'fogNear':          80.0,
        'fogFar':           500.0,
        'fogExponent':      None,
        'skyScale':         (0.70, 0.88, 0.68, 1.0),
        'clearColor':       (0.30, 0.38, 0.28, 1.0),
        'skyZenithColor':   (0.18, 0.28, 0.20),
        'sunUV':            (0.50, 0.70),
        'rayColor':         (0.68, 0.95, 0.55, 1.0),
        'rayIntensity':     0.20,
        'bloomIntensity':   0.22,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.85,
        'cloudSpeed':       0.35,
        'cloudSharpness':   0.20,
        'turbidity':        6.8,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Cashbot Mint interior ─────────────────────────────────────────────────
    'mint_int': {
        'ambient':          (0.22, 0.28, 0.22, 1.0),
        'key':              (0.72, 0.86, 0.70, 1.0),
        'keyHpr':           (90, -80, 0),
        'fill':             (0.18, 0.22, 0.18, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.24, 0.32, 0.22, 1.0),
        'rimHpr':           (88, -75, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       450,
        'shadowFollowDist': 380.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.24, 0.32, 0.24, 1.0),
        'fogNear':          40.0,
        'fogFar':           340.0,
        'fogExponent':      None,
        'skyScale':         (0.52, 0.68, 0.52, 1.0),
        'clearColor':       (0.18, 0.24, 0.18, 1.0),
        'sunUV':            (0.50, 0.50),
        'rayColor':         (0.00, 0.00, 0.00, 0.0),
        'rayIntensity':     0.00,
        'bloomIntensity':   0.12,
        'bloomThreshold':   0.62,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Lawbot HQ (exterior) ──────────────────────────────────────────────────
    'lawbot_hq': {
        'ambient':          (0.22, 0.26, 0.34, 1.0),
        'key':              (0.85, 0.92, 1.08, 1.0),
        'keyHpr':           (140, -55, 0),
        'fill':             (0.18, 0.22, 0.28, 1.0),
        'fillHpr':          (-40, -25, 0),
        'rim':              (0.24, 0.30, 0.42, 1.0),
        'rimHpr':           (320, -45, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       620,
        'shadowFollowDist': 500.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.00,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.38, 0.44, 0.54, 1.0),
        'fogNear':          85.0,
        'fogFar':           520.0,
        'fogExponent':      None,
        'skyScale':         (0.68, 0.78, 0.95, 1.0),
        'clearColor':       (0.28, 0.34, 0.44, 1.0),
        'skyZenithColor':   (0.16, 0.24, 0.38),
        'sunUV':            (0.50, 0.70),
        'rayColor':         (0.65, 0.80, 1.00, 1.0),
        'rayIntensity':     0.18,
        'bloomIntensity':   0.18,
        'bloomThreshold':   0.72,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.90,
        'cloudSpeed':       0.25,
        'cloudSharpness':   0.15,
        'turbidity':        6.2,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Lawbot Office interior ────────────────────────────────────────────────
    'office_int': {
        'ambient':          (0.22, 0.26, 0.34, 1.0),
        'key':              (0.75, 0.82, 0.98, 1.0),
        'keyHpr':           (0, -85, 0),
        'fill':             (0.18, 0.22, 0.28, 1.0),
        'fillHpr':          (180, -60, 0),
        'rim':              (0.22, 0.28, 0.36, 1.0),
        'rimHpr':           (90, -45, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       450,
        'shadowFollowDist': 380.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.26, 0.30, 0.40, 1.0),
        'fogNear':          40.0,
        'fogFar':           340.0,
        'fogExponent':      None,
        'skyScale':         (0.54, 0.64, 0.80, 1.0),
        'clearColor':       (0.18, 0.22, 0.30, 1.0),
        'sunUV':            (0.50, 0.50),
        'rayColor':         (0.00, 0.00, 0.00, 0.0),
        'rayIntensity':     0.00,
        'bloomIntensity':   0.12,
        'bloomThreshold':   0.65,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Bossbot HQ (exterior) ─────────────────────────────────────────────────
    'bossbot_hq': {
        'ambient':          (0.26, 0.22, 0.18, 1.0),
        'key':              (0.98, 0.85, 0.70, 1.0),
        'keyHpr':           (145, -52, 0),
        'fill':             (0.22, 0.18, 0.15, 1.0),
        'fillHpr':          (-35, -25, 0),
        'rim':              (0.32, 0.25, 0.18, 1.0),
        'rimHpr':           (325, -40, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       620,
        'shadowFollowDist': 500.0,
        'shadowResScale':   1.00,
        'shadowAreaScale':  1.00,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.48, 0.42, 0.36, 1.0),
        'fogNear':          80.0,
        'fogFar':           500.0,
        'fogExponent':      None,
        'skyScale':         (0.88, 0.74, 0.60, 1.0),
        'clearColor':       (0.38, 0.32, 0.26, 1.0),
        'skyZenithColor':   (0.28, 0.22, 0.16),
        'sunUV':            (0.50, 0.70),
        'rayColor':         (0.95, 0.78, 0.55, 1.0),
        'rayIntensity':     0.22,
        'bloomIntensity':   0.22,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.92,
        'cloudSpeed':       0.20,
        'cloudSharpness':   0.15,
        'turbidity':        7.2,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Bossbot Country Club interior ─────────────────────────────────────────
    'bossbot_cc': {
        'ambient':          (0.26, 0.22, 0.18, 1.0),
        'key':              (0.85, 0.74, 0.60, 1.0),
        'keyHpr':           (90, -60, 0),
        'fill':             (0.20, 0.18, 0.15, 1.0),
        'fillHpr':          (-80, -35, 0),
        'rim':              (0.28, 0.22, 0.16, 1.0),
        'rimHpr':           (88, -55, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       450,
        'shadowFollowDist': 380.0,
        'shadowDarknessFloor': 0.18,
        'fogColor':         (0.08, 0.04, 0.02, 1.0),
        'fogNear':          0.0,
        'fogFar':           None,
        'fogExponent':      0.018,
        'skyScale':         (0.40, 0.28, 0.16, 1.0),
        'clearColor':       (0.05, 0.03, 0.01, 1.0),
        'sunUV':            (0.50, 0.50),
        'rayColor':         (0.00, 0.00, 0.00, 0.0),
        'rayIntensity':     0.00,
        'bloomIntensity':   0.14,
        'bloomThreshold':   0.60,
        'exposure':         1.35,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Generic fallbacks ─────────────────────────────────────────────────────
    
    # ── Toon interior ────────────────────────────────────────────────────────
    'toon': {
        'ambient':          (0.44, 0.40, 0.36, 1.0),
        'key':              (1.30, 1.18, 0.95, 1.0),
        'keyHpr':           (120, -75, 0),
        'fill':             (0.30, 0.28, 0.24, 1.0),
        'fillHpr':          (-60, -35, 0),
        'rim':              (0.18, 0.16, 0.14, 1.0),
        'rimHpr':           (300, -45, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       220,
        'shadowFollowDist': 180.0,
        'shadowDarknessFloor': 0.25,
        'fogColor':         (0.35, 0.32, 0.28, 1.0),
        'fogNear':          80.0,
        'fogFar':           450.0,
        'fogExponent':      None,
        'skyScale':         (0.85, 0.85, 0.85, 1.0),
        'clearColor':       (0.12, 0.10, 0.08, 1.0),
        'bloomIntensity':   0.18,
        'bloomThreshold':   0.70,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Estate House interior ────────────────────────────────────────────────
    'estate_house': {
        'ambient':          (0.42, 0.38, 0.32, 1.0),
        'key':              (1.22, 1.10, 0.88, 1.0),
        'keyHpr':           (110, -75, 0),
        'fill':             (0.28, 0.25, 0.22, 1.0),
        'fillHpr':          (-70, -35, 0),
        'rim':              (0.16, 0.14, 0.12, 1.0),
        'rimHpr':           (290, -45, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       200,
        'shadowFollowDist': 160.0,
        'shadowDarknessFloor': 0.24,
        'fogColor':         (0.32, 0.28, 0.24, 1.0),
        'fogNear':          80.0,
        'fogFar':           400.0,
        'fogExponent':      None,
        'skyScale':         (0.85, 0.85, 0.85, 1.0),
        'clearColor':       (0.10, 0.08, 0.06, 1.0),
        'bloomIntensity':   0.18,
        'bloomThreshold':   0.70,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Cogdominium interior ─────────────────────────────────────────────────
    'cogdo': {
        'ambient':          (0.24, 0.26, 0.30, 1.0),
        'key':              (0.80, 0.85, 0.95, 1.0),
        'keyHpr':           (85, -80, 0),
        'fill':             (0.16, 0.18, 0.22, 1.0),
        'fillHpr':          (-75, -40, 0),
        'rim':              (0.20, 0.22, 0.26, 1.0),
        'rimHpr':           (85, -65, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       220,
        'shadowFollowDist': 180.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.22, 0.24, 0.28, 1.0),
        'fogNear':          50.0,
        'fogFar':           350.0,
        'fogExponent':      None,
        'skyScale':         (0.50, 0.55, 0.65, 1.0),
        'clearColor':       (0.08, 0.10, 0.12, 1.0),
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Sellbot Lobby interior ───────────────────────────────────────────────
    'sellbot_lobby': {
        'ambient':          (0.24, 0.20, 0.28, 1.0),
        'key':              (0.82, 0.72, 0.92, 1.0),
        'keyHpr':           (90, -78, 0),
        'fill':             (0.18, 0.15, 0.22, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.24, 0.18, 0.30, 1.0),
        'rimHpr':           (88, -70, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       240,
        'shadowFollowDist': 200.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.26, 0.22, 0.30, 1.0),
        'fogNear':          50.0,
        'fogFar':           360.0,
        'fogExponent':      None,
        'skyScale':         (0.55, 0.45, 0.65, 1.0),
        'clearColor':       (0.16, 0.12, 0.20, 1.0),
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Sellbot Building interior ────────────────────────────────────────────
    'sellbot_building': {
        'ambient':          (0.24, 0.20, 0.28, 1.0),
        'key':              (0.80, 0.70, 0.90, 1.0),
        'keyHpr':           (90, -78, 0),
        'fill':             (0.18, 0.15, 0.22, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.24, 0.18, 0.30, 1.0),
        'rimHpr':           (88, -70, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       220,
        'shadowFollowDist': 180.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.26, 0.22, 0.30, 1.0),
        'fogNear':          50.0,
        'fogFar':           360.0,
        'fogExponent':      None,
        'skyScale':         (0.55, 0.45, 0.65, 1.0),
        'clearColor':       (0.16, 0.12, 0.20, 1.0),
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Cashbot Lobby interior ───────────────────────────────────────────────
    'cashbot_lobby': {
        'ambient':          (0.22, 0.28, 0.22, 1.0),
        'key':              (0.76, 0.90, 0.74, 1.0),
        'keyHpr':           (90, -78, 0),
        'fill':             (0.16, 0.20, 0.16, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.22, 0.30, 0.20, 1.0),
        'rimHpr':           (88, -70, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       240,
        'shadowFollowDist': 200.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.22, 0.28, 0.22, 1.0),
        'fogNear':          50.0,
        'fogFar':           360.0,
        'fogExponent':      None,
        'skyScale':         (0.50, 0.65, 0.50, 1.0),
        'clearColor':       (0.14, 0.18, 0.14, 1.0),
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Cashbot Building interior ────────────────────────────────────────────
    'cashbot_building': {
        'ambient':          (0.22, 0.28, 0.22, 1.0),
        'key':              (0.74, 0.88, 0.72, 1.0),
        'keyHpr':           (90, -78, 0),
        'fill':             (0.16, 0.20, 0.16, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.22, 0.30, 0.20, 1.0),
        'rimHpr':           (88, -70, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       220,
        'shadowFollowDist': 180.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.22, 0.28, 0.22, 1.0),
        'fogNear':          50.0,
        'fogFar':           360.0,
        'fogExponent':      None,
        'skyScale':         (0.50, 0.65, 0.50, 1.0),
        'clearColor':       (0.14, 0.18, 0.14, 1.0),
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Lawbot Lobby interior ────────────────────────────────────────────────
    'lawbot_lobby': {
        'ambient':          (0.22, 0.26, 0.34, 1.0),
        'key':              (0.78, 0.85, 1.02, 1.0),
        'keyHpr':           (90, -78, 0),
        'fill':             (0.16, 0.20, 0.26, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.22, 0.26, 0.34, 1.0),
        'rimHpr':           (88, -70, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       240,
        'shadowFollowDist': 200.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.24, 0.28, 0.36, 1.0),
        'fogNear':          50.0,
        'fogFar':           360.0,
        'fogExponent':      None,
        'skyScale':         (0.50, 0.60, 0.75, 1.0),
        'clearColor':       (0.14, 0.16, 0.22, 1.0),
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Lawbot Building interior ─────────────────────────────────────────────
    'lawbot_building': {
        'ambient':          (0.22, 0.26, 0.34, 1.0),
        'key':              (0.76, 0.84, 1.00, 1.0),
        'keyHpr':           (90, -78, 0),
        'fill':             (0.16, 0.20, 0.26, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.22, 0.26, 0.34, 1.0),
        'rimHpr':           (88, -70, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       220,
        'shadowFollowDist': 180.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.24, 0.28, 0.36, 1.0),
        'fogNear':          50.0,
        'fogFar':           360.0,
        'fogExponent':      None,
        'skyScale':         (0.50, 0.60, 0.75, 1.0),
        'clearColor':       (0.14, 0.16, 0.22, 1.0),
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Bossbot Lobby interior ───────────────────────────────────────────────
    'bossbot_lobby': {
        'ambient':          (0.26, 0.22, 0.18, 1.0),
        'key':              (0.88, 0.78, 0.64, 1.0),
        'keyHpr':           (90, -78, 0),
        'fill':             (0.20, 0.17, 0.14, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.26, 0.20, 0.16, 1.0),
        'rimHpr':           (88, -70, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       240,
        'shadowFollowDist': 200.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.28, 0.24, 0.20, 1.0),
        'fogNear':          50.0,
        'fogFar':           360.0,
        'fogExponent':      None,
        'skyScale':         (0.65, 0.55, 0.45, 1.0),
        'clearColor':       (0.16, 0.12, 0.08, 1.0),
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    # ── Bossbot Building interior ────────────────────────────────────────────
    'bossbot_building': {
        'ambient':          (0.26, 0.22, 0.18, 1.0),
        'key':              (0.86, 0.76, 0.62, 1.0),
        'keyHpr':           (90, -78, 0),
        'fill':             (0.20, 0.17, 0.14, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.26, 0.20, 0.16, 1.0),
        'rimHpr':           (88, -70, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       220,
        'shadowFollowDist': 180.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.28, 0.24, 0.20, 1.0),
        'fogNear':          50.0,
        'fogFar':           360.0,
        'fogExponent':      None,
        'skyScale':         (0.65, 0.55, 0.45, 1.0),
        'clearColor':       (0.16, 0.12, 0.08, 1.0),
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },

    'playground': {
        'ambient':          (0.34, 0.40, 0.54, 1.0),
        'key':              (1.22, 1.14, 0.94, 1.0),
        'keyHpr':           (118, -52, 0),
        'fill':             (0.26, 0.34, 0.48, 1.0),
        'fillHpr':          (-48, -42, 0),
        'rim':              (0.14, 0.20, 0.34, 1.0),
        'rimHpr':           (300, -30, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       550,
        'shadowFollowDist': 450.0,
        'shadowResScale':   0.90,
        'shadowAreaScale':  1.20,
        'shadowDarknessFloor': 0.22,
        'fogColor':         (0.65, 0.78, 0.94, 1.0),
        'fogNear':          120.0,
        'fogFar':           560.0,
        'fogExponent':      None,
        'skyScale':         (1.00, 1.00, 1.00, 1.0),
        'clearColor':       (0.42, 0.62, 0.84, 1.0),
        'sunUV':            (0.62, 0.76),
        'rayColor':         (1.00, 0.95, 0.74, 1.0),
        'rayIntensity':     0.38,
        'bloomIntensity':   0.36,
        'bloomThreshold':   0.58,
        'exposure':         1.00,
        'hasWater':         False,
        'dayNightEnabled':  True,
        'cloudCoverage':    0.35,
        'cloudSpeed':       0.65,
        'cloudSharpness':   0.50,
        'turbidity':        2.4,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },
    'estate': {
        'ambient':          (0.36, 0.36, 0.48, 1.0),
        'key':              (1.20, 1.12, 0.96, 1.0),
        'keyHpr':           (105, -48, 0),
        'fill':             (0.28, 0.34, 0.46, 1.0),
        'fillHpr':          (-55, -38, 0),
        'rim':              (0.12, 0.18, 0.30, 1.0),
        'rimHpr':           (285, -28, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       580,
        'shadowFollowDist': 460.0,
        'shadowResScale':   0.90,
        'shadowAreaScale':  1.20,
        'shadowDarknessFloor': 0.24,
        'fogColor':         (0.68, 0.80, 0.94, 1.0),
        'fogNear':          120.0,
        'fogFar':           560.0,
        'fogExponent':      None,
        'skyScale':         (1.00, 1.00, 1.00, 1.0),
        'clearColor':       (0.44, 0.64, 0.86, 1.0),
        'sunUV':            (0.58, 0.74),
        'rayColor':         (1.00, 0.95, 0.80, 1.0),
        'rayIntensity':     0.32,
        'bloomIntensity':   0.30,
        'bloomThreshold':   0.60,
        'exposure':         1.00,
        'hasWater':         True,
        'waterColor':       (0.26, 0.46, 0.64, 0.86),
        'waterReflQuality': 'low',
        'dayNightEnabled':  True,
        'cloudCoverage':    0.38,
        'cloudSpeed':       0.60,
        'cloudSharpness':   0.55,
        'turbidity':        2.2,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },
    'cog': {
        'ambient':          (0.26, 0.28, 0.30, 1.0),
        'key':              (0.85, 0.88, 0.95, 1.0),
        'keyHpr':           (90,  -80, 0),
        'fill':             (0.18, 0.20, 0.22, 1.0),
        'fillHpr':          (-80, -40, 0),
        'rim':              (0.22, 0.24, 0.28, 1.0),
        'rimHpr':           (88,  -70, 0),
        'shadowCaster':     True,
        'shadowRes':        2048,
        'shadowArea':       220,
        'shadowFollowDist': 180.0,
        'shadowDarknessFloor': 0.20,
        'fogColor':         (0.24, 0.26, 0.28, 1.0),
        'fogNear':          60.0,
        'fogFar':           380.0,
        'fogExponent':      None,
        'skyScale':         (0.50, 0.55, 0.65, 1.0),
        'clearColor':       (0.10, 0.12, 0.14, 1.0),
        'sunUV':            (0.50, 0.70),
        'rayColor':         (0.88, 0.90, 1.00, 1.0),
        'rayIntensity':     0.12,
        'bloomIntensity':   0.15,
        'bloomThreshold':   0.68,
        'exposure':         1.05,
        'hasWater':         False,
        'dayNightEnabled':  False,
        'cloudCoverage':    0.0,
        'cloudSpeed':       0.0,
        'cloudSharpness':   0.0,
        'turbidity':        1.0,
        'starBrightness':   0.0,
        'moonEnabled':      False,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Day / Night keyframe tables (unchanged from previous version)
# ─────────────────────────────────────────────────────────────────────────────
_DAY_NIGHT_KEYFRAMES: dict[str, list[tuple]] = {
    'tt': [
        (0.0,  {'ambient': (0.06,0.08,0.18,1), 'key': (0.12,0.16,0.38,1),
                'keyHpr': (225,-38,0), 'fill': (0.04,0.06,0.14,1),
                'rim': (0.18,0.24,0.48,1), 'rimHpr': (222,-35,0),
                'fogColor': (0.05,0.07,0.16,1), 'fogNear': 150.0, 'fogFar': 1100.0,
                'clearColor': (0.03,0.05,0.14,1), 'skyScale': (0.42,0.48,0.75,1),
                'rayColor': (0.58,0.70,1.00,1), 'rayIntensity': 0.00,
                'bloomIntensity': 0.12, 'shadowCaster': False,
                'starBrightness': 0.96, 'moonEnabled': True, 'moonDir': (180,-52,0),
                'exposure': 1.00}),
        (5.5,  {'ambient': (0.08,0.10,0.20,1), 'key': (0.24,0.18,0.40,1),
                'keyHpr': (80,-5,0), 'fill': (0.06,0.07,0.15,1),
                'rim': (0.20,0.16,0.40,1), 'rimHpr': (82,-4,0),
                'fogColor': (0.12,0.15,0.30,1), 'fogNear': 150.0, 'fogFar': 1100.0,
                'clearColor': (0.08,0.11,0.24,1), 'skyScale': (0.52,0.55,0.80,1),
                'rayColor': (0.70,0.62,0.90,1), 'rayIntensity': 0.00,
                'bloomIntensity': 0.10, 'shadowCaster': False,
                'starBrightness': 0.32, 'moonEnabled': True, 'moonDir': (265,-38,0),
                'exposure': 1.00}),
        (6.5,  {'ambient': (0.24,0.16,0.18,1), 'key': (1.10,0.58,0.35,1),
                'keyHpr': (90,-10,0), 'fill': (0.22,0.16,0.32,1),
                'rim': (0.82,0.40,0.20,1), 'rimHpr': (88,-8,0),
                'fogColor': (0.88,0.56,0.35,1), 'fogNear': 70.0, 'fogFar': 420.0,
                'clearColor': (0.78,0.44,0.25,1), 'skyScale': (1.22,0.82,0.60,1),
                'rayColor': (1.00,0.64,0.30,1), 'rayIntensity': 0.60,
                'bloomIntensity': 0.32, 'shadowCaster': True,
                'starBrightness': 0.0, 'exposure': 1.05}),
        (9.0,  {'ambient': (0.26,0.30,0.44,1), 'key': (1.35,1.18,0.90,1),
                'keyHpr': (120,-36,0), 'fill': (0.28,0.36,0.52,1), 'rim': (0.14,0.20,0.34,1),
                'rimHpr': (300,-28,0),
                'fogColor': (0.70,0.80,0.94,1), 'fogNear': 100.0, 'fogFar': 500.0,
                'clearColor': (0.48,0.68,0.92,1), 'skyScale': (1.04,1.00,0.92,1),
                'rayColor': (1.00,0.92,0.68,1), 'rayIntensity': 0.45,
                'bloomIntensity': 0.24, 'shadowCaster': True,
                'starBrightness': 0.0, 'exposure': 1.00}),
        (12.0, {'ambient': (0.28,0.34,0.48,1), 'key': (1.55,1.40,1.10,1),
                'keyHpr': (180,-86,0), 'fill': (0.34,0.44,0.62,1), 'rim': (0.18,0.24,0.40,1),
                'rimHpr': (315,-30,0),
                'fogColor': (0.74,0.84,0.96,1), 'fogNear': 115.0, 'fogFar': 580.0,
                'clearColor': (0.50,0.72,0.96,1), 'skyScale': (1.00,1.00,0.96,1),
                'rayColor': (1.00,0.96,0.80,1), 'rayIntensity': 0.50,
                'bloomIntensity': 0.28, 'shadowCaster': True,
                'starBrightness': 0.0, 'exposure': 1.00}),
        (15.0, {'ambient': (0.28,0.34,0.48,1), 'key': (1.50,1.35,1.02,1),
                'keyHpr': (135,-42,0), 'fill': (0.32,0.42,0.62,1), 'rim': (0.18,0.24,0.40,1),
                'rimHpr': (315,-30,0),
                'fogColor': (0.72,0.80,0.92,1), 'fogNear': 110.0, 'fogFar': 560.0,
                'clearColor': (0.48,0.70,0.94,1), 'skyScale': (1.04,1.01,0.91,1),
                'rayColor': (1.00,0.94,0.70,1), 'rayIntensity': 0.55,
                'bloomIntensity': 0.28, 'shadowCaster': True,
                'starBrightness': 0.0, 'exposure': 1.00}),
        (17.5, {'ambient': (0.34,0.24,0.26,1), 'key': (1.38,0.85,0.40,1),
                'keyHpr': (230,-22,0), 'fill': (0.26,0.20,0.40,1),
                'rim': (0.90,0.44,0.18,1), 'rimHpr': (228,-18,0),
                'fogColor': (0.90,0.62,0.36,1), 'fogNear': 60.0, 'fogFar': 410.0,
                'clearColor': (0.84,0.54,0.26,1), 'skyScale': (1.24,0.86,0.60,1),
                'rayColor': (1.00,0.66,0.28,1), 'rayIntensity': 0.70,
                'bloomIntensity': 0.35, 'shadowCaster': True,
                'starBrightness': 0.0, 'exposure': 1.02}),
        (19.0, {'ambient': (0.22,0.14,0.28,1), 'key': (0.65,0.32,0.45,1),
                'keyHpr': (262,-8,0), 'fill': (0.16,0.10,0.28,1),
                'rim': (0.50,0.22,0.36,1), 'rimHpr': (260,-5,0),
                'fogColor': (0.38,0.18,0.36,1), 'fogNear': 30.0, 'fogFar': 310.0,
                'clearColor': (0.28,0.12,0.30,1), 'skyScale': (0.92,0.60,0.82,1),
                'rayColor': (0.90,0.46,0.72,1), 'rayIntensity': 0.35,
                'bloomIntensity': 0.22, 'shadowCaster': False,
                'starBrightness': 0.16, 'moonEnabled': False, 'exposure': 1.08}),
        (21.0, {'ambient': (0.06,0.08,0.18,1), 'key': (0.14,0.18,0.42,1),
                'keyHpr': (225,-40,0), 'fill': (0.04,0.06,0.14,1),
                'rim': (0.20,0.26,0.52,1), 'rimHpr': (222,-38,0),
                'fogColor': (0.05,0.07,0.16,1), 'fogNear': 150.0, 'fogFar': 1100.0,
                'clearColor': (0.03,0.05,0.14,1), 'skyScale': (0.40,0.44,0.72,1),
                'rayColor': (0.56,0.68,1.00,1), 'rayIntensity': 0.00,
                'bloomIntensity': 0.12, 'shadowCaster': False,
                'starBrightness': 0.92, 'moonEnabled': True, 'moonDir': (90,-55,0),
                'exposure': 1.00}),
        (24.0, None),
    ],
    'dd': [
        (0.0,  {'ambient': (0.16,0.18,0.26,1), 'key': (0.18,0.22,0.35,1),
                'keyHpr': (0,-70,0), 'fogColor': (0.08,0.10,0.18,1),
                'fogExponent': 0.007, 'clearColor': (0.06,0.08,0.16,1),
                'skyScale': (0.54,0.64,0.82,1), 'bloomIntensity': 0.10,
                'shadowCaster': False}),
        (7.0,  {'ambient': (0.34,0.40,0.54,1), 'key': (0.95,0.92,0.82,1),
                'keyHpr': (0,-75,0), 'fogColor': (0.56,0.70,0.86,1),
                'fogExponent': 0.005, 'clearColor': (0.36,0.60,0.88,1),
                'skyScale': (0.90,0.97,1.08,1), 'bloomIntensity': 0.15,
                'shadowCaster': True}),
        (13.0, {'ambient': (0.38,0.44,0.58,1), 'key': (1.05,1.02,0.95,1),
                'keyHpr': (0,-80,0), 'fogColor': (0.62,0.74,0.88,1),
                'fogExponent': 0.005, 'clearColor': (0.42,0.64,0.90,1),
                'skyScale': (0.94,0.98,1.06,1), 'bloomIntensity': 0.18,
                'shadowCaster': True}),
        (20.0, {'ambient': (0.22,0.24,0.32,1), 'key': (0.28,0.30,0.42,1),
                'keyHpr': (270,-72,0), 'fogColor': (0.14,0.16,0.24,1),
                'fogExponent': 0.007, 'clearColor': (0.10,0.12,0.22,1),
                'skyScale': (0.66,0.74,0.90,1), 'bloomIntensity': 0.12,
                'shadowCaster': False}),
        (24.0, None),
    ],
    'dg': [
        (0.0,  {'ambient': (0.05,0.07,0.14,1), 'key': (0.12,0.16,0.35,1),
                'keyHpr': (225,-38,0), 'fill': (0.03,0.05,0.10,1),
                'rim': (0.14,0.18,0.36,1), 'rimHpr': (222,-34,0),
                'fogColor': (0.04,0.06,0.14,1), 'fogNear': 10.0, 'fogFar': 280.0,
                'clearColor': (0.02,0.04,0.10,1), 'skyScale': (0.34,0.42,0.68,1),
                'rayColor': (0.56,0.70,1.00,1), 'rayIntensity': 0.00,
                'bloomIntensity': 0.14, 'shadowCaster': False,
                'starBrightness': 0.76, 'moonEnabled': True, 'moonDir': (180,-55,0)}),
        (6.0,  {'ambient': (0.20,0.16,0.18,1), 'key': (0.86,0.46,0.22,1),
                'keyHpr': (90,-8,0), 'fill': (0.16,0.12,0.26,1),
                'rim': (0.68,0.34,0.14,1), 'rimHpr': (88,-6,0),
                'fogColor': (0.80,0.50,0.28,1), 'fogNear': 50.0, 'fogFar': 360.0,
                'clearColor': (0.70,0.38,0.18,1), 'skyScale': (1.18,0.76,0.52,1),
                'rayColor': (1.00,0.60,0.28,1), 'rayIntensity': 0.55,
                'bloomIntensity': 0.44, 'shadowCaster': True,
                'starBrightness': 0.0, 'moonEnabled': False}),
        (11.0, {'ambient': (0.26,0.38,0.52,1), 'key': (1.52,1.45,1.15,1),
                'keyHpr': (180,-82,0), 'fill': (0.34,0.46,0.68,1),
                'rim': (0.18,0.28,0.48,1), 'rimHpr': (0,55,0),
                'fogColor': (0.64,0.82,1.00,1), 'fogNear': 145.0, 'fogFar': 660.0,
                'clearColor': (0.42,0.72,1.00,1), 'skyScale': (0.92,0.98,1.12,1),
                'rayColor': (1.00,1.00,0.85,1), 'rayIntensity': 0.72,
                'bloomIntensity': 0.45, 'shadowCaster': True,
                'starBrightness': 0.0, 'moonEnabled': False}),
        (17.0, {'ambient': (0.28,0.22,0.24,1), 'key': (1.10,0.70,0.30,1),
                'keyHpr': (235,-20,0), 'fill': (0.20,0.16,0.32,1),
                'rim': (0.78,0.36,0.14,1), 'rimHpr': (232,-16,0),
                'fogColor': (0.84,0.56,0.32,1), 'fogNear': 40.0, 'fogFar': 360.0,
                'clearColor': (0.76,0.46,0.20,1), 'skyScale': (1.18,0.82,0.54,1),
                'rayColor': (1.00,0.62,0.24,1), 'rayIntensity': 0.60,
                'bloomIntensity': 0.52, 'shadowCaster': True,
                'starBrightness': 0.0, 'moonEnabled': False}),
        (20.0, {'ambient': (0.06,0.08,0.18,1), 'key': (0.18,0.22,0.44,1),
                'keyHpr': (225,-40,0), 'fill': (0.04,0.06,0.12,1),
                'rim': (0.22,0.26,0.52,1), 'rimHpr': (222,-38,0),
                'fogColor': (0.05,0.08,0.18,1), 'fogNear': 10.0, 'fogFar': 280.0,
                'clearColor': (0.04,0.06,0.14,1), 'skyScale': (0.36,0.44,0.70,1),
                'rayColor': (0.56,0.70,1.00,1), 'rayIntensity': 0.00,
                'bloomIntensity': 0.16, 'shadowCaster': False,
                'starBrightness': 0.42, 'moonEnabled': True, 'moonDir': (120,-48,0)}),
        (24.0, None),
    ],
    'mm': [
        (0.0,  {'ambient': (0.08,0.06,0.12,1), 'key': (0.14,0.10,0.24,1),
                'keyHpr': (270,-5,0), 'fill': (0.05,0.04,0.10,1),
                'rim': (0.16,0.08,0.22,1), 'rimHpr': (268,-3,0),
                'fogColor': (0.08,0.04,0.12,1), 'fogNear': 5.0, 'fogFar': 200.0,
                'clearColor': (0.05,0.03,0.10,1), 'skyScale': (0.60,0.28,0.56,1),
                'rayColor': (0.72,0.32,0.80,1), 'rayIntensity': 0.00,
                'bloomIntensity': 0.20, 'shadowCaster': False,
                'starBrightness': 0.58, 'moonEnabled': True, 'moonDir': (220,-42,0)}),
        (6.0,  {'ambient': (0.32,0.20,0.16,1), 'key': (0.85,0.40,0.12,1),
                'keyHpr': (90,-10,0), 'fill': (0.18,0.12,0.26,1),
                'rim': (0.60,0.28,0.10,1), 'rimHpr': (88,-8,0),
                'fogColor': (0.72,0.36,0.14,1), 'fogNear': 10.0, 'fogFar': 290.0,
                'clearColor': (0.66,0.32,0.12,1), 'skyScale': (1.14,0.60,0.34,1),
                'rayColor': (1.00,0.52,0.18,1), 'rayIntensity': 0.60,
                'bloomIntensity': 0.50, 'shadowCaster': True,
                'starBrightness': 0.0, 'moonEnabled': False}),
        (14.0, {'ambient': (0.38,0.24,0.34,1), 'key': (1.35,0.75,0.35,1),
                'keyHpr': (260,-18,0), 'fill': (0.24,0.18,0.44,1),
                'rim': (0.85,0.42,0.18,1), 'rimHpr': (258,-10,0),
                'fogColor': (0.82,0.46,0.22,1), 'fogNear': 15.0, 'fogFar': 380.0,
                'clearColor': (0.85,0.45,0.20,1), 'skyScale': (1.24,0.80,0.58,1),
                'rayColor': (1.00,0.65,0.28,1), 'rayIntensity': 0.65,
                'bloomIntensity': 0.48, 'shadowCaster': True,
                'starBrightness': 0.0, 'moonEnabled': False}),
        (20.0, {'ambient': (0.18,0.10,0.22,1), 'key': (0.35,0.18,0.36,1),
                'keyHpr': (270,-6,0), 'fill': (0.08,0.05,0.18,1),
                'rim': (0.30,0.12,0.28,1), 'rimHpr': (268,-4,0),
                'fogColor': (0.20,0.08,0.24,1), 'fogNear': 5.0, 'fogFar': 220.0,
                'clearColor': (0.14,0.05,0.18,1), 'skyScale': (0.80,0.40,0.80,1),
                'rayColor': (0.80,0.38,0.90,1), 'rayIntensity': 0.00,
                'bloomIntensity': 0.28, 'shadowCaster': False,
                'starBrightness': 0.24, 'moonEnabled': True, 'moonDir': (240,-36,0)}),
        (24.0, None),
    ],
    'br': [
        (0.0,  {'ambient': (0.18,0.24,0.38,1), 'key': (0.22,0.28,0.48,1),
                'keyHpr': (180,-10,0), 'fogColor': (0.16,0.22,0.36,1),
                'fogExponent': 0.016, 'clearColor': (0.12,0.18,0.32,1),
                'skyScale': (0.60,0.72,0.94,1), 'bloomIntensity': 0.12,
                'auroraEnabled': True, 'starBrightness': 0.85, 'moonEnabled': True, 'moonDir': (180,-50,0)}),
        (12.0, {'ambient': (0.46,0.54,0.72,1), 'key': (0.75,0.85,1.05,1),
                'keyHpr': (180,-22,0), 'fogColor': (0.72,0.82,0.96,1),
                'fogExponent': 0.012, 'clearColor': (0.68,0.80,0.96,1),
                'skyScale': (0.84,0.92,1.10,1), 'bloomIntensity': 0.20}),
        (18.0, {'ambient': (0.28,0.34,0.48,1), 'key': (0.40,0.46,0.65,1),
                'keyHpr': (270,-12,0), 'fogColor': (0.35,0.42,0.60,1),
                'fogExponent': 0.014, 'clearColor': (0.30,0.38,0.56,1),
                'skyScale': (0.68,0.80,1.02,1), 'bloomIntensity': 0.16,
                'auroraEnabled': True, 'starBrightness': 0.40}),
        (24.0, None),
    ],
    'dl': [
        (0.0,  {'ambient': (0.18,0.16,0.32,1), 'key': (0.45,0.52,0.85,1),
                'keyHpr': (225,-55,0), 'fill': (0.06,0.08,0.18,1),
                'rim': (0.30,0.38,0.65,1), 'rimHpr': (220,-50,0),
                'fogColor': (0.05,0.07,0.14,1), 'fogExponent': 0.005,
                'clearColor': (0.03,0.04,0.10,1), 'skyScale': (0.45,0.50,0.80,1),
                'rayColor': (0.58,0.70,1.00,1), 'rayIntensity': 0.00,
                'bloomIntensity': 0.28, 'shadowCaster': False,
                'starBrightness': 0.95, 'moonEnabled': True, 'moonDir': (225,-55,0),
                'auroraEnabled': True}),
        (24.0, None),
    ],
    'gs': [
        (0.0,  {'ambient': (0.06,0.08,0.16,1), 'key': (0.12,0.14,0.28,1),
                'keyHpr': (225,-32,0), 'fogColor': (0.08,0.10,0.18,1),
                'fogNear': 20.0, 'fogFar': 280.0, 'clearColor': (0.06,0.08,0.16,1),
                'bloomIntensity': 0.12, 'shadowCaster': False,
                'starBrightness': 0.78, 'moonEnabled': True, 'moonDir': (0,-60,0)}),
        (7.0,  {'ambient': (0.28,0.26,0.24,1), 'key': (0.88,0.58,0.26,1),
                'keyHpr': (90,-12,0), 'fogColor': (0.72,0.60,0.42,1),
                'fogNear': 50.0, 'fogFar': 320.0, 'clearColor': (0.62,0.50,0.34,1),
                'bloomIntensity': 0.36, 'shadowCaster': True,
                'starBrightness': 0.0, 'moonEnabled': False}),
        (11.0, {'ambient': (0.30,0.32,0.40,1), 'key': (1.20,1.10,0.88,1),
                'keyHpr': (130,-48,0), 'fogColor': (0.76,0.78,0.72,1),
                'fogNear': 60.0, 'fogFar': 420.0, 'clearColor': (0.52,0.68,0.88,1),
                'bloomIntensity': 0.25, 'shadowCaster': True,
                'starBrightness': 0.0, 'moonEnabled': False}),
        (18.0, {'ambient': (0.28,0.22,0.18,1), 'key': (1.00,0.58,0.22,1),
                'keyHpr': (240,-18,0), 'fogColor': (0.72,0.46,0.24,1),
                'fogNear': 40.0, 'fogFar': 320.0, 'clearColor': (0.66,0.40,0.18,1),
                'bloomIntensity': 0.44, 'shadowCaster': True,
                'starBrightness': 0.0, 'moonEnabled': False}),
        (21.0, {'ambient': (0.08,0.10,0.18,1), 'key': (0.16,0.18,0.34,1),
                'keyHpr': (225,-34,0), 'fogColor': (0.08,0.10,0.18,1),
                'fogNear': 20.0, 'fogFar': 280.0, 'clearColor': (0.06,0.08,0.16,1),
                'bloomIntensity': 0.14, 'shadowCaster': False,
                'starBrightness': 0.68, 'moonEnabled': True, 'moonDir': (60,-56,0)}),
        (24.0, None),
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Hood ID + Zone ID → profile key mapping
# ─────────────────────────────────────────────────────────────────────────────

_HOOD_ID_MAP: dict[int, str] | None = None
_ZONE_ID_MAP: dict[int, str]  = {}   # populated lazily


def _buildHoodIdMap() -> dict[int, str]:
    from toontown.toonbase import ToontownGlobals as TG
    m = {
        TG.ToontownCentral:   'tt',
        TG.DonaldsDock:       'dd',
        TG.MinniesMelodyland: 'mm',
        TG.DaisyGardens:      'dg',
        TG.TheBrrrgh:         'br',
        TG.DonaldsDreamland:  'dl',
        TG.GoofySpeedway:     'gs',
        TG.OutdoorZone:       'oz',
        TG.GolfZone:          'golf_course',
        TG.PartyHood:         'party',
        TG.BossbotHQ:         'bossbot_hq',
        TG.SellbotHQ:         'sellbot_hq',
        TG.CashbotHQ:         'cashbot_hq',
        TG.LawbotHQ:          'lawbot_hq',
        TG.Tutorial:          'tutorial',
    }
    for attr in ('MyEstate', 'Estate', 'ToonEstate'):
        val = getattr(TG, attr, None)
        if val is not None:
            m[val] = 'estate'
    for attr in ('FunnyFarm',):
        val = getattr(TG, attr, None)
        if val is not None:
            m[val] = 'playground'
    for attr in ('WelcomeValleyBegin',):
        val = getattr(TG, attr, None)
        if val is not None:
            m[val] = 'tt'
    return m


def _buildZoneIdMap() -> dict[int, str]:
    """Map individual zone IDs to specific sub-profiles."""
    from toontown.toonbase import ToontownGlobals as TG

    def _zr(tg_attr: str) -> int | None:
        return getattr(TG, tg_attr, None)

    z: dict[int, str] = {}

    # TT streets
    for attr in ('SillyStreet', 'LoopyLane', 'PunchlinePlace'):
        v = _zr(attr)
        if v: z[v] = 'tt_street'

    # DD streets
    for attr in ('BarnacleBoulevard', 'SeaweedStreet', 'LighthouseLane'):
        v = _zr(attr)
        if v: z[v] = 'dd_street'

    # BR streets
    for attr in ('SleetStreet', 'WalrusWay', 'PolarPlace'):
        v = _zr(attr)
        if v: z[v] = 'br_street'

    # MM streets
    for attr in ('AltoAvenue', 'BaritoneBoulevard', 'TenorTerrace'):
        v = _zr(attr)
        if v: z[v] = 'mm_street'

    # DG streets
    for attr in ('ElmStreet', 'MapleStreet', 'OakStreet'):
        v = _zr(attr)
        if v: z[v] = 'dg_street'

    # DDL streets
    for attr in ('LullabyLane', 'PajamaPlace'):
        v = _zr(attr)
        if v: z[v] = 'dl_street'

    for attr in ('OutdoorZone',):
        v = _zr(attr)
        if v:
            z[v] = 'oz'
            z[v + 100] = 'oz_street'

    for attr in ('GolfZone',):
        v = _zr(attr)
        if v:
            z[v] = 'golf_course'
            z[v + 100] = 'golf_course'

    for attr in ('PartyHood',):
        v = _zr(attr)
        if v: z[v] = 'party'

    for attr in ('Tutorial',):
        v = _zr(attr)
        if v:
            z[v] = 'tutorial'
            z[v + 1] = 'tutorial'
            z[20000] = 'tutorial'
            z[20001] = 'tutorial'

    for attr in ('SellbotHQ', 'SellbotFactoryExt'):
        v = _zr(attr)
        if v: z[v] = 'sellbot_hq'

    for attr in ('SellbotLobby',):
        v = _zr(attr)
        if v: z[v] = 'sellbot_lobby'

    for attr in ('CashbotHQ',):
        v = _zr(attr)
        if v: z[v] = 'cashbot_hq'

    for attr in ('CashbotLobby',):
        v = _zr(attr)
        if v: z[v] = 'cashbot_lobby'

    for attr in ('LawbotHQ', 'LawbotOfficeExt'):
        v = _zr(attr)
        if v: z[v] = 'lawbot_hq'

    for attr in ('LawbotLobby',):
        v = _zr(attr)
        if v: z[v] = 'lawbot_lobby'

    for attr in ('BossbotHQ',):
        v = _zr(attr)
        if v: z[v] = 'bossbot_hq'

    for attr in ('BossbotLobby',):
        v = _zr(attr)
        if v: z[v] = 'bossbot_lobby'

    for attr in ('MyEstate', 'Estate', 'ToonEstate'):
        v = _zr(attr)
        if v: z[v] = 'estate_house'

    # Interiors
    for attr in ('SellbotFactoryInt', 'SellbotLegFactoryInt',
                 'LawbotFactoryInt', 'SellbotFactoryIntS'):
        v = _zr(attr)
        if v: z[v] = 'factory_int'

    for attr in ('CashbotMintIntA', 'CashbotMintIntB', 'CashbotMintIntC'):
        v = _zr(attr)
        if v: z[v] = 'cashbot_mint'

    for attr in ('LawbotOfficeInt', 'LawbotStageIntA', 'LawbotStageIntB',
                 'LawbotStageIntC', 'LawbotStageIntD'):
        v = _zr(attr)
        if v: z[v] = 'lawbot_office'

    for attr in ('BossbotCountryClubIntA', 'BossbotCountryClubIntB',
                 'BossbotCountryClubIntC'):
        v = _zr(attr)
        if v: z[v] = 'bossbot_cc'

    return z


def _resolveStyle(style: str, hoodId: int | None,
                  zoneId: int | None = None) -> str:
    global _HOOD_ID_MAP, _ZONE_ID_MAP

    # Zone ID takes priority (most specific).
    if zoneId is not None:
        if not _ZONE_ID_MAP:
            try:
                _ZONE_ID_MAP = _buildZoneIdMap()
            except Exception:
                pass
        res = _ZONE_ID_MAP.get(zoneId)
        if res and res in _ZONE_PROFILES:
            return res
        # Fallback to the canonical branch zone (e.g. street segment zone 4101 -> branch 4100)
        branchZone = zoneId - (zoneId % 100)
        res = _ZONE_ID_MAP.get(branchZone)
        if res and res in _ZONE_PROFILES:
            return res

    # Hood ID next.
    if hoodId is not None:
        if _HOOD_ID_MAP is None:
            try:
                _HOOD_ID_MAP = _buildHoodIdMap()
            except Exception:
                _HOOD_ID_MAP = {}
        res = _HOOD_ID_MAP.get(hoodId)
        if res and res in _ZONE_PROFILES:
            return res

    return style if style in _ZONE_PROFILES else 'playground'


# ─────────────────────────────────────────────────────────────────────────────
# Module-level shared state
# ─────────────────────────────────────────────────────────────────────────────

_refCount            = 0
_activeStyle         = 'playground'
_activeGeom: NodePath | None = None
_styleStack: list[tuple[str, Any]] = []
_geomShaderState: tuple[NodePath, Any | None] | None = None
_avatarShaderState: tuple[NodePath, Any | None] | None = None
# Dynamically streamed outdoor geometry receives its own root shader attrib.
# Preserve that state until the owning distributed object is cleared (or the
# outdoor session ends), otherwise a stale receiver can outlive the global
# osl_* inputs and assert in ShaderAttrib during the next render frame.
_extraShaderStates: list[dict[str, Any]] = []

# Light rig
_lightRig: NodePath | None = None
_renderLights: list[NodePath] = []
_keyLightNp: NodePath | None  = None
_keyCastsShadows               = False
_shadowFocusPos: Vec3 | None  = None
_shadowSceneRadius             = 550.0
_shadowMapRes                  = 1024
_shadowFilmArea                = 550.0
_shadowFollowDist              = 450.0
_prevShadowCasterState: bool | None = None
_lightRigSettingsSignature: tuple[bool, str] | None = None
_sunDirWorld: Vec3 | None = None
_worldShadowShader: Shader | None = None
_worldShadowWhiteTex: Texture | None = None
_worldShadowTextureState: tuple[NodePath, Any | None] | None = None
_worldShadowBuffer: Any = None
_worldShadowCameraNp: NodePath | None = None
_worldShadowMap: Texture | None = None
_worldShadowCasterShader: Shader | None = None

# Delay (seconds) between tearing down one zone's light rig and creating the next
# zone's shadow-map FBO. Creating a new shadow FBO while the previous zone's FBO
# is still live in the /Draw render thread wedges graphicsEngine.renderFrame()
# permanently on this Panda build (documented double-FBO GL deadlock on street
# entry). The sun light itself attaches immediately; only the shadow caster is
# deferred, so shadows appear about a second after the zone finishes loading.
_SHADOW_CASTER_DEFER_S = 1.0
# True while a deferred shadow-caster enable is already scheduled, so the dawn
# transition logic does not stack duplicate tasks.
_shadowEnablePending: bool = False
# Max vertices a Geom may have and still be treated as a 2D card / billboard
# for shadow-caster exclusion (foliage planes etc.).  Real building walls are
# merged into much larger Geoms after flattening, so they keep casting.
_PLANE_MAX_VERTS = 128

# Fog
_fogNode: Fog | None = None
# When False, the currently-active zone (set via begin(..., fogEnabled=False))
# suppresses atmospheric fog entirely, even though the rest of the rig runs.
# Go-kart races use this so track visibility is never blocked by dense fog.
_fogEnabled = True

# Sky / window
_skyWasDimmed    = False
_prevClearColor: tuple | None = None

# Post-process: CommonFilters bloom + render2dp sun-ray overlay (no scene RTT compositor)
_godRaysCard: NodePath | None = None
_godRaysShader: Shader | None = None
_bloomFilters: Any = None
_postFilterManager: Any = None
_postQuad: NodePath | None = None
_postColorTex: Texture | None = None
_postDepthTex: Texture | None = None
_postShader: Shader | None = None

# Water reflections
_waterSetups: list[dict] = []
_waterShader: Shader | None = None

# Procedural sky
_proceduralSky: Any = None  # ProceduralSky instance

# Day / night
_timeOfDay         = 12.0
_dayNightSpeed     = 1.0 / 60.0
_dayNightAccum     = 0.0
_godRaysTime       = 0.0

_godRaysTaskName   = 'outdoorLightingTask'

# Street lamp / lantern local lights (active at night)
_lampLights: list[NodePath]   = []   # Short-range PointLight NodePaths attached to _lightRig
_lampPoolNps: list[NodePath]  = []   # Ground overlays for legacy meshes that ignore lights
_lampPoolTex: Texture | None  = None
_lampGeomNps: list[Any]       = []   # Fixture NodePaths or cached world positions
_lampLightsActive: bool        = False
_activeLampSignature: tuple    = ()
_lampRefreshAccum: float       = 0.0

# Lamp node name keywords – any node whose name contains one of these is treated as a light source
_LAMP_KEYWORDS: tuple = (
    'lamp', 'lantern', 'streetlight', 'lightpole', 'lamp_post', 'lamppost',
    'prop_lamp', 'prop_lantern', 'light_pole', 'gazebo_lamp', 'post_lamp',
    'street_lamp', 'streetlamp', 'gas_lamp', 'gaslight', 'torch',
    'light_fixture', 'park_lamp', 'lampshade', 'bulb',
    'sconce', 'chandelier', 'fixture', 'glow', 'fluorescent',
    'ceiling_lamp', 'indoor-fixture',
    # Additional Toontown prop/DNA names that serve as light sources
    'prop_street_lamp', 'prop_gas_lamp', 'streetlamp_post',
    'light_post', 'prop_lightpost', 'lamp_base',
    'lanternpost', 'hanging_lamp', 'ceiling_light', 'wall_light',
    # Canonical Toontown DNA fixture names (TTC and several streets).
    'prop_post_one_light', 'prop_post_three_light', 'streetlight_tt',
)
_LAMP_LIGHT_LIMIT = 200   # max lamp candidates to cache while scanning a hood
_LAMP_POSITION_TAG = 'osl_lamp_fixture_positions'
# Every discovered fixture remains active for the lifetime of the loaded hood.
# Ground pools guarantee visibility on legacy meshes even when the renderer's
# hardware light budget cannot shade every PointLight in one generated shader.
_MAX_SHADER_LAMP_LIGHTS = 4
_LAMP_MAX_DISTANCE = 18.0
_LAMP_REFRESH_SECONDS = 0.10

# IMPORTANT:
# In this codebase, BitMask32.bit(5) is OTPRender.EnviroCameraBitmask and
# `OTPBase.setupEnviroCamera()` does `render.hide(EnviroCameraBitmask)`.
# Using bit(5) for shadow cameras makes the shadow pass see an empty scene.
_SHADOW_CAM_MASK = getattr(_OTPRender, 'ShadowCameraBitmask', BitMask32.bit(2))
_WATER_REFLECTION_CAM_MASK = BitMask32.bit(29)

# Temporary safety: do not rely on ShadowCameraBitmask-only visibility for the
# shadow map camera. Some scenes use draw masks in ways that can make the
# shadow pass see nothing when restricted to a single bit. We keep the mask
# constant for future use, but avoid forcing the shadow camera to use it.

_SHADER_DIR_CANDIDATES = (
    os.path.join(os.path.dirname(__file__), '..', 'shaders'),
    os.path.join(os.path.dirname(__file__), 'shaders'),
    os.path.join(os.path.dirname(__file__), '..', '..', 'shaders'),
)
_SHADER_DIR = next((d for d in _SHADER_DIR_CANDIDATES if os.path.exists(d)), _SHADER_DIR_CANDIDATES[0])
_LEGACY_VERT       = os.path.join(_SHADER_DIR, 'sunrays.vert.glsl')
_LEGACY_FRAG       = os.path.join(_SHADER_DIR, 'sunrays.frag.glsl')
_WATER_VERT        = os.path.join(_SHADER_DIR, 'water.vert.glsl')
_WATER_FRAG        = os.path.join(_SHADER_DIR, 'water.frag.glsl')
_POST_VERT         = os.path.join(_SHADER_DIR, 'post.vert.glsl')
_POST_FRAG         = os.path.join(_SHADER_DIR, 'scene_post.frag.glsl')
_WORLD_SHADOW_VERT = os.path.join(_SHADER_DIR, 'world_shadow.vert.glsl')
_WORLD_SHADOW_FRAG = os.path.join(_SHADER_DIR, 'world_shadow.frag.glsl')
_WORLD_SHADOW_CASTER_VERT = os.path.join(_SHADER_DIR, 'world_shadow_caster.vert.glsl')
_WORLD_SHADOW_CASTER_FRAG = os.path.join(_SHADER_DIR, 'world_shadow_caster.frag.glsl')

_WATER_PATTERNS = (
    'water', 'pond', 'ocean', 'sea', 'lake', 'fountain',
    'puddle', 'river', 'stream', 'brook', 'lagoon', 'tide',
)

_rigRebuildPending: dict | None = None

# ─────────────────────────────────────────────────────────────────────────────
# Settings helpers
# ─────────────────────────────────────────────────────────────────────────────

def _wantFx() -> bool:
    _syncBase()
    if _OUTDOOR_SHADER_BISECT_LEVEL < 1:
        return False
    try:
        val = _getSettingValue('want-modern-outdoor-lighting', None)
        if val is not None:
            return _coerceBool(val, True)
    except Exception:
        pass
    return ConfigVariableBool('want-modern-outdoor-lighting', True).value


def _coerceBool(value, default: bool = False) -> bool:
    """Parse bool-like settings without treating the string ``"false"`` as True."""
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


def _settingsBool(key: str, default: bool) -> bool:
    try:
        return _coerceBool(_getSettingValue(key, default), default)
    except Exception:
        return default


def _settingsFloat(key: str, default: float) -> float:
    try:
        value = float(_getSettingValue(key, default))
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _settingsStr(key: str, default: str) -> str:
    try:
        v = _getSettingValue(key, default)
        return str(v) if v is not None else default
    except Exception:
        return default


def _wantDayNight() -> bool:
    try:
        val = _getSettingValue('want-day-night-cycle', None)
        if val is not None:
            return _coerceBool(val, True)
    except Exception:
        pass
    return ConfigVariableBool('want-day-night-cycle', True).value




_mbExcludedBounds: PTA_LVecBase4f = PTA_LVecBase4f()
_mbExcludedDepth: PTA_LVecBase2f = PTA_LVecBase2f()
for _ in range(16):
    _mbExcludedBounds.push_back(LVecBase4f(0, 0, 0, 0))
    _mbExcludedDepth.push_back(LVecBase2f(0, 0))
_mbNumExcluded: int = 0


def _computeScreenBounds(np: NodePath, cam: NodePath, projMat: LMatrix4) -> tuple[Vec4, Vec2] | None:
    try:
        if np is None or np.isEmpty():
            return None
        bounds = np.getTightBounds(cam)
        if not bounds or len(bounds) != 2:
            return None
        min_cam, max_cam = bounds
        corners = [
            Vec3(min_cam.x, min_cam.y, min_cam.z),
            Vec3(max_cam.x, min_cam.y, min_cam.z),
            Vec3(min_cam.x, max_cam.y, min_cam.z),
            Vec3(max_cam.x, max_cam.y, min_cam.z),
            Vec3(min_cam.x, min_cam.y, max_cam.z),
            Vec3(max_cam.x, min_cam.y, max_cam.z),
            Vec3(min_cam.x, max_cam.y, max_cam.z),
            Vec3(max_cam.x, max_cam.y, max_cam.z),
        ]
        min_uv = Vec2(1.0, 1.0)
        max_uv = Vec2(0.0, 0.0)
        min_d = 1.0
        max_d = 0.0
        valid = False
        for c in corners:
            if c.y <= 0.1:
                continue
            p_proj = projMat.xform(Vec4(c.x, c.y, c.z, 1.0))
            if p_proj.w > 0.0:
                ndc_x = p_proj.x / p_proj.w
                ndc_y = p_proj.y / p_proj.w
                ndc_z = p_proj.z / p_proj.w
                uv_x = ndc_x * 0.5 + 0.5
                uv_y = ndc_y * 0.5 + 0.5
                depth = ndc_z * 0.5 + 0.5
                min_uv.x = min(min_uv.x, uv_x)
                min_uv.y = min(min_uv.y, uv_y)
                max_uv.x = max(max_uv.x, uv_x)
                max_uv.y = max(max_uv.y, uv_y)
                min_d = min(min_d, depth)
                max_d = max(max_d, depth)
                valid = True
        if valid and min_uv.x < max_uv.x and min_uv.y < max_uv.y:
            pad_x = 0.006
            pad_y = 0.006
            pad_d = 0.012
            return (
                Vec4(max(0.0, min_uv.x - pad_x), max(0.0, min_uv.y - pad_y),
                     min(1.0, max_uv.x + pad_x), min(1.0, max_uv.y + pad_y)),
                Vec2(max(0.0, min_d - pad_d), min(1.0, max_d + pad_d))
            )
    except Exception:
        pass
    return None

_prevViewProjMat: LMatrix4 | None = None
_currToPrevMat: LMatrix4 = LMatrix4.identMat()


def _wantMotionBlur() -> bool:
    return _bisectAllows(7) and _settingsBool('motion-blur', False) and _supportsBasicShaders()


def resetCameraCut() -> None:
    global _prevViewProjMat, _currToPrevMat
    _prevViewProjMat = None
    _currToPrevMat = LMatrix4.identMat()
    if _postQuad is not None and not _postQuad.isEmpty():
        try:
            _postQuad.setShaderInput('currToPrevMat', _currToPrevMat)
        except Exception:
            pass


def _wantBloom() -> bool:
    return _bisectAllows(7) and _settingsBool('lighting-bloom-enabled', True)


def _wantWater() -> bool:
    return (_OUTDOOR_SHADER_BISECT_LEVEL >= 3 and _bisectAllows(9)
            and _settingsBool('want-water-reflections', True)
            and _supportsBasicShaders())


_cachedSupportsBasicShaders: bool | None = None


def _supportsBasicShaders() -> bool:
    """Return the active renderer's shader capability without vendor guessing.

    Panda's GSG is authoritative.  If the window/GSG is not ready yet, report
    False for GPU-resource creation and let the next setup/refresh retry.  This
    behaves consistently on NVIDIA, AMD, Intel Arc, and software/fallback pipes.
    """
    global _cachedSupportsBasicShaders
    _syncBase()
    try:
        win = getattr(base, 'win', None)
        gsg = win.getGsg() if win else None
        if gsg is None:
            return False
        supports = bool(gsg.getSupportsBasicShaders())
        _cachedSupportsBasicShaders = supports
        if _debugEnabled():
            vendor = getattr(gsg, 'getDriverVendor', lambda: 'unknown')()
            renderer = getattr(gsg, 'getDriverRenderer', lambda: 'unknown')()
            _dbg(f"shader capability={supports} vendor={vendor!s} renderer={renderer!s}")
        return supports
    except Exception as error:
        _dbg(f"shader capability query failed: {error!r}")
        return False


def _wantDynamicShadows() -> bool:
    """Return whether shadow-map resources are safe to allocate.

    The active Panda3D GSG is authoritative.  We intentionally do not provide a
    vendor-guess or "force" bypass here: asking a renderer that reports no
    basic-shader support to allocate a shadow caster is exactly the sort of
    cross-driver failure this compatibility layer is meant to avoid.  Legacy
    world shadow maps are separately opt-in via
    ``lighting-experimental-world-shadows`` in ``_spawnLightRig``.
    """
    if not _settingsBool('dynamic-shadows', True):
        _dbg('dynamic shadows disabled by setting')
        return False
    if _shadowQuality() == 'off':
        _dbg('dynamic shadows disabled: shadow-quality=off')
        return False
    if not _supportsBasicShaders():
        _dbg('dynamic shadows disabled: active GSG reports no basic shader support')
        return False
    return True


def _wantWorldShadows() -> bool:
    """Master gate for the custom real-time world shadow map path.

    This is time-of-day independent; the per-zone ``shadowCaster`` keyframe and
    the current night factor decide whether a caster is actually *live*.  The
    flag itself lives in Settings so it can be toggled from the Options UI.
    """
    if not _settingsBool('lighting-experimental-world-shadows', True):
        return False
    if not _wantDynamicShadows():
        return False
    if _shadowQuality() == 'off':
        return False
    return True


def _worldShadersActive() -> bool:
    """True when the active zone uses the stable custom receiver shader.

    The shader remains attached across every time-of-day transition.  Only its
    numeric sun/shadow inputs change, so night cannot create a different shader
    variant for one half of flattened DNA geometry.
    """
    if not _wantWorldShadows():
        return False
    try:
        return bool(_ZONE_PROFILES.get(_activeStyle, {}).get('shadowCaster', False))
    except Exception:
        return False


def _wantSpecularHighlights() -> bool:
    # Specular in Panda3D auto-shaders can produce camera-dependent color shifts
    # on some legacy content/shader-generator combinations (often perceived as
    # an RGB "sheen" on flat vertical surfaces). Keep it opt-in by default.
    return _settingsBool('lighting-specular-enabled', False)


def _wantTonemap() -> bool:
    return _settingsBool('lighting-tonemap-enabled', True)


def _wantProceduralSky() -> bool:
    if _OUTDOOR_SHADER_BISECT_LEVEL < 1:
        return False
    return _bisectAllows(6) and _settingsBool('want-procedural-sky', True)


def _wantVignette() -> bool:
    return _settingsBool('lighting-vignette-enabled', False)


def _intensityScale() -> float:
    return max(0.1, min(2.0, _settingsFloat('lighting-intensity', 1.0)))


def _colorTempBias() -> float:
    return max(-1.0, min(1.0, _settingsFloat('lighting-color-temp', 0.0)))


def _dayNightSpeedMultiplier() -> float:
    return max(0.0, _settingsFloat('day-night-speed', 1.0))


def _vignetteStrength() -> float:
    if not _wantVignette():
        return 0.0
    return max(0.0, min(1.0, _settingsFloat('lighting-vignette-strength', 0.25)))


def _shadowQuality() -> str:
    return _settingsStr('shadow-quality', 'high').strip().lower()  # off | low | medium | high


def _cloudQuality() -> str:
    return _settingsStr('sky-cloud-quality', 'high').strip().lower()  # off | low | medium | high


def _fogDensityMult() -> float:
    return max(0.1, min(3.0, _settingsFloat('fog-density-multiplier', 1.0)))


# ─────────────────────────────────────────────────────────────────────────────
# Math / colour helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _lerpF(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerpT(a: tuple | None, b: tuple | None, t: float) -> tuple | None:
    if a is None and b is None:
        return None
    if a is None:
        a = tuple(0.0 for _ in b)
    if b is None:
        b = tuple(0.0 for _ in a)
    return tuple(_lerpF(float(ai), float(bi), t) for ai, bi in zip(a, b))


def _smoothstep01(v: float) -> float:
    """Cubic 0..1 easing used for celestial and fixture transitions."""
    v = _clamp(float(v), 0.0, 1.0)
    return v * v * (3.0 - 2.0 * v)


def _smootherstep01(v: float) -> float:
    """Quintic easing with zero velocity and acceleration at both ends.

    Lighting keyframes are evaluated frequently enough that a merely linear or
    cubic transition can expose a tiny change in velocity at sunrise/sunset.
    Quintic easing keeps sun motion, sky colour, fog, bloom and lamp envelopes
    visually continuous without changing the authored keyframe values.
    """
    v = _clamp(float(v), 0.0, 1.0)
    return v * v * v * (v * (v * 6.0 - 15.0) + 10.0)


def _lerpAngleDeg(a: float, b: float, t: float) -> float:
    """Interpolate an angle over the shortest arc.

    Straight tuple interpolation makes a moving sun occasionally rotate almost
    a full turn at a keyframe boundary.  Besides looking wrong, that can make a
    directional shadow map shear into long, spiky shapes for a frame.
    """
    delta = (float(b) - float(a) + 180.0) % 360.0 - 180.0
    return float(a) + delta * float(t)


def _lerpHpr(a: tuple | None, b: tuple | None, t: float) -> tuple | None:
    if a is None or b is None:
        return _lerpT(a, b, t)
    if len(a) < 3 or len(b) < 3:
        return _lerpT(a, b, t)
    return (
        _lerpAngleDeg(float(a[0]), float(b[0]), t),
        _lerpF(float(a[1]), float(b[1]), t),
        _lerpAngleDeg(float(a[2]), float(b[2]), t),
    )


def _moonHprForHour(hour: float) -> tuple[float, float, float]:
    """Return a continuous east-to-west moon path for an outdoor night."""
    progress = ((float(hour) - 18.0) % 24.0) / 12.0
    progress = _clamp(progress, 0.0, 1.0)
    heading = 90.0 + 180.0 * progress
    pitch = -(8.0 + 50.0 * math.sin(math.pi * progress))
    return (heading, pitch, 0.0)


def _applyColorTemp(color: tuple) -> tuple:
    bias = _colorTempBias()
    r, g, b, a = color
    if bias > 0:
        r = min(1.0, r + bias * 0.08)
        b = max(0.0, b - bias * 0.06)
    elif bias < 0:
        b = min(1.0, b - bias * 0.08)
        r = max(0.0, r + bias * 0.06)
    return (r, g, b, a)


def _scaleColor(color: tuple, scale: float) -> tuple:
    return (color[0] * scale, color[1] * scale, color[2] * scale, color[3])


def _desaturateRGBA(c: tuple, amt: float) -> tuple:
    """Desaturate an RGBA tuple toward gray by amt in [0,1]."""
    try:
        r, g, b, a = float(c[0]), float(c[1]), float(c[2]), float(c[3])
    except Exception:
        return c
    amt = _clamp(float(amt), 0.0, 1.0)
    gray = (r + g + b) / 3.0
    r = r * (1.0 - amt) + gray * amt
    g = g * (1.0 - amt) + gray * amt
    b = b * (1.0 - amt) + gray * amt
    return (r, g, b, a)


def _clampChromaRGBA(c: tuple, max_chroma: float) -> tuple:
    """Clamp chroma to avoid extreme channel separation on directionals.

    This reduces camera-dependent RGB banding on flat/billboarded geometry when
    multiple colored directionals overlap (key/fill/rim).
    """
    try:
        r, g, b, a = float(c[0]), float(c[1]), float(c[2]), float(c[3])
    except Exception:
        return c
    max_chroma = max(0.0, float(max_chroma))
    gray = (r + g + b) / 3.0
    dr, dg, db = r - gray, g - gray, b - gray
    chroma = max(abs(dr), abs(dg), abs(db))
    if chroma <= max_chroma or chroma <= 1e-8:
        return (r, g, b, a)
    s = max_chroma / chroma
    return (gray + dr * s, gray + dg * s, gray + db * s, a)


def _sunDirFromHpr(hpr: tuple[float, float, float]) -> Vec3:
    """Compute a stable sun direction from (H, P, R).

    This project’s zone profiles historically use a sign convention where a
    *negative* pitch indicates a sun above the horizon. Panda3D DirectionalLight
    expects a direction vector pointing *where the light shines* (rays travel),
    so we flip pitch when converting profile HPR -> direction.
    """
    try:
        h, p, r = float(hpr[0]), float(hpr[1]), float(hpr[2])
    except Exception:
        h, p, r = 0.0, -45.0, 0.0

    # Enforce a minimum downward pitch so ground/roads receive light.
    # Negative pitch points downward (shining down on the scene).
    if p > -18.0:
        p = -18.0

    q = Quat()
    q.setHpr(Vec3(h, p, r))
    d = q.getForward()
    try:
        d.normalize()
    except Exception:
        pass
    return d


def _celestialTowardDirFromHpr(hpr: tuple[float, float, float]) -> Vec3:
    """Return the unclamped world direction toward the visible celestial body."""
    try:
        h = math.radians(float(hpr[0]))
        p = math.radians(float(hpr[1]))
    except Exception:
        h = math.radians(135.0)
        p = math.radians(-42.0)
    direction = Vec3(math.sin(h) * math.cos(p),
                     -math.cos(h) * math.cos(p),
                     -math.sin(p))
    try:
        direction.normalize()
    except Exception:
        direction = Vec3(0, -0.707, 0.707)
    return direction


def _scaledShadowRes(spec: dict) -> int:
    q = _shadowQuality().strip().lower()
    if q == 'off':
        return 0
    base_res = int(spec.get('shadowRes', 1024))
    scale = float(spec.get('shadowResScale', 1.0))
    if q == 'low':
        target = min(base_res, 512)
    elif q == 'medium':
        target = min(base_res, 1024)
    else:
        target = min(base_res, 2048)
    res = int(round(target * _clamp(scale, 0.5, 1.5)))
    valid = (512, 1024, 2048, 4096)
    return min(valid, key=lambda v: abs(v - max(512, min(4096, res))))


_SPEC_TAG         = 'osl_added_material'
_LIGHTON_TAG      = 'osl_cleared_lightoff'
_SHADOW_HIDE_TAG  = 'osl_hide_from_shadow'
_NIGHT_CARD_TAG   = 'osl_night_card_tint'
_nightTintedNodes: list[tuple[NodePath, bool, Vec4]] = []
_nightWorldTintState: tuple[NodePath, bool, Vec4] | None = None
_avatarTintState: tuple[NodePath, bool, Vec4] | None = None

# Flattened DNA worlds mix geometry that participates in Panda's generated
# lighting shader with legacy batches that remain effectively unlit.  Changing
# the inherited light list at dusk therefore makes one group ambient-dark while
# the other stays texture-bright.  Keep a dedicated ambient light in the global
# light list for the lifetime of the rig and fade its color instead of replacing
# the world's LightAttrib at dusk.  This avoids per-batch shader/state changes
# while allowing inherited lamp PointLights to keep working normally.
_NIGHT_LIGHTING_THRESHOLD = 0.04
_nightWorldLightNp: NodePath | None = None


def _forceLightingOnSubtree(root: NodePath) -> None:
    """Remove only explicit *lighting-off* overrides from outdoor geometry.

    Older revisions cleared the entire LightAttrib on every child.  That also
    deleted per-node lights owned by props/other systems.  ``clearLightOff`` is
    the narrow operation we actually need and preserves unrelated light state.
    """
    if root is None or root.isEmpty():
        return
    try:
        nodes = root.findAllMatches('**;+s')
        for i in range(nodes.getNumPaths()):
            np = nodes.getPath(i)
            try:
                if hasattr(np, 'clearLightOff'):
                    np.clearLightOff()
                np.setPythonTag(_LIGHTON_TAG, True)
            except Exception:
                pass
    except Exception:
        try:
            if hasattr(root, 'clearLightOff'):
                root.clearLightOff()
            root.setPythonTag(_LIGHTON_TAG, True)
        except Exception:
            pass


def _isPlanarGeom(geom: Geom) -> bool:
    """Return True if every sampled triangle normal is (nearly) parallel.

    Used to detect 2D cards / billboards (foliage planes, sign cards) which are
    flat and small, as opposed to real 3D objects.
    """
    try:
        vdata = geom.getVertexData()
        if vdata is None:
            return False
        num_rows = vdata.getNumRows()
        if num_rows <= 0 or num_rows > _PLANE_MAX_VERTS:
            return False
        vr = GeomVertexReader(vdata, 'vertex')
        if not vdata.hasColumn('vertex'):
            return False

        def _pos(row: int) -> Vec3:
            vr.setRow(row)
            return vr.getData3()

        ref: Vec3 | None = None
        sampled = 0
        for pi in range(geom.getNumPrimitives()):
            prim = geom.getPrimitive(pi)
            if prim is None:
                continue
            try:
                decomp = prim.decompose()
            except Exception:
                decomp = prim
            try:
                decomp = decomp.decompose()
            except Exception:
                pass
            try:
                npr = decomp.getNumPrimitives()
            except Exception:
                npr = 0
            for t in range(npr):
                start = decomp.getPrimitiveStart(t)
                end   = decomp.getPrimitiveEnd(t)
                if end - start < 3:
                    continue
                try:
                    i0 = decomp.getVertex(start + 0)
                    i1 = decomp.getVertex(start + 1)
                    i2 = decomp.getVertex(start + 2)
                except Exception:
                    continue
                if i0 >= num_rows or i1 >= num_rows or i2 >= num_rows:
                    continue
                n = (_pos(i1) - _pos(i0)).cross(_pos(i2) - _pos(i0))
                if n.lengthSquared() <= 1e-12:
                    continue
                n.normalize()
                if ref is None:
                    ref = n
                elif ref.dot(n) < 0.995:
                    return False
                sampled += 1
                if sampled >= 64:
                    break
            if sampled >= 64:
                break
        return ref is not None
    except Exception:
        return False


def _enableAllShadowCasters(root: NodePath) -> None:
    """Make every piece of outdoor geometry visible to the shadow pass.

    Older builds excluded small planar Geoms to avoid solid foliage-card
    silhouettes.  That also removed signs, fences, tree canopies, facade cards,
    and many small props entirely.  A complete silhouette is preferable to a
    missing shadow, and the two-sided depth state handles mixed legacy winding.
    """
    if root is None or root.isEmpty():
        return
    try:
        nodes = root.findAllMatches('**/+GeomNode;+s')
        for i in range(nodes.getNumPaths()):
            np = nodes.getPath(i)
            try:
                np.show(_SHADOW_CAM_MASK)
                if np.getPythonTag(_SHADOW_HIDE_TAG):
                    np.clearPythonTag(_SHADOW_HIDE_TAG)
            except Exception:
                pass
    except Exception:
        pass


def _captureShaderState(np: NodePath | None, avatar: bool = False) -> None:
    """Capture a root NodePath's local ShaderAttrib once for exact teardown."""
    global _geomShaderState, _avatarShaderState
    if np is None or np.isEmpty():
        return
    slot = _avatarShaderState if avatar else _geomShaderState
    if slot is not None and slot[0] == np:
        return
    try:
        attrib = np.getAttrib(ShaderAttrib.getClassType())
    except Exception:
        attrib = None
    if avatar:
        _avatarShaderState = (np, attrib)
    else:
        _geomShaderState = (np, attrib)


def _restoreShaderStates(releaseExtras: bool = True) -> None:
    """Restore root shader state that OutdoorLighting temporarily replaced."""
    global _geomShaderState, _avatarShaderState, _worldShadowTextureState
    global _extraShaderStates
    # Explicit receivers must be removed before their inherited osl_* inputs.
    # This ordering is essential during streamed-zone transitions.
    for entry in list(_extraShaderStates):
        _restoreExtraShaderState(entry)
    if releaseExtras:
        _extraShaderStates = []
    for state in (_geomShaderState, _avatarShaderState):
        if state is None:
            continue
        np, attrib = state
        try:
            if np is not None and not np.isEmpty():
                np.clearAttrib(ShaderAttrib.getClassType())
                if attrib is not None:
                    np.setAttrib(attrib)
        except Exception:
            pass
    _geomShaderState = None
    _avatarShaderState = None
    if _worldShadowTextureState is not None:
        np, attrib = _worldShadowTextureState
        try:
            if np is not None and not np.isEmpty():
                np.clearAttrib(TextureAttrib.getClassType())
                if attrib is not None:
                    np.setAttrib(attrib)
        except Exception:
            pass
    _worldShadowTextureState = None
    input_names = (
        'osl_SunDirWorld', 'osl_SunColor', 'osl_Ambient', 'osl_WorldGrade',
        'osl_SkyAmbient', 'osl_GroundAmbient', 'osl_CameraPosWorld',
        'osl_SpecularStrength', 'osl_CelShadingMode', 'osl_FogColor', 'osl_FogParams', 'osl_FogMode',
        'osl_ShadowFloor', 'osl_DebugMode', 'osl_ShadowMatrix',
        'osl_ShadowMap', 'osl_ShadowTexel', 'osl_ShadowFilterRadius',
        'osl_ShadowBias', 'osl_ShadowOn',
        'osl_LampPosWorld0', 'osl_LampPosWorld1',
        'osl_LampPosWorld2', 'osl_LampPosWorld3',
        'osl_LampGroundPos0', 'osl_LampGroundPos1',
        'osl_LampGroundPos2', 'osl_LampGroundPos3',
        'osl_LampColor0', 'osl_LampColor1', 'osl_LampColor2', 'osl_LampColor3',
        'osl_PointShadowOn',
    )
    targets = [getattr(base, 'render', None)]
    if _activeGeom is not None:
        targets.append(_activeGeom)
    for target in targets:
        if target is None or target.isEmpty():
            continue
        for name in input_names:
            try:
                target.clearShaderInput(name)
            except Exception:
                pass


def _restoreNightWorldTint(target: NodePath | None = None) -> None:
    global _nightWorldTintState
    if _nightWorldTintState is not None:
        root, had_scale, old_scale = _nightWorldTintState
        try:
            if root is not None and not root.isEmpty():
                if had_scale:
                    root.setColorScale(old_scale)
                else:
                    root.clearColorScale()
        except Exception:
            pass
        _nightWorldTintState = None
    if target is not None and not target.isEmpty():
        try:
            if _nightWorldTintState is None and hasattr(target, 'clearColorScale'):
                pass
        except Exception:
            pass


def _nightWorldGrade(night: float) -> Vec4:
    blend = _smootherstep01((float(night) - 0.04) / 0.72)
    return Vec4(
        _lerpF(1.0, 0.40, blend),
        _lerpF(1.0, 0.45, blend),
        _lerpF(1.0, 0.58, blend),
        1.0,
    )


def _syncNightWorldLighting(root: NodePath | None, night: float) -> None:
    """Update the stable global night ambient without replacing world lights.

    Root-level all-off/set LightAttrib overrides make flattened DNA batches
    resolve inconsistently and also mask inherited PointLights.  The night light
    therefore remains on base.render from rig creation to destruction; only its
    color changes over time.
    """
    if root is None or root.isEmpty():
        return
    try:
        # Clean up the override used by older revisions of this fix.
        if root.hasLightOff() and hasattr(root, 'clearLightOff'):
            root.clearLightOff()
        if _nightWorldLightNp is None or _nightWorldLightNp.isEmpty():
            return
        if night > _NIGHT_LIGHTING_THRESHOLD:
            blend = _smootherstep01(
                (float(night) - _NIGHT_LIGHTING_THRESHOLD) /
                (1.0 - _NIGHT_LIGHTING_THRESHOLD))
            # Raise the ambient contribution smoothly as the directional sun
            # retires.  Combined with the darker root grade this keeps distant
            # geometry readable without making nearby surfaces fullbright.
            color = Vec4(
                0.28 * blend,
                0.30 * blend,
                0.42 * blend,
                1.0,
            )
            _nightWorldLightNp.node().setColor(color)
        else:
            _nightWorldLightNp.node().setColor(Vec4(0, 0, 0, 1))
    except Exception:
        pass


def _syncNightWorldTint(root: NodePath | None, night: float) -> None:
    global _nightWorldTintState
    if _worldShadersActive():
        # The custom receiver grades ambient light internally, before adding
        # local lamps.  A root ColorScale would dim lamps too and make night
        # fixtures ineffective.
        _restoreNightWorldTint()
        return
    if night <= 0.04 or root is None or root.isEmpty():
        _restoreNightWorldTint()
        return
    if _nightWorldTintState is None or _nightWorldTintState[0] != root:
        _restoreNightWorldTint()
        try:
            had_scale = bool(root.hasColorScale())
        except Exception:
            had_scale = False
        try:
            old_scale = Vec4(root.getColorScale()) if had_scale else Vec4(1, 1, 1, 1)
        except Exception:
            old_scale = Vec4(1, 1, 1, 1)
        _nightWorldTintState = (root, had_scale, old_scale)

    root, _had_scale, old_scale = _nightWorldTintState
    grade = _nightWorldGrade(night)
    try:
        root.setColorScale(Vec4(old_scale.x * grade.x,
                                old_scale.y * grade.y,
                                old_scale.z * grade.z,
                                old_scale.w), 200)
    except Exception:
        pass


def _restoreNightCardTints() -> None:
    global _nightTintedNodes
    if not _nightTintedNodes:
        return
    for np, had_scale, old_scale in _nightTintedNodes:
        try:
            if np is not None and not np.isEmpty():
                if had_scale:
                    np.setColorScale(old_scale)
                else:
                    np.clearColorScale()
        except Exception:
            pass
    _nightTintedNodes.clear()


def _syncNightCardTints(root: NodePath | None, night: float) -> None:
    """Compatibility hook: child cards must never receive a second night grade.

    Older revisions tinted billboard/card children in addition to the world root,
    producing the documented half-dark / near-black upper geometry.  Restore any
    stale child state once and leave all grading to ``_syncNightWorldTint``.
    """
    if _nightTintedNodes:
        _restoreNightCardTints()


def _restoreAvatarNightTint() -> None:
    global _avatarTintState
    if _avatarTintState is None:
        return
    np, had_scale, old_scale = _avatarTintState
    try:
        if np is not None and not np.isEmpty():
            if had_scale:
                np.setColorScale(old_scale)
            else:
                np.clearColorScale()
    except Exception:
        pass
    _avatarTintState = None


def _syncAvatarNightTint(night: float) -> None:
    global _avatarTintState
    lav = getattr(base, 'localAvatar', None)
    if lav is None or lav.isEmpty() or night <= 0.04:
        _restoreAvatarNightTint()
        return
    if _avatarTintState is None or _avatarTintState[0] != lav:
        _restoreAvatarNightTint()
        try:
            had_scale = bool(lav.hasColorScale())
            old_scale = Vec4(lav.getColorScale()) if had_scale else Vec4(1, 1, 1, 1)
        except Exception:
            had_scale = False
            old_scale = Vec4(1, 1, 1, 1)
        _avatarTintState = (lav, had_scale, old_scale)
    _np, _had, old = _avatarTintState
    grade = _nightWorldGrade(night)
    try:
        lav.setColorScale(Vec4(old.x * grade.x, old.y * grade.y, old.z * grade.z, old.w), 200)
    except Exception:
        pass


def _lampPositionRelativeTo(lampNp: NodePath, relativeTo: NodePath) -> Vec3:
    fallback = lampNp.getPos(relativeTo)
    try:
        bounds = lampNp.getTightBounds(relativeTo)
        if bounds and len(bounds) == 2:
            mins, maxs = bounds
            size = maxs - mins
            if (0.05 < float(size.z) <= 32.0
                    and float(size.x) <= 24.0 and float(size.y) <= 24.0):
                return Vec3(
                    (mins.x + maxs.x) * 0.5,
                    (mins.y + maxs.y) * 0.5,
                    maxs.z - min(0.35, float(size.z) * 0.04),
                )
    except Exception:
        pass
    return Vec3(fallback.x, fallback.y, fallback.z + 4.5)


def cacheLampFixtures(geom) -> None:
    if geom is None or geom.isEmpty():
        return
    entries: list[tuple[Vec3, Vec3]] = []
    try:
        nodes = geom.findAllMatches('**/*;+s')
        for i in range(nodes.getNumPaths()):
            np = nodes.getPath(i)
            name = np.getName().lower()
            if not any(k in name for k in _LAMP_KEYWORDS):
                continue
            for bulb_name in ('p13', 'p23'):
                try:
                    bulbs = np.findAllMatches('**/' + bulb_name)
                    for bulb_index in range(bulbs.getNumPaths()):
                        bulbs.getPath(bulb_index).setLightOff(1)
                        bulbs.getPath(bulb_index).setColorScaleOff(300)
                except Exception:
                    pass
            pos = _lampPositionRelativeTo(np, geom)
            ground_z = float(pos.z - 14.30)
            try:
                bounds = np.getTightBounds(geom)
                if bounds and len(bounds) == 2:
                    mins, _ = bounds
                    if -500.0 < float(mins.z) < 500.0:
                        ground_z = float(mins.z) + 0.04
            except Exception:
                pass
            ground_pt = Vec3(pos.x, pos.y, ground_z)
            duplicate = next((idx for idx, old in enumerate(entries)
                              if (pos.x - old[0].x) ** 2 + (pos.y - old[0].y) ** 2 < 9.0), None)
            if duplicate is not None:
                if pos.z > entries[duplicate][0].z:
                    entries[duplicate] = (Vec3(pos), ground_pt)
                continue
            entries.append((Vec3(pos), ground_pt))
            if len(entries) >= _LAMP_LIGHT_LIMIT:
                break
        if entries:
            geom.setPythonTag(
                _LAMP_POSITION_TAG,
                [((float(b.x), float(b.y), float(b.z)), (float(g.x), float(g.y), float(g.z))) for b, g in entries],
            )
    except Exception as error:
        _dbg(f"unable to cache lamp fixtures: {error!r}")


def _scanForLampNodes(geom) -> list[Any]:
    if geom is None or geom.isEmpty():
        return []
    try:
        cached = geom.getPythonTag(_LAMP_POSITION_TAG)
        if cached:
            return list(cached)
    except Exception:
        pass

    # Street fixtures are static. Cache their bulb/ground positions once so the
    # 10 Hz nearest-light update does not call getTightBounds() hundreds of
    # times per second on flattened DNA.
    try:
        cacheLampFixtures(geom)
        cached = geom.getPythonTag(_LAMP_POSITION_TAG)
        if cached:
            return list(cached)
    except Exception:
        pass

    results: list[Any] = []
    try:
        nodes = geom.findAllMatches('**/*;+s')
        for i in range(nodes.getNumPaths()):
            np = nodes.getPath(i)
            name = np.getName().lower()
            if any(k in name for k in _LAMP_KEYWORDS):
                results.append(np)
            if len(results) >= _LAMP_LIGHT_LIMIT:
                break
    except Exception:
        pass
    return results


def _lampWorldPosition(lampNp: Any) -> Vec3:
    try:
        if isinstance(lampNp, (list, tuple)) and len(lampNp) == 2:
            local_pt = Vec3(*lampNp[0])
            if _activeGeom is not None and not _activeGeom.isEmpty():
                return _activeGeom.getMat(base.render).xformPoint(local_pt)
            return local_pt
        if isinstance(lampNp, NodePath):
            return _lampPositionRelativeTo(lampNp, base.render)
        return Vec3(lampNp)
    except Exception:
        return Vec3(0, 0, 4.5)


def _lampGroundPosition(lampNp: Any) -> Vec3:
    try:
        if isinstance(lampNp, (list, tuple)) and len(lampNp) == 2:
            local_pt = Vec3(*lampNp[1])
            if _activeGeom is not None and not _activeGeom.isEmpty():
                return _activeGeom.getMat(base.render).xformPoint(local_pt)
            return local_pt
        if isinstance(lampNp, NodePath):
            light_pos = _lampPositionRelativeTo(lampNp, base.render)
            bounds = lampNp.getTightBounds(base.render)
            if bounds and len(bounds) == 2:
                mins, _ = bounds
                if -500.0 < float(mins.z) < 500.0:
                    return Vec3(light_pos.x, light_pos.y, float(mins.z) + 0.04)
            return Vec3(light_pos.x, light_pos.y, _groundZBelowLamp(light_pos))
        pos = Vec3(lampNp)
        return Vec3(pos.x, pos.y, _groundZBelowLamp(pos))
    except Exception:
        return Vec3(0, 0, 0.04)


def _allLampNodes() -> list[Any]:
    return list(_lampGeomNps)


def _lampStrength() -> float:
    perpetualNight = _activeStyle in (
        'dl', 'dl_street', 'sellbot_hq', 'cashbot_hq',
        'lawbot_hq', 'bossbot_hq', 'factory_int',
    )
    if perpetualNight:
        return 1.0
    if _activeStyle in _INDOOR_STYLES or not _ZONE_PROFILES.get(_activeStyle, {}).get('dayNightEnabled', True):
        return 1.0
    return _smootherstep01((_nightFactor() - 0.04) / 0.62)


def _setLampLightTransform(plNp: NodePath, lampNp: Any, strength: float) -> None:
    lightPos = _lampWorldPosition(lampNp)
    plNp.setPos(base.render, lightPos)
    node = plNp.node()
    # A street lamp is a compact area emitter, not a theatre spotlight.  A
    # short-range point source reaches the ground, post, and nearby facade even
    # when legacy TTC cards have unusual normals, while attenuation keeps it
    # from washing the whole map.
    node.setColor(Vec4(4.0 * strength, 3.2 * strength, 1.4 * strength, 1.0))


def _getLampPoolTexture() -> Texture:
    global _lampPoolTex
    if _lampPoolTex is not None:
        return _lampPoolTex
    size = 96
    image = PNMImage(size, size, 4)
    for y in range(size):
        for x in range(size):
            nx = (x + 0.5) / size * 2.0 - 1.0
            ny = (y + 0.5) / size * 2.0 - 1.0
            radius = math.sqrt(nx * nx + ny * ny)
            alpha = (1.0 - _smoothstep01((radius - 0.05) / 0.95)) ** 1.65
            image.setXelA(x, y, 1.0, 0.72, 0.30, alpha * 0.46)
    _lampPoolTex = Texture('outdoorLampPoolTexture')
    _lampPoolTex.load(image)
    _lampPoolTex.setWrapU(Texture.WMClamp)
    _lampPoolTex.setWrapV(Texture.WMClamp)
    _lampPoolTex.setMinfilter(Texture.FTLinear)
    _lampPoolTex.setMagfilter(Texture.FTLinear)
    return _lampPoolTex


def _createLampPool(index: int) -> NodePath:
    maker = CardMaker(f'outdoorLampPoolCard_{index}')
    radius = 7.5
    maker.setFrame(-radius, radius, -radius, radius)
    parent = _lightRig if (_lightRig is not None and not _lightRig.isEmpty()) else base.render
    pool = parent.attachNewNode(maker.generate())
    pool.setP(-90.0)
    pool.setTexture(_getLampPoolTexture(), 1)
    pool.setTransparency(TransparencyAttrib.MAlpha)
    pool.setAttrib(ColorBlendAttrib.make(
        ColorBlendAttrib.MAdd,
        ColorBlendAttrib.OIncomingAlpha,
        ColorBlendAttrib.OOne,
    ))
    pool.setTwoSided(True)
    pool.setDepthWrite(False)
    pool.setDepthOffset(-4)
    pool.setBin('transparent', 25)
    pool.setLightOff(200)
    pool.setShaderOff(200)
    return pool


def _groundZBelowLamp(light_pos: Vec3) -> float:
    """Find the highest walkable collision surface below a lamp head."""
    ray_np = None
    try:
        ray = CollisionRay(light_pos.x, light_pos.y, light_pos.z + 2.0,
                           0.0, 0.0, -1.0)
        ray_node = CollisionNode('outdoorLampGroundProbe')
        ray_node.setFromCollideMask(BitMask32.allOn())
        ray_node.setIntoCollideMask(BitMask32.allOff())
        ray_node.addSolid(ray)
        ray_np = base.render.attachNewNode(ray_node)
        queue = CollisionHandlerQueue()
        traverser = CollisionTraverser('outdoorLampGroundProbeTraverser')
        traverser.addCollider(ray_np, queue)
        target = _activeGeom if (_activeGeom is not None and not _activeGeom.isEmpty()) else base.render
        traverser.traverse(target)
        candidates: list[float] = []
        for index in range(queue.getNumEntries()):
            entry = queue.getEntry(index)
            point = entry.getSurfacePoint(base.render)
            if point.z > light_pos.z - 5.0:
                continue
            try:
                normal = entry.getSurfaceNormal(base.render)
                if normal.z < 0.45:
                    continue
            except Exception:
                pass
            candidates.append(float(point.z))
        if candidates:
            return max(candidates) + 0.06
    except Exception:
        pass
    finally:
        try:
            if ray_np is not None and not ray_np.isEmpty():
                ray_np.removeNode()
        except Exception:
            pass
    return float(light_pos.z - 14.30)


def _setLampPoolTransform(poolNp: NodePath, lampNp: Any, strength: float) -> None:
    ground_pos = _lampGroundPosition(lampNp)
    poolNp.setPos(base.render, ground_pos.x, ground_pos.y, ground_pos.z)
    poolNp.setColorScale(1.0, 1.0, 1.0, _clamp(strength, 0.0, 1.0))
    if strength > 0.01:
        poolNp.show()
    else:
        poolNp.hide()


def _setLampPoolStrength(poolNp: NodePath, strength: float) -> None:
    poolNp.setColorScale(1.0, 1.0, 1.0, _clamp(strength, 0.0, 1.0))
    if strength > 0.01:
        poolNp.show()
    else:
        poolNp.hide()


def _enableLampLights(style: str) -> None:
    _syncLampLights(force=True)


def _disableLampLights(teardown: bool = False) -> None:
    global _lampLights, _lampPoolNps, _lampLightsActive, _activeLampSignature
    if not teardown:
        # Keep the same four PointLight NodePaths in the inherited light list for
        # the entire zone.  Removing and recreating them at sunset/midnight makes
        # flattened DNA batches retain different auto-generated shader variants.
        for plNp in _lampLights:
            try:
                if plNp is not None and not plNp.isEmpty():
                    plNp.node().setColor(Vec4(0, 0, 0, 1))
            except Exception:
                pass
        for poolNp in _lampPoolNps:
            try:
                if poolNp is not None and not poolNp.isEmpty():
                    poolNp.hide()
            except Exception:
                pass
        _lampLightsActive = False
        _activeLampSignature = ()
        return

    for plNp in _lampLights:
        try:
            base.render.clearLight(plNp)
        except Exception:
            pass
        try:
            if _activeGeom is not None and not _activeGeom.isEmpty():
                _activeGeom.clearLight(plNp)
        except Exception:
            pass
        try:
            if not plNp.isEmpty():
                plNp.removeNode()
        except Exception:
            pass
    _lampLights.clear()
    for poolNp in _lampPoolNps:
        try:
            if poolNp is not None and not poolNp.isEmpty():
                poolNp.removeNode()
        except Exception:
            pass
    _lampPoolNps.clear()
    _lampLightsActive = False
    _activeLampSignature = ()


def _nightFactor(hour: float | None = None) -> float:
    if _activeStyle in ('dl', 'dl_street', 'sellbot_hq', 'cashbot_hq', 'lawbot_hq', 'bossbot_hq', 'factory_int'):
        return 1.0
    if _activeStyle in _INDOOR_STYLES or not _ZONE_PROFILES.get(_activeStyle, {}).get('dayNightEnabled', True):
        return 0.0
    h = _timeOfDay if hour is None else float(hour)
    h %= 24.0
    if h >= 20.0 or h <= 5.0:
        return 1.0
    if 18.0 < h < 20.0:
        return _smootherstep01((h - 18.0) / 2.0)
    if 5.0 < h < 7.0:
        return _smootherstep01((7.0 - h) / 2.0)
    return 0.0


def _syncLampLights(force: bool = False) -> None:
    global _lampRefreshAccum, _lampLights, _lampPoolNps, _lampLightsActive, _activeLampSignature
    wantsLamps = False
    if _lampGeomNps and _settingsBool('lighting-streetlamps-enabled', True):
        isIndoor = _activeStyle in _INDOOR_STYLES or not _ZONE_PROFILES.get(_activeStyle, {}).get('dayNightEnabled', True)
        perpetualNight = _activeStyle in (
            'dl', 'dl_street', 'sellbot_hq', 'cashbot_hq',
            'lawbot_hq', 'bossbot_hq',
        )
        wantsLamps = isIndoor or perpetualNight or _nightFactor() > 0.04

    rankedLamps = _allLampNodes()
    if not rankedLamps:
        _disableLampLights()
        return

    strength = _lampStrength() if wantsLamps else 0.0
    refPos = Vec3(0, 0, 0)
    try:
        if hasattr(base, 'localAvatar') and base.localAvatar and not base.localAvatar.isEmpty():
            refPos = base.localAvatar.getPos(base.render)
        elif hasattr(base, 'camera') and base.camera and not base.camera.isEmpty():
            refPos = base.camera.getPos(base.render)
    except Exception:
        pass

    def _distSq(lampNp: Any) -> float:
        lp = _lampWorldPosition(lampNp)
        return (lp.x - refPos.x)**2 + (lp.y - refPos.y)**2 + (lp.z - refPos.z)**2

    sortedByDist = sorted(rankedLamps, key=_distSq)
    closestLamps = sortedByDist[:_MAX_SHADER_LAMP_LIGHTS]

    # Allocate the pools and the fixed four PointLights once per zone, even when
    # the current time is sunset/day.  Their nodes stay bound to base.render and
    # daylight is represented by zero RGB rather than by changing LightAttrib.
    if not _lampLights:
        parent = _lightRig if (_lightRig is not None and not _lightRig.isEmpty()) else base.render
        for idx, lampNp in enumerate(rankedLamps):
            try:
                poolNp = _createLampPool(idx)
                _setLampPoolTransform(poolNp, lampNp, strength)
                _lampPoolNps.append(poolNp)
            except Exception:
                pass

        for idx in range(_MAX_SHADER_LAMP_LIGHTS):
            try:
                pl = PointLight(f'outdoorLampLight_{idx}')
                # A larger constant term prevents camera-near geometry from
                # blowing out while the brighter source remains useful at the
                # ground and nearby facade.
                pl.setAttenuation(Vec3(2.0, 0.04, 0.006))
                try:
                    pl.setMaxDistance(20.0)
                except Exception:
                    pass
                plNp = parent.attachNewNode(pl)
                if idx < len(closestLamps):
                    _setLampLightTransform(plNp, closestLamps[idx], strength)
                else:
                    pl.setColor(Vec4(0, 0, 0, 1))
                base.render.setLight(plNp)
                _lampLights.append(plNp)
            except Exception:
                pass
    if not wantsLamps:
        _disableLampLights()
        _lampRefreshAccum = 0.0
        return

    for idx, poolNp in enumerate(_lampPoolNps):
        try:
            if idx < len(rankedLamps):
                _setLampPoolStrength(poolNp, strength)
            else:
                poolNp.hide()
        except Exception:
            pass
    for idx, plNp in enumerate(_lampLights):
        try:
            if idx < len(closestLamps):
                _setLampLightTransform(plNp, closestLamps[idx], strength)
            else:
                plNp.node().setColor(Vec4(0, 0, 0, 1))
        except Exception:
            pass
    _lampLightsActive = True
    _lampRefreshAccum = 0.0
    # Lamp NodePaths may have just been created or replaced.  Refresh the
    # root-level night LightAttrib so the current virtualized PointLights are
    # included rather than being masked by the ambient-only override.
    if _activeGeom is not None and not _activeGeom.isEmpty():
        _syncNightWorldLighting(_activeGeom, _nightFactor())


# ─────────────────────────────────────────────────────────────────────────────
# Day / night interpolation
# ─────────────────────────────────────────────────────────────────────────────

def _syncTimeOfDayFromSettings() -> None:
    global _timeOfDay
    mode = _settingsStr('day-night-mode', 'Dynamic')
    if mode == 'Real-Time Sync':
        import time as _py_time
        lt = _py_time.localtime()
        _timeOfDay = (lt.tm_hour + lt.tm_min / 60.0 + lt.tm_sec / 3600.0) % 24.0
    elif mode == 'Always Noon':
        _timeOfDay = 12.0
    elif mode == 'Always Sunset':
        _timeOfDay = 18.0
    elif mode == 'Always Midnight':
        _timeOfDay = 0.0
    elif mode == 'Always Dawn':
        _timeOfDay = 6.0


def _evalKeyframes(zone: str, hour: float) -> dict:
    frames = _DAY_NIGHT_KEYFRAMES.get(zone)
    if not frames:
        base_zone = zone.replace('_street', '')
        frames = _DAY_NIGHT_KEYFRAMES.get(base_zone)
    if not frames:
        if zone in ('estate', 'playground', 'golf_course'):
            frames = _DAY_NIGHT_KEYFRAMES.get('tt')
    if not frames:
        return {}
    hour   = hour % 24.0
    valid  = [(h, d) for h, d in frames if d is not None]
    if not valid:
        return {}
    if len(valid) == 1:
        return dict(valid[0][1])

    prev_h, prev_d = valid[-1]
    next_h, next_d = valid[0]
    for i in range(len(valid)):
        kh, kd = valid[i]
        if kh <= hour:
            prev_h, prev_d = kh, kd
            nxt_i = (i + 1) % len(valid)
            next_h, next_d = valid[nxt_i]

    span = next_h - prev_h
    if span <= 0:
        span   = (24.0 - prev_h) + next_h
        t_prog = (hour - prev_h) % 24.0
    else:
        t_prog = hour - prev_h
    linear_t = _clamp(t_prog / span if span > 0 else 0.0, 0.0, 1.0)
    t = _smootherstep01(linear_t)

    result: dict = {}
    all_keys = set(prev_d.keys()) | set(next_d.keys())
    for key in all_keys:
        av = prev_d.get(key)
        bv = next_d.get(key)
        if isinstance(av, bool) or isinstance(bv, bool):
            result[key] = bv if linear_t >= 0.5 else av
        elif isinstance(av, (int, float)) and isinstance(bv, (int, float)):
            result[key] = _lerpF(float(av), float(bv), t)
        elif isinstance(av, (tuple, list)) or isinstance(bv, (tuple, list)):
            lerper = _lerpHpr if key in ('keyHpr', 'fillHpr', 'rimHpr', 'moonDir') else _lerpT
            result[key] = lerper(
                tuple(av) if av is not None else None,
                tuple(bv) if bv is not None else None,
                t,
            )
        else:
            result[key] = bv if linear_t >= 0.5 else av
    return result


def _getActiveSpec() -> dict:
    base_spec = _ZONE_PROFILES.get(_activeStyle, _ZONE_PROFILES['playground'])
    if not _wantDayNight() or not base_spec.get('dayNightEnabled', True):
        return base_spec
    overrides = _evalKeyframes(_activeStyle, _timeOfDay)
    merged = dict(base_spec)
    if overrides:
        merged.update(overrides)

    # A single celestial key drives the outdoor world: sun by day, moon by
    # night.  This keeps the visible moon, directional light and its shadow
    # camera in the same place.  Previously the moon disc and the blue key
    # light used unrelated headings, so the scene never looked moonlit.
    night = _nightFactor()
    if night > 0.0:
        moon_hpr = _moonHprForHour(_timeOfDay)
        hour = _timeOfDay % 24.0
        if hour >= 18.0:
            moon_blend = _smoothstep01((hour - 18.0) / 1.25)
        else:
            # Preserve a warm dawn while smoothly retiring the moon.
            moon_blend = 1.0 - _smoothstep01((hour - 5.0) / 1.5)
        current_hpr = merged.get('keyHpr', base_spec.get('keyHpr', (135, -42, 0)))
        merged['moonDir'] = moon_hpr
        merged['keyHpr'] = _lerpHpr(tuple(current_hpr), moon_hpr, moon_blend)
        merged['moonEnabled'] = night > 0.04

        style_base = _activeStyle.replace('_street', '')
        star_floor = {
            'dd': 0.24, 'br': 0.34, 'mm': 0.62, 'dl': 0.96,
        }.get(style_base, 0.82)
        merged['starBrightness'] = max(
            float(merged.get('starBrightness') or 0.0), star_floor * night)

        # Pull the key toward a readable but restrained blue-white moon.  The
        # live rig applies a separate night intensity envelope, so this creates
        # contrast without turning the terrain into blue daylight.
        old_key = tuple(merged.get('key', (0.24, 0.30, 0.52, 1.0)))
        moon_key = (0.18, 0.28, 0.68, 1.0)
        merged['key'] = _lerpT(old_key, moon_key, moon_blend * 0.92)

        # Sunset keyframes contain strong orange/red fog, fill and rim colors.
        # Once the moon is established those channels must cool together, or
        # red twilight leaks over the entire night scene despite a blue key.
        cool_targets = {
            'ambient':   (0.145, 0.180, 0.320, 1.0),
            'fill':      (0.070, 0.105, 0.225, 1.0),
            'rim':       (0.120, 0.220, 0.550, 1.0),
            'fogColor':  (0.045, 0.070, 0.170, 1.0),
            'clearColor':(0.025, 0.040, 0.120, 1.0),
            'skyScale':  (0.320, 0.420, 0.780, 1.0),
            'rayColor':  (0.420, 0.560, 1.000, 1.0),
        }
        for color_name, cool_color in cool_targets.items():
            current = merged.get(color_name)
            if current is not None:
                merged[color_name] = _lerpT(
                    tuple(current), cool_color, moon_blend * 0.88)

    # Honor the authored night shadow state.  Keeping the daytime directional
    # shadow camera alive after sunset made large flattened DNA batches sample
    # as fully occluded, producing the hard black buildings/ground polygons in
    # TTC.  Night remains shaped by the moon key plus restrained indirect fill;
    # shadow map is retired at dusk and recreated once at dawn.
    if night >= 0.20:
        merged['shadowCaster'] = False
    merged['rayIntensity'] = float(merged.get('rayIntensity') or 0.0) * max(0.0, 1.0 - night)

    if night >= 0.70:
        # Midnight keyframes historically raised exposure to compensate for
        # very weak legacy lights.  The modern moon and fixture lights no longer
        # need that compensation, which otherwise makes terrain look fullbright.
        merged['exposure'] = min(float(merged.get('exposure') or 1.0), 0.85)

    # Keep authored keyframes inside the display-referred range used by the
    # legacy renderer.  Several sunrise/sunset frames were authored with HDR
    # values while the stable CommonFilters path has no tone mapper, causing
    # whole facades and the sky to clip white for a portion of the cycle.
    # Preserve the colour ratios; only constrain the post effects that amplify
    # already-bright pixels.
    if night < 0.70:
        merged['exposure'] = min(float(merged.get('exposure') or 1.0), 1.0)
        merged['bloomIntensity'] = min(
            float(merged.get('bloomIntensity') or 0.0), 0.32)
        merged['bloomThreshold'] = max(
            float(merged.get('bloomThreshold') or 0.65), 0.72)
        merged['rayIntensity'] = min(
            float(merged.get('rayIntensity') or 0.0), 0.52)
        merged['sunBlindStrength'] = min(
            float(merged.get('sunBlindStrength') or 0.28), 0.32)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Sky helpers
# ─────────────────────────────────────────────────────────────────────────────

def _getHood():
    hood = getattr(getattr(base, 'cr', None), 'playGame', None)
    return getattr(hood, 'hood', None) if hood else None


def _dimSkyForLights() -> None:
    global _skyWasDimmed
    hood = _getHood()
    if _proceduralSky and _proceduralSky.isActive():
        # Procedural sky manages its own lighting — hide the model sky.
        if hood and getattr(hood, 'sky', None) and not hood.sky.isEmpty():
            hood.sky.hide()
        _skyWasDimmed = False
        return
    if hood and getattr(hood, 'sky', None) and not hood.sky.isEmpty():
        hood.sky.setLightOff(1)
        _skyWasDimmed = True


def _restoreSkyLighting() -> None:
    global _skyWasDimmed
    if not _skyWasDimmed:
        return
    hood = _getHood()
    if hood and getattr(hood, 'sky', None) and not hood.sky.isEmpty():
        if hasattr(hood.sky, 'clearLightOff'):
            hood.sky.clearLightOff()
        else:
            try:
                hood.sky.clearAttrib(LightAttrib.getClassType())
            except Exception:
                try:
                    hood.sky.setLightOff(0)
                except Exception:
                    pass
        hood.sky.show()
    _skyWasDimmed = False


def _tintSky(spec: dict) -> None:
    if _proceduralSky and _proceduralSky.isActive():
        return  # Sky handled by procedural system
    hood = _getHood()
    if hood and getattr(hood, 'sky', None) and not hood.sky.isEmpty():
        sc = spec.get('skyScale', (1, 1, 1, 1))
        hood.sky.setColorScale(Vec4(*sc))


def _clearSkyTint() -> None:
    hood = _getHood()
    if hood and getattr(hood, 'sky', None) and not hood.sky.isEmpty():
        hood.sky.clearColorScale()
        hood.sky.show()


# ─────────────────────────────────────────────────────────────────────────────
# Window background colour
# ─────────────────────────────────────────────────────────────────────────────

def _applyBackgroundColor(spec: dict) -> None:
    global _prevClearColor
    try:
        cc = spec.get('clearColor', (0.2, 0.4, 0.6, 1.0))
        if hasattr(base, 'win') and base.win:
            if _prevClearColor is None:
                _prevClearColor = tuple(base.win.getClearColor())
            base.win.setClearColor(Vec4(*cc))
    except Exception:
        pass


def _restoreBackgroundColor() -> None:
    global _prevClearColor
    try:
        if _prevClearColor and hasattr(base, 'win') and base.win:
            base.win.setClearColor(Vec4(*_prevClearColor))
    except Exception:
        pass
    _prevClearColor = None


# ─────────────────────────────────────────────────────────────────────────────
# Shadow / scene bounds
# ─────────────────────────────────────────────────────────────────────────────

def _computeFullZoneShadowBounds(geom, defaultArea: float) -> tuple[Vec3, float, float]:
    focus = Vec3(0.0, 0.0, 0.0)
    half  = max(240.0, float(defaultArea))
    if geom is None or geom.isEmpty():
        return (focus, half, half)
    try:
        nodes = geom.findAllMatches('**/+GeomNode;+s')
        if nodes.getNumPaths() > 0:
            all_min = Vec3(1e9, 1e9, 1e9)
            all_max = Vec3(-1e9, -1e9, -1e9)
            found = False
            for i in range(nodes.getNumPaths()):
                gnp = nodes.getPath(i)
                tb = gnp.getTightBounds(base.render) if (base and getattr(base, 'render', None)) else gnp.getTightBounds()
                if tb and len(tb) == 2:
                    all_min = Vec3(min(all_min.x, tb[0].x), min(all_min.y, tb[0].y), min(all_min.z, tb[0].z))
                    all_max = Vec3(max(all_max.x, tb[1].x), max(all_max.y, tb[1].y), max(all_max.z, tb[1].z))
                    found = True
            if found:
                center = (all_min + all_max) * 0.5
                size   = all_max - all_min
                focus  = Vec3(center[0], center[1], center[2])
                half   = max(half, max(float(size[0]), float(size[1])) * 0.55)
                return (focus, half * 0.8, half)
    except Exception:
        pass
    try:
        bounds = geom.getTightBounds()
        if bounds and len(bounds) == 2:
            mins, maxs = bounds
            center = (mins + maxs) * 0.5
            size   = maxs - mins
            focus  = Vec3(center[0], center[1], center[2])
            half   = max(half, max(float(size[0]), float(size[1])) * 0.55)
    except Exception:
        pass
    return (focus, half * 0.8, half)


# ─────────────────────────────────────────────────────────────────────────────
# Light rig
# ─────────────────────────────────────────────────────────────────────────────

def _positionShadowCaster() -> None:
    if not (_keyCastsShadows and _keyLightNp and not _keyLightNp.isEmpty()):
        return
    if _nightFactor() > 0.04:
        return
    try:
        targetNp = getattr(base, 'localAvatar', None)
        if targetNp is not None and not targetNp.isEmpty():
            target = targetNp.getPos(base.render)
        elif _shadowFocusPos is not None:
            target = Vec3(_shadowFocusPos[0], _shadowFocusPos[1], _shadowFocusPos[2])
        else:
            target = Vec3(0, 0, 0)

        # Never focus a directional shadow map on the view camera.  An orbital
        # camera changes position as it rotates around its subject, which made
        # the texel-snapped light frustum move and the shadows appear to slide
        # over stationary geometry.  The avatar (or stable zone centre during
        # loading/cutscenes) is a world-space anchor and is view-independent.

        # This world vector mirrors the NodePath HPR used by the directional
        # light and its shadow camera.
        forward = Vec3(_sunDirWorld) if _sunDirWorld is not None else Vec3(0, 1, -1)
        forward.normalize()
        # Derive the horizontal axis from azimuth, even when the sun is almost
        # overhead.  Switching between two arbitrary reference-up vectors near
        # noon rotated the shadow texture abruptly and produced needle/spike
        # artifacts during an active cycle.
        right = Vec3(forward.y, -forward.x, 0.0)
        if right.lengthSquared() < 1.0e-8:
            right = Vec3(1, 0, 0)
        right.normalize()
        up = right.cross(forward)
        up.normalize()

        texel = max(0.001, _shadowFilmArea / max(1.0, float(_shadowMapRes)))
        x_proj = target.dot(right)
        y_proj = target.dot(up)
        z_proj = target.dot(forward)

        x_snapped = round(x_proj / texel) * texel
        y_snapped = round(y_proj / texel) * texel

        snappedTarget = right * x_snapped + up * y_snapped + forward * z_proj
        shadowCamPos = snappedTarget - forward * _shadowFollowDist
        _keyLightNp.setPos(base.render, shadowCamPos)
        if _worldShadowCameraNp is not None and not _worldShadowCameraNp.isEmpty():
            _worldShadowCameraNp.setPos(base.render, shadowCamPos)
            _worldShadowCameraNp.setQuat(base.render, _keyLightNp.getQuat(base.render))
    except Exception:
        pass


def _destroyWorldShadowBuffer() -> None:
    global _worldShadowBuffer, _worldShadowCameraNp, _worldShadowMap
    try:
        if _worldShadowCameraNp is not None and not _worldShadowCameraNp.isEmpty():
            _worldShadowCameraNp.removeNode()
    except Exception:
        pass
    _worldShadowCameraNp = None
    try:
        if _worldShadowBuffer is not None:
            base.graphicsEngine.removeWindow(_worldShadowBuffer)
    except Exception:
        pass
    _worldShadowBuffer = None
    _worldShadowMap = None


def _createWorldShadowBuffer(res: int, area: float, farClip: float) -> bool:
    """Create an explicit depth camera, independent of Panda's auto-shader."""
    global _worldShadowBuffer, _worldShadowCameraNp, _worldShadowMap
    global _worldShadowCasterShader
    _destroyWorldShadowBuffer()
    try:
        if _worldShadowCasterShader is None:
            _worldShadowCasterShader = Shader.load(
                Shader.SL_GLSL,
                Filename.fromOsSpecific(_WORLD_SHADOW_CASTER_VERT),
                Filename.fromOsSpecific(_WORLD_SHADOW_CASTER_FRAG),
            )
        if _worldShadowCasterShader is None:
            return False

        fb = FrameBufferProperties()
        fb.setRgbColor(False)
        fb.setDepthBits(24)
        props = WindowProperties.size(res, res)
        flags = GraphicsPipe.BFRefuseWindow
        buffer = base.graphicsEngine.makeOutput(
            base.pipe, 'outdoorWorldShadowBuffer', -100, fb, props, flags,
            base.win.getGsg(), base.win)
        if buffer is None:
            return False
        depth = Texture('outdoorWorldShadowDepth')
        depth.setFormat(Texture.FDepthComponent)
        depth.setWrapU(Texture.WMClamp)
        depth.setWrapV(Texture.WMClamp)
        depth.setMinfilter(Texture.FTLinear)
        depth.setMagfilter(Texture.FTLinear)
        buffer.addRenderTexture(depth, GraphicsOutput.RTMBindOrCopy,
                                GraphicsOutput.RTPDepth)
        buffer.setClearDepthActive(True)
        buffer.setClearDepth(1.0)

        lens = OrthographicLens()
        lens.setFilmSize(area, area)
        lens.setNearFar(1.0, farClip)
        camera = base.makeCamera(buffer, lens=lens, scene=base.render,
                                 camName='outdoorWorldShadowCamera')
        # makeCamera() may parent auxiliary cameras beneath ShowBase's main
        # camera group.  That is correct for post-process cameras but fatal for
        # a world-space sun camera: orbiting the view then rotates/translates
        # the shadow projection as an inherited transform.  Put this camera in
        # render space before assigning its independent light transform.
        camera.wrtReparentTo(base.render)
        # A dedicated bit isolates the world depth pass from sky and reflection
        # cameras while all outdoor geometry remains opted in as a caster.
        camera.node().setCameraMask(_SHADOW_CAM_MASK)
        initial = RenderState.make(
            ShaderAttrib.make(_worldShadowCasterShader),
            ColorWriteAttrib.make(ColorWriteAttrib.COff),
        )
        initial = initial.addAttrib(
            CullFaceAttrib.make(CullFaceAttrib.MCullNone), 100)
        camera.node().setInitialState(initial)
        _worldShadowBuffer = buffer
        _worldShadowCameraNp = camera
        _worldShadowMap = depth
        return True
    except Exception as error:
        _dbg(f'custom shadow buffer creation failed: {error!r}')
        _destroyWorldShadowBuffer()
        return False


def _enableShadowCaster(spec: dict, task=None):
    """(Re)create the key light's shadow map after a deferral.

    Shadow FBO creation is deferred away from zone transitions / dusk so a
    fresh FBO never races a still-live one in the /Draw thread (the documented
    double-FBO GL deadlock on this Panda build).  Idempotent: safe to call from
    the initial zone spawn or from a later dawn transition.
    """
    global _shadowMapRes, _keyCastsShadows, _shadowFilmArea, _shadowEnablePending
    _shadowEnablePending = False
    if _keyLightNp is None or _keyLightNp.isEmpty():
        return task.done if task is not None else None
    key = _keyLightNp.node()
    defaultArea = float(spec.get('shadowArea', 180))
    fullHalf    = float(_shadowSceneRadius) / 0.8
    try:
        res = _scaledShadowRes(spec)
        # Clamp requested shadow resolution to GPU limits.
        try:
            gsg = base.win.getGsg() if getattr(base, 'win', None) else None
            if gsg:
                try:
                    max_dim = int(gsg.getMaxTextureDimension())
                except Exception:
                    max_dim = 0
                # Depth textures / shadow maps often fail silently if too large.
                if max_dim and res > max_dim:
                    _dbg(f"clamping shadowRes {res} -> {max_dim} (GPU max texture dim)")
                    res = max_dim
                # Snap down to a sane power-of-two bucket.
                if res >= 4096:
                    res = 4096
                elif res >= 2048:
                    res = 2048
                elif res >= 1024:
                    res = 1024
                else:
                    res = 512
        except Exception:
            pass
        areaScale = float(spec.get('shadowAreaScale', 1.0))
        area      = max(400.0, defaultArea * _clamp(areaScale, 0.85, 1.50))
        _shadowFilmArea = float(area)

        nearClip = 1.0
        farClip  = float(_shadowFollowDist * 2.0 + 800.0)
        if not _createWorldShadowBuffer(res, area, farClip):
            _keyCastsShadows = False
            return task.done if task is not None else None
        _shadowMapRes = res
        _keyCastsShadows = True

        # NOTE: "force huge frustum" debug knob lowers shadow texel density so
        # much it looks like blocky square lighting.  Do NOT enable it just
        # because lighting-debug is on.
        try:
            if ConfigVariableBool('lighting-shadow-force-huge-frustum', False).value:
                if _worldShadowCameraNp is not None:
                    lens = _worldShadowCameraNp.node().getLens()
                    lens.setFilmSize(max(2000.0, float(area)), max(2000.0, float(area)))
                    lens.setNearFar(1.0, 20000.0)
                _shadowFilmArea = max(2000.0, float(area))
        except Exception:
            pass
        try:
            _positionShadowCaster()
        except Exception:
            pass
        _updateWorldShadowShaderInputs(spec)
        return task.done if task is not None else None
    except Exception as outer_e:
        _dbg(f"primary shadow setup failed: {outer_e!r}; disabling custom caster")
        _destroyWorldShadowBuffer()
        _keyCastsShadows = False
        try:
            _positionShadowCaster()
        except Exception:
            pass
        return task.done if task is not None else None


def _disableShadowCaster() -> None:
    """Retire the world shadow map (dusk, teardown, or settings change)."""
    global _keyCastsShadows, _shadowMapRes, _shadowFilmArea
    _destroyWorldShadowBuffer()
    _keyCastsShadows = False
    _shadowMapRes    = 1024
    _shadowFilmArea  = 550.0
    if _activeGeom is not None and not _activeGeom.isEmpty():
        try:
            _activeGeom.setShaderInput('osl_ShadowOn', 0.0)
        except Exception:
            pass


def _spawnLightRig(spec: dict, geom=None,
                   shadowBounds: tuple[Vec3, float] | None = None,
                   enable_ambient: bool = True,
                   enable_key: bool = True,
                   enable_fill: bool = True,
                   enable_rim: bool = True,
                   enable_shadows: bool = True) -> None:
    global _lightRig, _renderLights, _keyLightNp, _nightWorldLightNp, _shadowFollowDist
    global _keyCastsShadows, _shadowFocusPos, _shadowSceneRadius
    global _shadowMapRes, _shadowFilmArea, _prevShadowCasterState, _lightRigSettingsSignature, _sunDirWorld
    global _shadowEnablePending

    intensity          = _intensityScale()
    night              = _nightFactor()
    # TTC's flattened legacy meshes contain normals that can make Panda's
    # generated directional term change radically with view pitch.  Use a cool
    # camera-independent moon fill at night and retain only a faint directional
    # accent.  Local fixture PointLights still provide the visible warm pools.
    indirectScale      = _lerpF(1.0, 0.75, night)
    directScale        = _lerpF(1.0, 0.28, night)
    _lightRig          = base.render.attachNewNode('outdoorLightRig')
    _renderLights      = []
    _keyLightNp        = None
    _nightWorldLightNp = None
    _keyCastsShadows   = False
    _shadowFocusPos    = None
    _shadowSceneRadius = 550.0
    _shadowMapRes      = 1024
    _shadowFollowDist  = float(spec.get('shadowFollowDist', 450.0))
    defaultArea        = float(spec.get('shadowArea', 550))
    areaScale          = float(spec.get('shadowAreaScale', 1.0))
    _shadowFilmArea    = max(400.0, defaultArea * _clamp(areaScale, 0.85, 1.50))
    _lightRigSettingsSignature = (_wantDynamicShadows(), _shadowQuality(), _wantWorldShadows())

    if shadowBounds is not None:
        _shadowFocusPos    = Vec3(*shadowBounds[0])
        _shadowSceneRadius = max(60.0, float(shadowBounds[1]))
        fullHalf           = float(_shadowSceneRadius) / 0.8
    else:
        focus, radius, fullHalf = _computeFullZoneShadowBounds(geom, defaultArea)
        _shadowFocusPos    = focus
        _shadowSceneRadius = radius
    try:
        _dbg(f"shadowBounds focus={_shadowFocusPos} radius={_shadowSceneRadius} fullHalf={fullHalf}")
    except Exception:
        pass

    # ── Ambient ──────────────────────────────────────────────────────────────
    if enable_ambient:
        floor    = float(spec.get('shadowDarknessFloor', 0.18)) * _lerpF(1.0, 0.75, night)
        # Lower indirect light while increasing the direct sun below.  This
        # produces a brighter day without flattening away contact shadows.
        rawAmb   = _applyColorTemp(_scaleColor(spec['ambient'], intensity * 0.72 * indirectScale))
        ambColor = (
            max(rawAmb[0], floor * 0.58),
            max(rawAmb[1], floor * 0.52),
            max(rawAmb[2], floor * 0.64),
            rawAmb[3],
        )
        amb   = AmbientLight('outdoorAmbient')
        amb.setColor(Vec4(*ambColor))
        ambNp = _lightRig.attachNewNode(amb)
        base.render.setLight(ambNp, 100)
        _renderLights.append(ambNp)

        # Keep this zero-intensity light in the inherited list from the moment
        # the rig is created.  Fading its color at dusk avoids replacing the
        # LightAttrib (and regenerating different shaders) mid-transition.
        nightAmb = AmbientLight('outdoorNightWorldAmbient')
        nightAmb.setColor(Vec4(0, 0, 0, 1.0))
        _nightWorldLightNp = _lightRig.attachNewNode(nightAmb)
        base.render.setLight(_nightWorldLightNp, 100)
        _renderLights.append(_nightWorldLightNp)

    # ── Key / sun ─────────────────────────────────────────────────────────────
    if not enable_key:
        return
    keyGain       = _lerpF(0.92, 0.78, night)
    keyColor      = _applyColorTemp(_scaleColor(spec['key'], intensity * keyGain * directScale))
    key           = DirectionalLight('outdoorKey')
    key.setColor(Vec4(*keyColor))
    casterSpec = _ZONE_PROFILES.get(_activeStyle, spec)
    wants_shadow  = (enable_shadows
                     and _wantWorldShadows()
                     and casterSpec.get('shadowCaster', False))
    _prevShadowCasterState = wants_shadow

    if wants_shadow:
        # Defer shadow-map FBO creation out of the zone-transition frame. The
        # previous zone's rig (and its shadow FBO) was destroyed immediately
        # before this spawn; creating a fresh shadow FBO while the old one is
        # still live in the /Draw render thread permanently wedges
        # graphicsEngine.renderFrame() on this Panda build (the documented
        # double-FBO GL deadlock, seen on street entry). Waiting a beat lets
        # the old FBO retire; the sun itself still lights the scene right away.
        if not _shadowEnablePending:
            _shadowEnablePending = True
            taskMgr.doMethodLater(_SHADOW_CASTER_DEFER_S, _enableShadowCaster,
                                  'outdoorLightingShadowEnable',
                                  extraArgs=[casterSpec])

    # Keep the DirectionalLight direction local-forward and rotate its NodePath
    # exactly once.  This gives the generated shader the correct world direction
    # while also aiming Panda's attached shadow camera.  Applying the world
    # vector internally as well would rotate sunlight twice; leaving the
    # NodePath unrotated instead creates the giant black shadow-frustum wedge.
    try:
        kh, kp, kr = spec.get('keyHpr', (0, -45, 0))
        _sunDirWorld = _sunDirFromHpr((kh, kp, kr))
        key.setDirection(Vec3(0, 1, 0))
    except Exception:
        _sunDirWorld = Vec3(0, 0, -1)
        try:
            key.setDirection(Vec3(0, 1, 0))
        except Exception:
            pass

    keyNp = _lightRig.attachNewNode(key)
    keyNp.setHpr(float(kh), float(kp), float(kr))
    # Keep the key in the inherited light list for the lifetime of the zone.
    # At night its RGB fades to a restrained cool moon accent; removing or
    # re-adding the NodePath here made
    # different flattened DNA batches retain different generated shaders.
    base.render.setLight(keyNp, 100)
    _renderLights.append(keyNp)
    _keyLightNp = keyNp
    _positionShadowCaster()

    # ── Fill ──────────────────────────────────────────────────────────────────
    if enable_fill:
        fillColor = _applyColorTemp(_scaleColor(spec['fill'], intensity * 0.72 * indirectScale))
        # IMPORTANT:
        # A *directional* fill light is view-independent in theory, but in practice
        # Toontown content contains camera-facing cards / special effects / odd
        # transforms that make directional multi-light shading show camera-dependent
        # RGB artifacts (your "RGB vertical surfaces" issue).
        #
        # We therefore implement fill as a *soft ambient* contribution. This preserves
        # the overall mood and lifts shadows without introducing view-dependent hue
        # banding, and also avoids the TransformState/normal-matrix singular crashes
        # observed when adding extra directionals.
        # Keep fill restrained so the stronger sun still casts deep shadows.
        fillColor = _clampChromaRGBA(_desaturateRGBA(fillColor, 0.45), 0.45)
        fill      = AmbientLight('outdoorFillAmbient')
        fill.setColor(Vec4(fillColor[0] * 0.55, fillColor[1] * 0.55, fillColor[2] * 0.55, fillColor[3]))
        fillNp = _lightRig.attachNewNode(fill)
        base.render.setLight(fillNp, 100)
        _renderLights.append(fillNp)

    # ── Rim ───────────────────────────────────────────────────────────────────
    if enable_rim and spec.get('rim'):
        rimColor = _applyColorTemp(_scaleColor(spec['rim'], intensity * 0.75 * indirectScale))
        rimColor = _clampChromaRGBA(_desaturateRGBA(rimColor, 0.45), 0.45)
        rim      = AmbientLight('outdoorRimAmbient')
        rim.setColor(Vec4(rimColor[0] * 0.32, rimColor[1] * 0.32, rimColor[2] * 0.32, rimColor[3]))
        rimNp = _lightRig.attachNewNode(rim)
        base.render.setLight(rimNp, 100)
        _renderLights.append(rimNp)


def _destroyLightRig() -> None:
    global _lightRig, _renderLights, _keyLightNp, _nightWorldLightNp, _keyCastsShadows
    global _shadowFocusPos, _shadowSceneRadius, _shadowMapRes, _shadowFilmArea
    global _lightRigSettingsSignature, _shadowEnablePending
    try:
        taskMgr.remove('outdoorLightingShadowEnable')
    except Exception:
        pass
    _shadowEnablePending = False
    _destroyWorldShadowBuffer()
    _disableLampLights(teardown=True)
    if _keyLightNp and not _keyLightNp.isEmpty():
        try:
            k = _keyLightNp.getNode(0)
            if hasattr(k, 'setShadowCaster') and getattr(k, 'isShadowCaster', lambda: False)():
                _dbg('_destroyLightRig: shadow FBO freed with light (no live setShadowCaster(False))')
        except Exception:
            pass
    for ln in _renderLights:
        try:
            base.render.clearLight(ln)
        except Exception:
            pass
    _renderLights         = []
    _keyLightNp           = None
    _nightWorldLightNp    = None
    _keyCastsShadows      = False
    _shadowFocusPos       = None
    _shadowSceneRadius    = 550.0
    _shadowMapRes         = 1024
    _shadowFilmArea       = 550.0
    _lightRigSettingsSignature = None
    if _lightRig and not _lightRig.isEmpty():
        _lightRig.removeNode()
    _lightRig = None


def _applyProfileLive(spec: dict) -> None:
    global _sunDirWorld
    if not (_lightRig and not _lightRig.isEmpty()):
        return

    intensity     = _intensityScale()
    night         = _nightFactor()
    indirectScale = _lerpF(1.0, 0.75, night)
    directScale   = _lerpF(1.0, 0.28, night)
    floor         = float(spec.get('shadowDarknessFloor', 0.18)) * _lerpF(1.0, 0.75, night)

    for np in _renderLights:
        node = np.getNode(0)
        name = node.getName()

        if name == 'outdoorAmbient':
            raw = _applyColorTemp(_scaleColor(
                spec.get('ambient', (0.2, 0.2, 0.3, 1)), intensity * 0.72 * indirectScale))
            c   = (max(raw[0], floor * 0.58), max(raw[1], floor * 0.52),
                   max(raw[2], floor * 0.64), raw[3])
            node.setColor(Vec4(*c))

        elif name == 'outdoorKey':
            keyGain = _lerpF(0.92, 0.78, night)
            c = _applyColorTemp(_scaleColor(
                spec.get('key', (1, 1, 1, 1)), intensity * keyGain * directScale))
            node.setColor(Vec4(*c))
            kh, kp, kr = spec.get('keyHpr', (0, -45, 0))
            np.setHpr(kh, kp, kr)
            _sunDirWorld = _sunDirFromHpr((kh, kp, kr))
            try:
                node.setDirection(Vec3(0, 1, 0))
            except Exception:
                pass
            try:
                base.render.setLight(np, 100)
            except Exception:
                pass

        elif name == 'outdoorFillAmbient':
            raw = _applyColorTemp(_scaleColor(
                spec.get('fill', (0.5, 0.5, 0.5, 1)), intensity * 0.72 * indirectScale))
            fillColor = _clampChromaRGBA(_desaturateRGBA(raw, 0.70), 0.20)
            node.setColor(Vec4(fillColor[0] * 0.55, fillColor[1] * 0.55, fillColor[2] * 0.55, fillColor[3]))

        elif name == 'outdoorRimAmbient':
            rim = spec.get('rim')
            if rim:
                raw = _applyColorTemp(_scaleColor(rim, intensity * 0.75 * indirectScale))
                rimColor = _clampChromaRGBA(_desaturateRGBA(raw, 0.70), 0.20)
                node.setColor(Vec4(rimColor[0] * 0.32, rimColor[1] * 0.32, rimColor[2] * 0.32, rimColor[3]))

    _syncNightWorldLighting(_activeGeom, night)
    _syncNightWorldTint(_activeGeom, night)
    _syncNightCardTints(_activeGeom, night)
    _updateWorldShadowShaderInputs(spec)

    # Fog live update
    if _fogNode:
        fc  = spec.get('fogColor', (0.5, 0.6, 0.8, 1))
        exp = spec.get('fogExponent')
        dm  = _fogDensityMult()
        _fogNode.setColor(Vec4(*fc))
        if exp is not None:
            _fogNode.setExpDensity(exp * dm)
        elif spec.get('fogFar'):
            near_v = spec.get('fogNear', 80.0)
            far_v  = spec.get('fogFar', 450.0)
            adjusted_near = near_v / dm
            adjusted_far  = far_v  / dm
            _fogNode.setLinearRange(adjusted_near, adjusted_far)

    _tintSky(spec)
    _applyBackgroundColor(spec)

    _updateGodRaysLive(spec)

    # Shadow topology stays stable across the day; only the caster's on/off
    # state tracks the authored day/night keyframes (see
    # _syncShadowCasterWithTimeOfDay).  Full topology rebuilds are reserved for
    # explicit graphics-setting changes; this keeps every flattened DNA batch on
    # one generated shader.


def _syncShadowCasterWithTimeOfDay() -> None:
    """Keep one shadow-map topology alive for the complete zone lifetime.

    The custom receiver fades its direct shadow input to zero at night.  We do
    not destroy/recreate the FBO at sunset and dawn: that old topology switch
    was the source of stale half-world shader variants and transition races.
    """
    global _keyCastsShadows, _shadowEnablePending
    if _keyLightNp is None or _keyLightNp.isEmpty():
        return
    spec = _ZONE_PROFILES.get(_activeStyle, _getActiveSpec())
    wants = bool(spec.get('shadowCaster', False)) and _wantWorldShadows()
    if wants and not _keyCastsShadows:
        if not _shadowEnablePending:
            _shadowEnablePending = True
            _dbg('dawn transition: scheduling world shadow caster enable')
            taskMgr.doMethodLater(_SHADOW_CASTER_DEFER_S, _enableShadowCaster,
                                  'outdoorLightingShadowEnable',
                                  extraArgs=[spec])
    elif (not wants) and _keyCastsShadows:
        _disableShadowCaster()


def _scheduleRigRebuild(spec: dict) -> None:
    global _rigRebuildPending
    _dbg("scheduling light-rig rebuild")
    _rigRebuildPending = spec


def _flushRigRebuild() -> None:
    global _rigRebuildPending, _activeGeom
    if _rigRebuildPending is None:
        return
    try:
        if not _bisectAllows(2):
            _dbg("cancelling rig rebuild: bisect step disables lights")
            _rigRebuildPending = None
            return
    except Exception:
        pass
    spec = _rigRebuildPending
    _rigRebuildPending = None
    savedBounds = None
    if _shadowFocusPos is not None:
        savedBounds = (Vec3(_shadowFocusPos), float(_shadowSceneRadius))
    _destroyLightRig()
    _spawnLightRig(spec, geom=_activeGeom, shadowBounds=savedBounds)
    if _activeGeom is not None and not _activeGeom.isEmpty():
        try:
            _forceLightingOnSubtree(_activeGeom)
            _enableAllShadowCasters(_activeGeom)
            _syncNightWorldLighting(_activeGeom, _nightFactor())
            _syncNightWorldTint(_activeGeom, _nightFactor())
            _syncNightCardTints(_activeGeom, _nightFactor())
        except Exception as e:
            _dbg(f"geometry shader regeneration failed: {e!r}")
    _syncLampLights(force=True)
    lav = getattr(base, 'localAvatar', None)
    if lav is not None and not lav.isEmpty():
        _captureShaderState(lav, avatar=True)
        try:
            lav.clearShader()
        except Exception as e:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Atmospheric fog
# ─────────────────────────────────────────────────────────────────────────────

def _applyFog(spec: dict) -> None:
    global _fogNode
    if not _bisectAllows(5):
        return
    if not _settingsBool('lighting-fog-enabled', True):
        return
    if not _fogEnabled:
        _clearFog()
        return
    fogColor = spec.get('fogColor')
    if not fogColor:
        return
    dm       = _fogDensityMult()
    _fogNode = Fog('outdoorFog')
    _fogNode.setColor(Vec4(*fogColor))
    exponent = spec.get('fogExponent')
    if exponent is not None:
        _fogNode.setExpDensity(exponent * dm)
    else:
        near_v = float(spec.get('fogNear', 80.0))
        far_v  = float(spec.get('fogFar', 450.0))
        _fogNode.setLinearRange(near_v / dm, far_v / dm)
    base.render.setFog(_fogNode)


def _clearFog() -> None:
    global _fogNode
    try:
        base.render.clearFog()
    except Exception:
        pass
    _fogNode = None


# ─────────────────────────────────────────────────────────────────────────────
# Post-process (bloom + 2D sun-ray overlay — no custom scene→texture compositor)
# ─────────────────────────────────────────────────────────────────────────────

def _maintainOutdoorLightingViewport() -> None:
    """Keep the main window's DisplayRegions at full size and pixel_zoom=1.

    Two separate issues produce the same “3D in the bottom-left quarter” look:
    (1) per-DisplayRegion **pixel_zoom** > 1, or (2) normalized **dimensions**
    smaller than the full window on the compositor / 3D region.  Stock
    FilterManager does not always keep those in sync on every GL driver.

    When outdoor FX is active we therefore reset **every** main-window
    DisplayRegion to (0,1)×(0,1) and pixel_zoom 1 each frame.  That can break
    rare sub-rectangle DR tricks (e.g. picture-in-picture) while the rig runs.

    Each operation is individually guarded so that a threading assertion on
    win.setPixelZoom (observed on the custom Panda3D build) cannot abort the
    entire repair and leave the DR dimensions uncorrected.
    """
    win = getattr(base, 'win', None)
    if not win:
        return
    if _postFilterManager is None:
        # Stable CommonFilters/render2dp path only needs its own RTT regions fixed.
        _fixPostProcessRttViewports()
        return

    # Window-level pixel zoom — may raise assertion on some builds; keep isolated.
    try:
        win.setPixelZoom(1)
    except Exception:
        pass

    try:
        main_drs = set()
        if win.getNumDisplayRegions() > 0:
            main_drs.add(win.getDisplayRegion(0))

        for attr in ('dr', 'dr2d', 'dr2dp'):
            d = getattr(base, attr, None)
            if d:
                main_drs.add(d)

        for attr in ('cam', 'cam2d', 'cam2dp'):
            c = getattr(base, attr, None)
            if c and hasattr(c, 'node'):
                cn = c.node()
                if hasattr(cn, 'getNumDisplayRegions'):
                    for i in range(cn.getNumDisplayRegions()):
                        d = cn.getDisplayRegion(i)
                        if d:
                            main_drs.add(d)

        if _postFilterManager is not None:
            r = getattr(_postFilterManager, 'region', None)
            if r:
                main_drs.add(r)
        if _bloomFilters is not None:
            mgr = getattr(_bloomFilters, 'manager', None)
            if mgr:
                r = getattr(mgr, 'region', None)
                if r:
                    main_drs.add(r)

        n = win.getNumDisplayRegions()
        for i in range(n):
            dr = win.getDisplayRegion(i)
            if not dr:
                continue
            is_main = (dr in main_drs)
            if not is_main:
                try:
                    cam = dr.getCamera()
                    if cam:
                        cam_name = cam.getName()
                        if cam_name in ('filter-quad-cam', 'cam', 'cam2d', 'cam2dp', 'camera', 'camera2d', 'camera2dp') or cam in [getattr(base, 'cam', None), getattr(base, 'cam2d', None), getattr(base, 'cam2dp', None)]:
                            is_main = True
                except Exception:
                    pass
            if is_main:
                dr.setDimensions(0.0, 1.0, 0.0, 1.0)
                try:
                    if dr.supportsPixelZoom():
                        dr.setPixelZoom(1)
                except Exception:
                    pass
                try:
                    dr.setScissorEnabled(False)
                except Exception:
                    pass
    except Exception:
        pass

    # RTT buffer DisplayRegions (scene capture buffers, bloom passes, etc.)
    _fixPostProcessRttViewports()


def _loadPostShader() -> Shader | None:
    global _postShader
    if _postShader is not None:
        return _postShader
    try:
        vp_os = os.path.normpath(_POST_VERT)
        fp_os = os.path.normpath(_POST_FRAG)
        if not (os.path.isfile(vp_os) and os.path.isfile(fp_os)):
            return None
        vp = Filename.fromOsSpecific(vp_os)
        fp = Filename.fromOsSpecific(fp_os)
        try:
            vp.makeTrueCase()
            fp.makeTrueCase()
        except Exception:
            pass
        _postShader = Shader.load(Shader.SL_GLSL, vp, fp)
        return _postShader
    except Exception:
        return None


def _setupCinematicPost(spec: dict) -> None:
    """Stable bloom plus additive shafts, without the custom compositor."""
    _setupBloom(spec)
    if _bisectAllows(8):
        _createGodRaysOverlay(spec)


def _setupPostProcess(spec: dict) -> None:
    """Set up cinematic post effects with a stable default path.

    CommonFilters bloom + render2dp sun shafts avoid the driver-sensitive
    scene->texture->fullscreen-quad compositor.  The HDR compositor remains
    available only behind ``lighting-experimental-full-post`` or ``motion-blur``.
    """
    global _postFilterManager, _postQuad, _postColorTex, _postDepthTex, _prevViewProjMat
    _destroyPostProcess()
    _prevViewProjMat = None

    if not _wantFx() or not _supportsBasicShaders():
        return

    if not _settingsBool('lighting-experimental-full-post', False) and not _wantMotionBlur():
        _setupCinematicPost(spec)
        return

    if base is None or getattr(base, 'win', None) is None or getattr(base, 'cam', None) is None:
        return
    shader = _loadPostShader()
    if shader is None:
        _setupCinematicPost(spec)
        return
    try:
        from direct.filter.FilterManager import FilterManager
        _postFilterManager = FilterManager(base.win, base.cam)
        _postColorTex = Texture('scene-post-color')
        _postDepthTex = Texture('scene-post-depth')
        for tex in (_postColorTex, _postDepthTex):
            tex.setWrapU(Texture.WMClamp)
            tex.setWrapV(Texture.WMClamp)
        _postQuad = _postFilterManager.renderSceneInto(colortex=_postColorTex, depthtex=_postDepthTex)
        if _postQuad is None or _postQuad.isEmpty():
            raise RuntimeError('FilterManager did not create a composite quad')
        _postQuad.setShader(shader)
        _postQuad.setShaderInput('sceneColor', _postColorTex)
        _postQuad.setShaderInput('sceneDepth', _postDepthTex)
        _postQuad.setShaderInput('currToPrevMat', _currToPrevMat)
        _postQuad.setShaderInput('motionBlurEnabled', 1.0 if _wantMotionBlur() else 0.0)
        _postQuad.setShaderInput('motionBlurStrength', float(_settingsFloat('motion-blur-strength', 1.0)))
        _postQuad.setShaderInput('mbExcludedBounds', _mbExcludedBounds)
        _postQuad.setShaderInput('mbExcludedDepth', _mbExcludedDepth)
        _postQuad.setShaderInput('mbNumExcluded', _mbNumExcluded)
        ts = _postColorTex.getTexScale()
        _postQuad.setShaderInput('texScale', Vec2(ts[0], ts[1]))
        _fixPostProcessRttViewports()
        _updatePostProcessLive(spec)
    except Exception as error:
        _dbg(f'full post-process unavailable, using stable path: {error!r}')
        _destroyPostProcess()
        _setupCinematicPost(spec)


def _destroyPostProcess() -> None:
    global _postFilterManager, _postQuad, _postColorTex, _postDepthTex
    _destroyBloom()
    _destroyGodRaysOverlay()
    if _postFilterManager is not None:
        try:
            _postFilterManager.cleanup()
        except Exception:
            pass
        _postFilterManager = None
    if _postQuad and not _postQuad.isEmpty():
        try:
            _postQuad.removeNode()
        except Exception:
            pass
    _postQuad = None
    _postColorTex = None
    _postDepthTex = None


def _updatePostProcessLive(spec: dict) -> None:
    if _postQuad is None or _postQuad.isEmpty():
        return
    try:
        if _postColorTex is not None:
            _postQuad.setShaderInput('sceneColor', _postColorTex)
            ts = _postColorTex.getTexScale()
            _postQuad.setShaderInput('texScale', Vec2(ts[0], ts[1]))
            tw = max(1, _postColorTex.getXSize())
            th = max(1, _postColorTex.getYSize())
            texel_size = (1.0 / float(tw), 1.0 / float(th))
        else:
            _postQuad.setShaderInput('texScale', Vec2(1.0, 1.0))
            try:
                props = base.win.getProperties()
                w = max(1, props.getXSize())
                h = max(1, props.getYSize())
                texel_size = (1.0 / float(w), 1.0 / float(h))
            except Exception:
                texel_size = (1.0 / 1280.0, 1.0 / 720.0)

        if _postDepthTex is not None:
            _postQuad.setShaderInput('sceneDepth', _postDepthTex)

        if _wantMotionBlur():
            _postQuad.setShaderInput('motionBlurEnabled', 1.0)
        else:
            _postQuad.setShaderInput('motionBlurEnabled', 0.0)
        _postQuad.setShaderInput('motionBlurStrength', float(_settingsFloat('motion-blur-strength', 1.0)))
        _postQuad.setShaderInput('currToPrevMat', _currToPrevMat)
        _postQuad.setShaderInput('mbExcludedBounds', _mbExcludedBounds)
        _postQuad.setShaderInput('mbExcludedDepth', _mbExcludedDepth)
        _postQuad.setShaderInput('mbNumExcluded', _mbNumExcluded)

        intensity = _intensityScale()
        night = _nightFactor()
        exposure = (float(_settingsFloat('lighting-exposure', 1.0))
                    * float(spec.get('exposure', 1.0))
                    * _lerpF(1.0, 0.72, night))
        # A user can still brighten the scene, but prevent a high authored
        # daylight exposure and a high slider value from multiplying into a
        # multi-stop blowout before tone mapping.
        exposure = _clamp(exposure, 0.35, 1.35)

        tonemap_mode_str = _settingsStr('lighting-tonemap-mode', 'ACES')
        if not _wantTonemap() or tonemap_mode_str.lower() in ('off', 'none', 'disabled'):
            tonemap_val = 2.0
        elif tonemap_mode_str == 'Reinhard':
            tonemap_val = 1.0
        else:
            tonemap_val = 0.0

        bloom_val = (min(float(spec.get('bloomIntensity', 0.0)), 0.32) * intensity
                     if _wantBloom() else 0.0)
        bloom_thresh = max(float(spec.get('bloomThreshold', 0.65)), 0.72)

        ray_val = (float(spec.get('rayIntensity', 0.0)) * intensity * (1.0 - night)
                   if (_bisectAllows(8)
                       and _settingsBool('lighting-god-rays', True))
                   else 0.0)
        ray_color = spec.get('rayColor', (1.0, 1.0, 1.0, 1.0))
        sun_uv = spec.get('sunUV', (0.5, 0.75))

        contact_shadows = 1.0 if _settingsBool('lighting-contact-shadows', True) else 0.0
        cel_val = 1.0 if _settingsBool('lighting-cel-shading', False) else 0.0
        vignette_val = float(_vignetteStrength())

        _postQuad.setShaderInput('exposure', exposure)
        _postQuad.setShaderInput('celShadingMode', cel_val)
        _postQuad.setShaderInput('tonemapMode', tonemap_val)
        _postQuad.setShaderInput('bloomIntensity', bloom_val)
        _postQuad.setShaderInput('bloomThreshold', bloom_thresh)
        _postQuad.setShaderInput('rayIntensity', ray_val)
        _postQuad.setShaderInput('rayColor', Vec4(*ray_color))
        _postQuad.setShaderInput('sunScreenPos', Vec2(*sun_uv))
        _postQuad.setShaderInput('contactShadowsEnabled', contact_shadows)
        _postQuad.setShaderInput('vignetteStrength', vignette_val)
        _postQuad.setShaderInput('time', _godRaysTime)
        _postQuad.setShaderInput('texelSize', Vec2(*texel_size))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Bloom (CommonFilters)
# ─────────────────────────────────────────────────────────────────────────────

def _setupBloom(spec: dict) -> None:
    global _bloomFilters
    wantsBloom = _wantBloom()
    # Cel shading is the master switch for every cartoon shader effect.  Do not
    # leave CommonFilters' ink pass active through a separate/defaulted flag.
    wantsInk = _settingsBool('lighting-cel-shading', False)
    intensity = min(float(spec.get('bloomIntensity', 0.0)), 0.32)
    if not wantsBloom and not wantsInk:
        return
    try:
        from direct.filter.CommonFilters import CommonFilters
        _bloomFilters = CommonFilters(base.win, base.cam)
        if wantsBloom and intensity > 0.001:
            threshold = max(float(spec.get('bloomThreshold', 0.65)), 0.72)
            size = 'large' if intensity >= 0.55 else ('medium' if intensity >= 0.25 else 'small')
            _bloomFilters.setBloom(
                size=size,
                intensity=intensity,
                mintrigger=threshold,
                maxtrigger=min(1.0, threshold + 0.30),
                desat=-0.4,
            )
        if wantsInk:
            _bloomFilters.setCartoonInk(separation=1.2, color=(0.10, 0.08, 0.14, 1.0))
        # CommonFilters uses the base FilterManager.renderSceneInto which calls
        # buffer.makeDisplayRegion() without explicit bounds.  On some GL drivers
        # (including the custom OpenToontown build) that defaults to a bottom-left
        # sub-rectangle, causing the scene to render into only part of the colour
        # texture — the root cause of the "3D view in the bottom-left" bug.
        # Force full-window bounds on every offscreen DR immediately after setup.
        _fixPostProcessRttViewports()
    except Exception:
        _bloomFilters = None


def _destroyBloom() -> None:
    global _bloomFilters
    if _bloomFilters is not None:
        try:
            _bloomFilters.cleanup()
        except Exception:
            pass
        _bloomFilters = None


def _updateBloomLive(spec: dict) -> None:
    if _bloomFilters is None:
        return
    try:
        wantsInk = _settingsBool('lighting-cel-shading', False)
        if wantsInk:
            _bloomFilters.setCartoonInk(separation=1.2, color=(0.10, 0.08, 0.14, 1.0))
        else:
            try:
                _bloomFilters.delCartoonInk()
            except Exception:
                pass
        intensity = min(float(spec.get('bloomIntensity', 0.0)), 0.32) * _intensityScale()
        if not _wantBloom() or intensity <= 0.001:
            _bloomFilters.delBloom()
            return
        threshold = max(float(spec.get('bloomThreshold', 0.65)), 0.72)
        size = 'large' if intensity >= 0.55 else ('medium' if intensity >= 0.25 else 'small')
        _bloomFilters.setBloom(
            size=size, intensity=intensity,
            mintrigger=threshold, maxtrigger=min(1.0, threshold + 0.30),
            desat=-0.4,
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Sun-ray overlay (render2dp)
# ─────────────────────────────────────────────────────────────────────────────

def _loadGodRaysShader() -> Shader | None:
    global _godRaysShader
    if _godRaysShader is not None:
        return _godRaysShader
    try:
        vp_os = os.path.normpath(_LEGACY_VERT)
        fp_os = os.path.normpath(_LEGACY_FRAG)
        if not (os.path.isfile(vp_os) and os.path.isfile(fp_os)):
            return None
        vp = Filename.fromOsSpecific(vp_os)
        fp = Filename.fromOsSpecific(fp_os)
        try:
            vp.makeTrueCase()
            fp.makeTrueCase()
        except Exception:
            pass
        _godRaysShader = Shader.load(Shader.SL_GLSL, vp, fp)
        return _godRaysShader
    except Exception:
        return None


def _createGodRaysOverlay(spec: dict) -> None:
    global _godRaysCard
    if not _bisectAllows(8):
        return
    if not _settingsBool('lighting-god-rays', True):
        return
    rawI = spec.get('rayIntensity', 0.0)
    if rawI <= 0.0:
        return
    shader = _loadGodRaysShader()
    if shader is None:
        return
    try:
        cm   = CardMaker('outdoorGodRays')
        cm.setFrameFullscreenQuad()
        card = base.render2dp.attachNewNode(cm.generate())
        card.setDepthTest(False)
        card.setDepthWrite(False)
        card.setTransparency(TransparencyAttrib.MAlpha)
        card.setAttrib(ColorBlendAttrib.make(
            ColorBlendAttrib.MAdd,
            ColorBlendAttrib.OIncomingAlpha,
            ColorBlendAttrib.OOne,
        ))
        card.setShader(shader)

        su, sv = spec.get('sunUV', (0.5, 0.75))
        rc     = spec.get('rayColor', (1.0, 1.0, 1.0, 1.0))
        # Many zone profiles use very warm/orange rayColor which can turn
        # blown-out highlights into "red sunlight". Prefer a neutral color that
        # matches the actual key light.
        try:
            if _keyLightNp is not None and not _keyLightNp.isEmpty():
                k = _keyLightNp.getNode(0)
                if k and hasattr(k, 'getColor'):
                    kc = k.getColor()
                    # Clamp and slightly desaturate toward white.
                    r = max(0.0, min(1.0, float(kc[0])))
                    g = max(0.0, min(1.0, float(kc[1])))
                    b = max(0.0, min(1.0, float(kc[2])))
                    desat = 0.55
                    lum = (r + g + b) / 3.0
                    r = r * (1.0 - desat) + lum * desat
                    g = g * (1.0 - desat) + lum * desat
                    b = b * (1.0 - desat) + lum * desat
                    rc = (r, g, b, float(rc[3]) if len(rc) >= 4 else 1.0)
        except Exception:
            pass
        eff    = rawI * _intensityScale() * (1.0 - _nightFactor())
        card.setShaderInput('sunPos',       (su, sv))
        card.setShaderInput('rayColor',     Vec4(*rc))
        card.setShaderInput('rayIntensity', eff)
        card.setShaderInput('time',          0.0)
        try:
            props = base.win.getProperties()
            ar    = props.getXSize() / max(1, props.getYSize())
        except Exception:
            ar = 1.333
        card.setShaderInput('aspectRatio', ar)
        _godRaysCard = card
        _updateGodRaysLive(spec)
    except Exception:
        _godRaysCard = None


def _updateGodRaysLive(spec: dict) -> None:
    """Project the actual celestial key into screen space every frame."""
    if _godRaysCard is None or _godRaysCard.isEmpty():
        return
    rawIntensity = float(spec.get('rayIntensity', 0.0) or 0.0)
    intensity = rawIntensity * _intensityScale() * (1.0 - _nightFactor())
    if not _settingsBool('lighting-god-rays', True) or intensity <= 0.001:
        _godRaysCard.hide()
        return

    try:
        cameraNp = getattr(base, 'cam', None)
        if cameraNp is None or cameraNp.isEmpty():
            cameraNp = getattr(base, 'camera', None)
        if cameraNp is None or cameraNp.isEmpty():
            _godRaysCard.hide()
            return

        towardSun = _celestialTowardDirFromHpr(
            tuple(spec.get('keyHpr') or (135, -42, 0)))
        towardSun.normalize()
        cameraWorld = cameraNp.getPos(base.render)
        sunWorld = Point3(cameraWorld + towardSun * 1000.0)
        cameraPoint = cameraNp.getRelativePoint(base.render, sunWorld)
        projected = Point2()
        lens = cameraNp.node().getLens()
        if not lens.project(cameraPoint, projected):
            _godRaysCard.hide()
            return
        u = float(projected.x) * 0.5 + 0.5
        v = float(projected.y) * 0.5 + 0.5
        if u < -0.12 or u > 1.12 or v < -0.12 or v > 1.12:
            _godRaysCard.hide()
            return

        _godRaysCard.setShaderInput('sunPos', Vec2(u, v))
        _godRaysCard.setShaderInput('rayIntensity', intensity)
        try:
            props = base.win.getProperties()
            _godRaysCard.setShaderInput(
                'aspectRatio', float(props.getXSize()) / max(1.0, float(props.getYSize())))
        except Exception:
            pass
        rayColor = tuple(spec.get('rayColor') or (1, 1, 1, 1))
        if _keyLightNp is not None and not _keyLightNp.isEmpty():
            keyColor = _keyLightNp.node().getColor()
            luminance = (float(keyColor.x) + float(keyColor.y) + float(keyColor.z)) / 3.0
            rayColor = (
                float(keyColor.x) * 0.45 + luminance * 0.55,
                float(keyColor.y) * 0.45 + luminance * 0.55,
                float(keyColor.z) * 0.45 + luminance * 0.55,
                rayColor[3] if len(rayColor) > 3 else 1.0,
            )
        _godRaysCard.setShaderInput('rayColor', Vec4(*rayColor))
        _godRaysCard.show()
    except Exception:
        _godRaysCard.hide()


def _destroyGodRaysOverlay() -> None:
    global _godRaysCard
    if _godRaysCard and not _godRaysCard.isEmpty():
        _godRaysCard.removeNode()
    _godRaysCard = None


# ─────────────────────────────────────────────────────────────────────────────
# Procedural sky
# ─────────────────────────────────────────────────────────────────────────────

def _hideLegacySkyNodes(root: NodePath | None = None) -> None:
    if _proceduralSky is None or not _proceduralSky.isActive():
        return
    try:
        hood = _getHood()
        if hood and getattr(hood, 'sky', None) and not hood.sky.isEmpty():
            hood.sky.hide(BitMask32.allOn())
            hood.sky.hide()
    except Exception:
        pass
    
    roots = []
    if getattr(base, 'render', None) and not base.render.isEmpty():
        roots.append(base.render)
    if getattr(base, 'camera', None) and not base.camera.isEmpty() and base.camera not in roots:
        roots.append(base.camera)
    if getattr(base, 'cam', None) and not base.cam.isEmpty() and base.cam not in roots:
        roots.append(base.cam)
    if root is not None and not root.isEmpty() and root not in roots:
        roots.append(root)
    if _activeGeom is not None and not _activeGeom.isEmpty() and _activeGeom not in roots:
        roots.append(_activeGeom)

    sky_keywords = (
        'cog_sky', 'tt_sky', 'dd_sky', 'br_sky', 'mm_sky', 'dg_sky', 'dl_sky',
        'wholesky', 'innergroup', 'middlegroup', 'spooky_sky'
    )
    for r in roots:
        try:
            nodes = r.findAllMatches('**/*')
            for i in range(nodes.getNumPaths()):
                np = nodes.getPath(i)
                if np.isEmpty():
                    continue
                if _proceduralSky and _proceduralSky._skyNp and (np == _proceduralSky._skyNp or _proceduralSky._skyNp.isAncestorOf(np)):
                    continue
                name = np.getName().lower()
                if any(kw in name for kw in sky_keywords) or name == 'sky':
                    try:
                        np.hide(BitMask32.allOn())
                        np.hide()
                    except Exception:
                        pass
        except Exception:
            pass

def _setupProceduralSky(spec: dict) -> None:
    global _proceduralSky
    if _activeStyle in _INDOOR_STYLES or not _wantProceduralSky():
        _destroyProceduralSky()
        return
    try:
        try:
            from toontown.hood.ProceduralSky import ProceduralSky
        except ImportError:
            from ProceduralSky import ProceduralSky
        _proceduralSky = ProceduralSky()
        # Parent to the lens NodePath (base.cam) so the dome shares the same
        # transform as the camera FilterManager renders with; base.camera can sit
        # slightly off the lens on third-person rigs.
        _skyParent = base.cam if (getattr(base, 'cam', None) and not base.cam.isEmpty()) else base.camera
        _proceduralSky.attach(_skyParent, style=_activeStyle)
        if _proceduralSky and _proceduralSky.isActive():
            try:
                _proceduralSky._skyNp.hide(_SHADOW_CAM_MASK)
            except Exception:
                pass
        _proceduralSky.update(spec, _timeOfDay)
        hood = _getHood()
        _hideLegacySkyNodes()
        if hood and getattr(hood, 'sky', None) and not hood.sky.isEmpty():
            # If the procedural sky failed to attach (shader missing, etc),
            # keep the legacy model sky visible as a fallback.
            if _proceduralSky and _proceduralSky.isActive():
                hood.sky.hide(BitMask32.allOn())
            else:
                hood.sky.show(BitMask32.allOn())
    except Exception as e:
        import traceback
        _dbg(f"procedural sky setup failed: {e!r}")
        traceback.print_exc()
        _proceduralSky = None


def _destroyProceduralSky() -> None:
    global _proceduralSky
    if _proceduralSky is not None:
        try:
            _proceduralSky.detach()
        except Exception:
            pass
        _proceduralSky = None
    try:
        hood = _getHood()
        if hood and getattr(hood, 'sky', None) and not hood.sky.isEmpty():
            hood.sky.show(BitMask32.allOn())
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Water reflection system (unchanged from previous version)
# ─────────────────────────────────────────────────────────────────────────────

def _loadWaterShader() -> Shader | None:
    global _waterShader
    if _waterShader is not None:
        return _waterShader
    try:
        vp_os = os.path.normpath(_WATER_VERT)
        fp_os = os.path.normpath(_WATER_FRAG)
        if not (os.path.isfile(vp_os) and os.path.isfile(fp_os)):
            return None
        vp = Filename.fromOsSpecific(vp_os)
        fp = Filename.fromOsSpecific(fp_os)
        try:
            vp.makeTrueCase()
            fp.makeTrueCase()
        except Exception:
            pass
        _waterShader = Shader.load(Shader.SL_GLSL, vp, fp)
        return _waterShader
    except Exception:
        return None


def _isWaterNode(name: str) -> bool:
    nl = name.lower()
    return any(p in nl for p in _WATER_PATTERNS)


def _findWaterNodes(geom) -> list[NodePath]:
    if geom is None or geom.isEmpty():
        return []
    results: list[NodePath] = []
    try:
        all_nodes = geom.findAllMatches('**/*')
        for i in range(all_nodes.getNumPaths()):
            np = all_nodes.getPath(i)
            if _isWaterNode(np.getName()):
                results.append(np)
    except Exception:
        pass
    return results


def _setupWaterNode(waterNp: NodePath, spec: dict) -> dict | None:
    if not _wantWater() or waterNp is None or waterNp.isEmpty():
        return None
    shader = _loadWaterShader()
    if shader is None:
        return None

    quality = str(spec.get('waterReflQuality', 'medium')).strip().lower()
    bufW, bufH = {'high': (1024, 1024), 'low': (256, 256)}.get(quality, (512, 512))
    old_shader_attrib = None
    old_transparency_attrib = None
    had_scale = False
    old_scale = Vec4(1, 1, 1, 1)
    try:
        old_shader_attrib = waterNp.getAttrib(ShaderAttrib.getClassType())
    except Exception:
        pass
    try:
        old_transparency_attrib = waterNp.getAttrib(TransparencyAttrib.getClassType())
    except Exception:
        pass
    try:
        had_scale = bool(waterNp.hasColorScale())
        if had_scale:
            old_scale = Vec4(waterNp.getColorScale())
    except Exception:
        pass

    buf = None
    reflCamNp = None
    taskName = f'waterReflTask_{id(waterNp)}'
    try:
        # Keep the custom shadow FBO isolated from the planar reflection FBO on
        # this Panda build.  Concurrent scene buffers historically made the
        # main view render near-black.  When world shadows are active, keep the water shader
        # (waves / fresnel / sun specular) but bind a flat fallback texture and
        # skip the reflection camera entirely.
        if _worldShadersActive():
            reflTex = Texture('osl_noReflection')
            reflTex.setup2dTexture(1, 1, Texture.TUnsignedByte, Texture.FRgba)
            reflTex.setRamImage(bytes((12, 20, 32, 255)))
            reflectionAvailable = 0.0
        else:
            buf = base.win.makeTextureBuffer(f'waterRefl_{id(waterNp)}', bufW, bufH)
            if not buf:
                return None
            buf.setClearColor(Vec4(0.1, 0.2, 0.3, 1.0))
            reflTex = buf.getTexture()
            reflTex.setWrapU(Texture.WMClamp)
            reflTex.setWrapV(Texture.WMClamp)
            reflectionAvailable = 1.0
            reflCamNp = base.makeCamera(buf)
            reflCamNp.reparentTo(base.render)
            try:
                reflCamNp.node().setCameraMask(_WATER_REFLECTION_CAM_MASK)
                waterNp.hide(_WATER_REFLECTION_CAM_MASK)
            except Exception:
                pass

            waterHeight = waterNp.getZ(base.render)
            try:
                source_lens = base.camNode.getLens()
                if hasattr(source_lens, 'makeCopy'):
                    reflCamNp.node().setLens(source_lens.makeCopy())
            except Exception:
                pass

            def _syncReflCam(task, rnp=reflCamNp, wh=waterHeight):
                try:
                    if rnp.isEmpty() or base.camera.isEmpty():
                        return task.done
                    cp = base.camera.getPos(base.render)
                    ch = base.camera.getHpr(base.render)
                    rnp.setPos(base.render, cp.x, cp.y, 2.0 * wh - cp.z)
                    # Reflection across a horizontal Z-up plane flips pitch and roll.
                    rnp.setHpr(base.render, ch.x, -ch.y, -ch.z)
                except Exception:
                    return task.cont
                return task.cont

            taskMgr.add(_syncReflCam, taskName, sort=44)

        wc = spec.get('waterColor') or (0.22, 0.38, 0.55, 0.85)
        skyColor = spec.get('clearColor') or spec.get('fogColor') or (0.25, 0.45, 0.75, 1.0)
        sdir = Vec3(_sunDirWorld) if _sunDirWorld is not None else Vec3(0, -1, -1)
        try:
            sdir.normalize()
        except Exception:
            pass

        waterNp.clearShader()
        waterNp.setShader(shader)
        waterNp.setShaderInput('osl_ReflectionTex', reflTex)
        waterNp.setShaderInput('osl_WaterColor', Vec4(*wc))
        waterNp.setShaderInput('osl_SunColor', Vec4(1.0, 0.95, 0.80, 1.0) * (1.0 - _nightFactor()))
        waterNp.setShaderInput('osl_SkyReflectionColor', Vec3(skyColor[0], skyColor[1], skyColor[2]))
        waterNp.setShaderInput('osl_ReflectionAvailable', reflectionAvailable)
        waterNp.setShaderInput('osl_SunDir', sdir)
        waterNp.setShaderInput('osl_CameraPos', base.camera.getPos(base.render))
        waterNp.setShaderInput('osl_Time', 0.0)
        waterNp.setShaderInput('osl_WaveScale', float(spec.get('waterWaveScale', 1.0)))
        waterNp.setShaderInput('osl_WaveSpeed', float(spec.get('waterWaveSpeed', 0.8)))
        waterNp.setShaderInput('osl_FresnelPower', float(spec.get('waterFresnelPower', 4.0)))
        waterNp.setShaderInput('osl_Roughness', float(spec.get('waterRoughness', 0.28)))
        waterNp.setShaderInput('osl_ReflectionStrength', float(spec.get('waterReflectionStrength', 0.72)))
        waterNp.setTransparency(TransparencyAttrib.MAlpha)

        return {
            'np': waterNp, 'buffer': buf, 'camera': reflCamNp, 'taskName': taskName,
            'oldShaderAttrib': old_shader_attrib, 'oldTransparencyAttrib': old_transparency_attrib,
            'hadColorScale': had_scale, 'oldColorScale': old_scale,
        }
    except Exception as error:
        _dbg(f'water reflection setup failed for {waterNp.getName()!r}: {error!r}')
        try:
            taskMgr.remove(taskName)
        except Exception:
            pass
        try:
            if reflCamNp and not reflCamNp.isEmpty():
                reflCamNp.removeNode()
        except Exception:
            pass
        try:
            if buf:
                base.graphicsEngine.removeWindow(buf)
        except Exception:
            pass
        try:
            waterNp.show(_WATER_REFLECTION_CAM_MASK)
        except Exception:
            pass
        return None


def _setupAllWater(geom, spec: dict) -> None:
    global _waterSetups
    if not spec.get('hasWater', False) or not _wantWater():
        return
    for np in _findWaterNodes(geom):
        setup = _setupWaterNode(np, spec)
        if setup:
            _waterSetups.append(setup)
        try:
            np.hide(_SHADOW_CAM_MASK)
        except Exception:
            pass


def _cleanupAllWater() -> None:
    global _waterSetups, _waterShader
    for ws in _waterSetups:
        try:
            taskMgr.remove(ws['taskName'])
        except Exception:
            pass
        try:
            cam = ws.get('camera')
            if cam and not cam.isEmpty():
                cam.removeNode()
        except Exception:
            pass
        try:
            buf = ws.get('buffer')
            if buf:
                base.graphicsEngine.removeWindow(buf)
        except Exception:
            pass
        try:
            np = ws.get('np')
            if np and not np.isEmpty():
                np.show(_WATER_REFLECTION_CAM_MASK)
                np.clearShader()
                old_attrib = ws.get('oldShaderAttrib')
                if old_attrib is not None:
                    np.setAttrib(old_attrib)
                old_transparency = ws.get('oldTransparencyAttrib')
                if old_transparency is not None:
                    np.setAttrib(old_transparency)
                else:
                    np.clearAttrib(TransparencyAttrib.getClassType())
                if ws.get('hadColorScale'):
                    np.setColorScale(ws.get('oldColorScale', Vec4(1, 1, 1, 1)))
                else:
                    np.clearColorScale()
        except Exception:
            pass
    _waterSetups = []
    _waterShader = None


def _tickWaterUniforms(dt: float) -> None:
    if not _waterSetups:
        return
    camPos = None
    sunDir = Vec3(0, -1, -1)
    try:
        camPos = base.camera.getPos(base.render)
    except Exception:
        pass
    if _sunDirWorld is not None:
        try:
            sunDir = Vec3(_sunDirWorld)
            sunDir.normalize()
        except Exception:
            pass
    elif _keyLightNp and not _keyLightNp.isEmpty():
        try:
            sunDir = _keyLightNp.getQuat(base.render).getForward()
            sunDir.normalize()
        except Exception:
            pass
    spec = _getActiveSpec()
    night = _nightFactor()
    rawSunColor = _applyColorTemp(_scaleColor(
        spec.get('key') or (1.0, 0.95, 0.80, 1.0),
        _lerpF(1.0, 0.24, night)))
    skyColor = spec.get('clearColor') or spec.get('fogColor') or (0.25, 0.45, 0.75, 1.0)
    for ws in _waterSetups:
        np = ws.get('np')
        if not np or np.isEmpty():
            continue
        try:
            np.setShaderInput('osl_Time', _godRaysTime)
            if camPos is not None:
                np.setShaderInput('osl_CameraPos', camPos)
            np.setShaderInput('osl_SunDir', sunDir)
            np.setShaderInput('osl_SunColor', Vec4(*rawSunColor))
            np.setShaderInput('osl_SkyReflectionColor',
                              Vec3(skyColor[0], skyColor[1], skyColor[2]))
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Per-frame update task
# ─────────────────────────────────────────────────────────────────────────────

def _lightingUpdateTask(task):
    global _godRaysTime, _timeOfDay, _dayNightAccum, _lampRefreshAccum, _lampGeomNps

    # This module was supplied with very verbose crash instrumentation. Keep it
    # available behind lighting-debug without flooding normal client logs every
    # rendered frame.
    import builtins as _builtins_ol
    print = _builtins_ol.print if _debugEnabled() else (lambda *args, **kwargs: None)

    # Teleport/zone transitions can temporarily tear down the scene graph in ways
    # that make TransformState matrices non-invertible, which can crash inside
    # Panda3D when we query transforms. We should *not* permanently stop this task
    # on a transient bad frame, because that can leave the shadow caster frozen
    # and make the whole world look incorrectly shadowed. Instead, keep the task
    # alive and just skip frames that hit assertions.
    import sys as _sys_ol
    if _debugEnabled():
        _sys_ol.stderr.write('[CRASH-DIAG] _lightingUpdateTask: tick start\n'); _sys_ol.stderr.flush()
    global _prevViewProjMat, _currToPrevMat, _mbNumExcluded
    if _postQuad is not None and not _postQuad.isEmpty():
        if _wantMotionBlur() and base is not None and getattr(base, 'cam', None) and not base.cam.isEmpty() and getattr(base, 'camLens', None):
            try:
                viewMat = base.render.getMat(base.cam)
                projMat = base.camLens.getProjectionMat()
                currVP = viewMat * projMat
                if _prevViewProjMat is not None:
                    currVP_inv = LMatrix4(currVP)
                    currVP_inv.invertInPlace()
                    _currToPrevMat = currVP_inv * _prevViewProjMat
                    _postQuad.setShaderInput('currToPrevMat', _currToPrevMat)
                else:
                    _currToPrevMat = LMatrix4.identMat()
                    _postQuad.setShaderInput('currToPrevMat', _currToPrevMat)
                _prevViewProjMat = LMatrix4(currVP)
                _postQuad.setShaderInput('motionBlurEnabled', 1.0)
                _postQuad.setShaderInput('motionBlurStrength', float(_settingsFloat('motion-blur-strength', 1.0)))

                # Collect exclusion bounds for localAvatar and visible nametags
                count = 0
                lav = getattr(base, 'localAvatar', None)
                if lav is not None and not lav.isEmpty():
                    lav_bounds = _computeScreenBounds(lav, base.cam, projMat)
                    if lav_bounds is not None:
                        _mbExcludedBounds[count] = LVecBase4f(lav_bounds[0])
                        _mbExcludedDepth[count] = LVecBase2f(lav_bounds[1])
                        count += 1

                try:
                    if hasattr(base, 'render') and base.render is not None and not base.render.isEmpty():
                        nametag_nodes = base.render.findAllMatches('**/nametag3d')
                        for i in range(min(15, nametag_nodes.getNumPaths())):
                            if count >= 16:
                                break
                            nt = nametag_nodes.getPath(i)
                            if nt is not None and not nt.isEmpty():
                                nt_bounds = _computeScreenBounds(nt, base.cam, projMat)
                                if nt_bounds is not None:
                                    _mbExcludedBounds[count] = LVecBase4f(nt_bounds[0])
                                    _mbExcludedDepth[count] = LVecBase2f(nt_bounds[1])
                                    count += 1
                except Exception:
                    pass

                _mbNumExcluded = count
                _postQuad.setShaderInput('mbExcludedBounds', _mbExcludedBounds)
                _postQuad.setShaderInput('mbExcludedDepth', _mbExcludedDepth)
                _postQuad.setShaderInput('mbNumExcluded', _mbNumExcluded)
            except Exception:
                _currToPrevMat = LMatrix4.identMat()
                _postQuad.setShaderInput('currToPrevMat', _currToPrevMat)
                _postQuad.setShaderInput('motionBlurEnabled', 0.0)
        else:
            _currToPrevMat = LMatrix4.identMat()
            _postQuad.setShaderInput('currToPrevMat', _currToPrevMat)
            _postQuad.setShaderInput('motionBlurEnabled', 0.0)
    if _bisectStep() <= 7:
        if _refCount <= 0:
            return task.done
        dt = globalClock.getDt()
        if _wantDayNight():
            spec = _ZONE_PROFILES.get(_activeStyle, _ZONE_PROFILES['playground'])
            if spec.get('dayNightEnabled', True):
                _dayNightAccum += dt
                if _dayNightAccum >= 0.10:
                    mode = _settingsStr('day-night-mode', 'Dynamic')
                    if mode == 'Real-Time Sync':
                        import time as _py_time
                        lt = _py_time.localtime()
                        _timeOfDay = lt.tm_hour + lt.tm_min / 60.0 + lt.tm_sec / 3600.0
                    elif mode == 'Always Noon':
                        _timeOfDay = 12.0
                    elif mode == 'Always Sunset':
                        _timeOfDay = 18.0
                    elif mode == 'Always Midnight':
                        _timeOfDay = 0.0
                    elif mode == 'Always Dawn':
                        _timeOfDay = 6.0
                    else:
                        dayDur = max(0.1, _settingsFloat('day-duration-minutes', 10.0))
                        nightDur = max(0.1, _settingsFloat('night-duration-minutes', 5.0))
                        dayRate = 12.0 / (dayDur * 60.0)
                        nightRate = 12.0 / (nightDur * 60.0)
                        h = _timeOfDay
                        if 7.0 <= h <= 17.0:
                            rate = dayRate
                        elif h >= 19.0 or h <= 5.0:
                            rate = nightRate
                        elif 5.0 < h < 7.0:
                            t = (h - 5.0) / 2.0
                            rate = nightRate * (1.0 - t) + dayRate * t
                        else:
                            t = (h - 17.0) / 2.0
                            rate = dayRate * (1.0 - t) + nightRate * t
                        gameHours = (_dayNightAccum * rate * _dayNightSpeedMultiplier())
                        _timeOfDay = (_timeOfDay + gameHours) % 24.0
                    _dayNightAccum = 0.0
                    if _bisectStep() >= 5:
                        if _proceduralSky and _proceduralSky.isActive():
                            cur_spec = _getActiveSpec()
                            _proceduralSky.update(cur_spec, _timeOfDay)
                    if _bisectStep() >= 7:
                        cur_spec = _getActiveSpec()
                        _updateBloomLive(cur_spec)
                        _updatePostProcessLive(cur_spec)
        if _refCount > 0 and _activeGeom is not None and not _activeGeom.isEmpty():
            _syncNightWorldLighting(_activeGeom, _nightFactor())
            _syncNightWorldTint(_activeGeom, _nightFactor())
        if _bisectAllows(4):
            _syncShadowCasterWithTimeOfDay()
        if _bisectStep() >= 6:
            if _proceduralSky and _proceduralSky.isActive():
                cur_spec = _getActiveSpec()
                _proceduralSky.update(cur_spec, _timeOfDay)
        if _bisectStep() >= 7:
            cur_spec = _getActiveSpec()
            _updateBloomLive(cur_spec)
            _updatePostProcessLive(cur_spec)
        if _bisectStep() >= 3:
            _lampRefreshAccum += dt
            if _lampRefreshAccum >= _LAMP_REFRESH_SECONDS:
                _lampRefreshAccum = 0.0
                if not _lampGeomNps and _activeGeom:
                    _lampGeomNps = _scanForLampNodes(_activeGeom)
                if _lampGeomNps:
                    try:
                        _syncLampLights()
                    except Exception:
                        pass
            _syncAvatarNightTint(_nightFactor())
        _updateWorldShadowShaderInputs(_getActiveSpec())
        return task.cont

    try:
        _syncBase()
        if base is None or getattr(base, 'render', None) is None:
            print(f"[DEBUG VideoSettings] _lightingUpdateTask: base or base.render is None, returning task.done.", flush=True)
            return task.done
        if _refCount <= 0 or _lightRig is None or (_lightRig and _lightRig.isEmpty()):
            print(f"[DEBUG VideoSettings] _lightingUpdateTask: refCount={_refCount} or lightRig is None/empty, returning task.done.", flush=True)
            return task.done
    except Exception as e:
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: exception in initial checks: {e!r}, returning task.done.")
        return task.done

    dt            = globalClock.getDt()
    _godRaysTime += dt
    _lampRefreshAccum += dt

    if _refCount > 0:
        if _debugEnabled():
            _sys_ol.stderr.write('[CRASH-DIAG] _lightingUpdateTask: maintainViewport\n'); _sys_ol.stderr.flush()
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: calling _maintainOutdoorLightingViewport...", flush=True)
        _maintainOutdoorLightingViewport()
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: _maintainOutdoorLightingViewport returned.", flush=True)

    # ── Shadow camera positioning ─────────────────────────────────────────────
    try:
        if _debugEnabled():
            _sys_ol.stderr.write('[CRASH-DIAG] _lightingUpdateTask: positionShadowCaster\n'); _sys_ol.stderr.flush()
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: calling _positionShadowCaster...", flush=True)
        _positionShadowCaster()
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: _positionShadowCaster returned.", flush=True)
    except AssertionError as ae:
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: _positionShadowCaster assertion error: {ae!r}")
        return task.cont
    except Exception as e:
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: _positionShadowCaster exception: {e!r}")
        return task.cont

    # ── Water uniforms ────────────────────────────────────────────────────────
    try:
        if _debugEnabled():
            _sys_ol.stderr.write('[CRASH-DIAG] _lightingUpdateTask: tickWaterUniforms\n'); _sys_ol.stderr.flush()
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: calling _tickWaterUniforms...", flush=True)
        _tickWaterUniforms(dt)
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: _tickWaterUniforms returned.", flush=True)
    except AssertionError as ae:
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: _tickWaterUniforms assertion error: {ae!r}")
        return task.cont
    except Exception as e:
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: _tickWaterUniforms exception: {e!r}")
        pass

    # ── Procedural sky update ─────────────────────────────────────────────────
    try:
        if _debugEnabled():
            _sys_ol.stderr.write('[CRASH-DIAG] _lightingUpdateTask: proceduralSky\n'); _sys_ol.stderr.flush()
        if _proceduralSky and _proceduralSky.isActive():
            print(f"[DEBUG VideoSettings] _lightingUpdateTask: calling procedural sky update...", flush=True)
            spec = _getActiveSpec()
            _proceduralSky.update(spec, _timeOfDay)
            print(f"[DEBUG VideoSettings] _lightingUpdateTask: procedural sky update returned.")
            _hideLegacySkyNodes()
    except AssertionError as ae:
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: procedural sky update assertion error: {ae!r}")
        return task.cont
    except Exception as e:
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: procedural sky update exception: {e!r}")
        pass

    # ── Day / night cycle ─────────────────────────────────────────────────────
    if _debugEnabled():
        _sys_ol.stderr.write('[CRASH-DIAG] _lightingUpdateTask: dayNight\n'); _sys_ol.stderr.flush()
    if _wantDayNight() and _refCount > 0:
        spec = _ZONE_PROFILES.get(_activeStyle, _ZONE_PROFILES['playground'])
        if spec.get('dayNightEnabled', True):
            _dayNightAccum += dt
            if _dayNightAccum >= 0.10:
                mode = _settingsStr('day-night-mode', 'Dynamic')
                if mode == 'Real-Time Sync':
                    import time as _py_time
                    lt = _py_time.localtime()
                    _timeOfDay = lt.tm_hour + lt.tm_min / 60.0 + lt.tm_sec / 3600.0
                elif mode == 'Always Noon':
                    _timeOfDay = 12.0
                elif mode == 'Always Sunset':
                    _timeOfDay = 18.0
                elif mode == 'Always Midnight':
                    _timeOfDay = 0.0
                elif mode == 'Always Dawn':
                    _timeOfDay = 6.0
                else:
                    dayDur = max(0.1, _settingsFloat('day-duration-minutes', 10.0))
                    nightDur = max(0.1, _settingsFloat('night-duration-minutes', 5.0))
                    dayRate = 12.0 / (dayDur * 60.0)
                    nightRate = 12.0 / (nightDur * 60.0)
                    h = _timeOfDay
                    if 7.0 <= h <= 17.0:
                        rate = dayRate
                    elif h >= 19.0 or h <= 5.0:
                        rate = nightRate
                    elif 5.0 < h < 7.0:
                        t = (h - 5.0) / 2.0
                        rate = nightRate * (1.0 - t) + dayRate * t
                    else:
                        t = (h - 17.0) / 2.0
                        rate = dayRate * (1.0 - t) + nightRate * t
                    gameHours = (_dayNightAccum * rate * _dayNightSpeedMultiplier())
                    _timeOfDay = (_timeOfDay + gameHours) % 24.0
                _dayNightAccum = 0.0
                cur_spec = _getActiveSpec()
                _applyProfileLive(cur_spec)
                _updateBloomLive(cur_spec)
                _updatePostProcessLive(cur_spec)
                try:
                    if _proceduralSky and _proceduralSky.isActive():
                        _proceduralSky.update(cur_spec, _timeOfDay)
                except Exception:
                    pass

    if _refCount > 0 and _activeGeom is not None and not _activeGeom.isEmpty():
        _syncNightWorldLighting(_activeGeom, _nightFactor())
        _syncNightWorldTint(_activeGeom, _nightFactor())

    if _bisectAllows(4):
        _syncShadowCasterWithTimeOfDay()

    if _lampRefreshAccum >= _LAMP_REFRESH_SECONDS:
        _lampRefreshAccum = 0.0
        if not _lampGeomNps and _activeGeom:
            _lampGeomNps = _scanForLampNodes(_activeGeom)
        if _lampGeomNps:
            try:
                _syncLampLights()
            except Exception:
                pass

    _syncAvatarNightTint(_nightFactor())
    _updateWorldShadowShaderInputs(_getActiveSpec())

    # ── Sun-ray overlay shimmer ───────────────────────────────────────────────
    if _debugEnabled():
        _sys_ol.stderr.write('[CRASH-DIAG] _lightingUpdateTask: godRays\n'); _sys_ol.stderr.flush()
    if _godRaysCard and not _godRaysCard.isEmpty():
        _godRaysCard.setShaderInput('time', _godRaysTime)
        _updateGodRaysLive(_getActiveSpec())

    _updatePostProcessLive(_getActiveSpec())

    # ── Deferred rig rebuild ──────────────────────────────────────────────────
    if _debugEnabled():
        _sys_ol.stderr.write('[CRASH-DIAG] _lightingUpdateTask: flushRigRebuild\n'); _sys_ol.stderr.flush()
    try:
        if _rigRebuildPending is not None:
            print(f"[DEBUG VideoSettings] _lightingUpdateTask: calling _flushRigRebuild...", flush=True)
        _flushRigRebuild()
    except AssertionError as ae:
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: _flushRigRebuild assertion error: {ae!r}")
        return task.cont
    except Exception as e:
        print(f"[DEBUG VideoSettings] _lightingUpdateTask: _flushRigRebuild exception: {e!r}")
        pass

    if _debugEnabled():
        _sys_ol.stderr.write('[CRASH-DIAG] _lightingUpdateTask: tick end\n'); _sys_ol.stderr.flush()
    print(f"[DEBUG VideoSettings] _lightingUpdateTask: tick end.", flush=True)
    return task.cont


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def _getWorldShadowWhiteTexture() -> Texture:
    global _worldShadowWhiteTex
    if _worldShadowWhiteTex is None:
        tex = Texture('outdoorShadowWhiteFallback')
        tex.setup2dTexture(1, 1, Texture.TUnsignedByte, Texture.FRgba)
        tex.setRamImage(bytes((255, 255, 255, 255)))
        _worldShadowWhiteTex = tex
    return _worldShadowWhiteTex


def _loadWorldShadowShader() -> Shader | None:
    global _worldShadowShader
    if _worldShadowShader is not None:
        return _worldShadowShader
    try:
        shader = Shader.load(
            Shader.SL_GLSL,
            Filename.fromOsSpecific(_WORLD_SHADOW_VERT),
            Filename.fromOsSpecific(_WORLD_SHADOW_FRAG),
        )
        if shader is not None:
            _worldShadowShader = shader
        return shader
    except Exception as error:
        _dbg(f'custom world-shadow shader unavailable: {error!r}')
        return None


def _clearShadersForCustomReceiver(root: NodePath) -> None:
    """Remove local shader overrides before installing one inherited shader."""
    try:
        nodes = root.findAllMatches('**;+s')
        for i in range(nodes.getNumPaths()):
            try:
                nodes.getPath(i).clearAttrib(ShaderAttrib.getClassType())
            except Exception:
                pass
    except Exception:
        pass


def _worldShadowAmbient(spec: dict, night: float) -> Vec3:
    """Build restrained indirect light; lamp light is added separately."""
    intensity = _intensityScale()
    indirect = _lerpF(1.0, 0.75, night)
    floor = float(spec.get('shadowDarknessFloor', 0.18)) * _lerpF(1.0, 0.75, night)
    raw = _applyColorTemp(_scaleColor(
        spec.get('ambient', (0.2, 0.2, 0.3, 1.0)), intensity * 0.72 * indirect))
    result = Vec3(max(raw[0], floor * 0.58),
                  max(raw[1], floor * 0.52),
                  max(raw[2], floor * 0.64))
    fill = spec.get('fill')
    if fill:
        fc = _clampChromaRGBA(_desaturateRGBA(
            _applyColorTemp(_scaleColor(fill, intensity * 0.72 * indirect)), 0.70), 0.20)
        result += Vec3(fc[0], fc[1], fc[2]) * 0.32
    rim = spec.get('rim')
    if rim:
        rc = _clampChromaRGBA(_desaturateRGBA(
            _applyColorTemp(_scaleColor(rim, intensity * 0.75 * indirect)), 0.70), 0.20)
        result += Vec3(rc[0], rc[1], rc[2]) * 0.16
    if night > _NIGHT_LIGHTING_THRESHOLD:
        blend = _smootherstep01((night - _NIGHT_LIGHTING_THRESHOLD) /
                                (1.0 - _NIGHT_LIGHTING_THRESHOLD))
        result += Vec3(0.28, 0.30, 0.42) * blend
    return result


def _updateWorldShadowShaderInputs(spec: dict | None = None) -> None:
    """Refresh camera-relative light, lamp, and shadow-map inputs in one state."""
    root = _activeGeom
    if not _worldShadersActive():
        return
    # Streamed instances (Country Clubs and Factories) intentionally begin with
    # geom=None.  Bind their shared ABI on render so their explicit receiver
    # roots inherit every required input, including osl_Ambient.
    if root is None or root.isEmpty():
        root = base.render
    spec = spec or _getActiveSpec()
    night = _nightFactor()
    intensity = _intensityScale()
    directScale = _lerpF(1.0, 0.28, night)

    towardSun = Vec3(_sunDirWorld) * -1.0 if _sunDirWorld is not None else Vec3(0, 0, 1)
    try:
        towardSun.normalize()
    except Exception:
        towardSun = Vec3(0, 0, 1)
    keyColor = _applyColorTemp(_scaleColor(
        spec.get('key', (1, 1, 1, 1)), intensity * _lerpF(0.92, 0.78, night) * directScale))
    grade = _nightWorldGrade(night)
    ambient = _worldShadowAmbient(spec, night)
    skyAmbient = Vec3(ambient.x * 1.10, ambient.y * 1.14, ambient.z * 1.22)
    groundAmbient = Vec3(ambient.x * 0.58, ambient.y * 0.54, ambient.z * 0.50)
    cameraPos = Vec3(0, 0, 0)
    try:
        cameraNp = getattr(base, 'cam', None)
        if cameraNp is None or cameraNp.isEmpty():
            cameraNp = getattr(base, 'camera', None)
        if cameraNp is not None and not cameraNp.isEmpty():
            cameraPos = cameraNp.getPos(base.render)
    except Exception:
        pass
    specularStrength = 0.18 if _wantSpecularHighlights() else 0.0
    fogColor = spec.get('fogColor') or (0.5, 0.6, 0.8, 1.0)
    fogMode = 0.0
    fogParams = Vec3(0.0, 1.0, 0.0)
    if (_bisectAllows(5) and _settingsBool('lighting-fog-enabled', True)
            and spec.get('fogColor') is not None):
        densityMult = _fogDensityMult()
        exponent = spec.get('fogExponent')
        if exponent is not None:
            fogMode = 2.0
            fogParams = Vec3(0.0, 1.0, max(0.000001, float(exponent) * densityMult))
        else:
            fogMode = 1.0
            fogNear = float(spec.get('fogNear', 80.0)) / densityMult
            fogFar = max(fogNear + 1.0, float(spec.get('fogFar', 450.0)) / densityMult)
            fogParams = Vec3(fogNear, fogFar, 0.0)
    root.setShaderInput('osl_SunDirWorld', towardSun)
    root.setShaderInput('osl_SunColor', Vec3(keyColor[0], keyColor[1], keyColor[2]))
    root.setShaderInput('osl_Ambient', ambient)
    root.setShaderInput('osl_SkyAmbient', skyAmbient)
    root.setShaderInput('osl_GroundAmbient', groundAmbient)
    root.setShaderInput('osl_WorldGrade', Vec3(grade.x, grade.y, grade.z))
    root.setShaderInput('osl_CameraPosWorld', cameraPos)
    root.setShaderInput('osl_SpecularStrength', specularStrength)
    root.setShaderInput('osl_FogColor', Vec3(fogColor[0], fogColor[1], fogColor[2]))
    root.setShaderInput('osl_FogParams', fogParams)
    root.setShaderInput('osl_FogMode', fogMode)
    shadowFloor = _clamp(float(spec.get('shadowDarknessFloor', 0.18)) * 0.38,
                         0.035, 0.16)
    celShading = 1.0 if _settingsBool('lighting-cel-shading', False) else 0.0
    root.setShaderInput('osl_ShadowFloor', shadowFloor)
    root.setShaderInput('osl_CelShadingMode', celShading)
    root.setShaderInput('osl_DebugMode', 0.0)
    # Mirror the ABI at render so separately attached outdoor subtrees can use
    # the same receiver without creating another shader/light variant.
    base.render.setShaderInput('osl_SunDirWorld', towardSun)
    base.render.setShaderInput('osl_SunColor', Vec3(keyColor[0], keyColor[1], keyColor[2]))
    base.render.setShaderInput('osl_Ambient', ambient)
    base.render.setShaderInput('osl_SkyAmbient', skyAmbient)
    base.render.setShaderInput('osl_GroundAmbient', groundAmbient)
    base.render.setShaderInput('osl_WorldGrade', Vec3(grade.x, grade.y, grade.z))
    base.render.setShaderInput('osl_CameraPosWorld', cameraPos)
    base.render.setShaderInput('osl_SpecularStrength', specularStrength)
    base.render.setShaderInput('osl_FogColor', Vec3(fogColor[0], fogColor[1], fogColor[2]))
    base.render.setShaderInput('osl_FogParams', fogParams)
    base.render.setShaderInput('osl_FogMode', fogMode)
    base.render.setShaderInput('osl_ShadowFloor', shadowFloor)
    base.render.setShaderInput('osl_CelShadingMode', celShading)
    base.render.setShaderInput('osl_DebugMode', 0.0)

    pointShadowsOn = 1.0 if _settingsBool('lighting-pointlight-shadows', True) else 0.0
    root.setShaderInput('osl_PointShadowOn', pointShadowsOn)
    base.render.setShaderInput('osl_PointShadowOn', pointShadowsOn)

    for idx in range(_MAX_SHADER_LAMP_LIGHTS):
        pos = Vec3(0, 0, 100000)
        groundPos = Vec3(0, 0, 100000)
        color = Vec3(0, 0, 0)
        if idx < len(_lampLights):
            lamp = _lampLights[idx]
            try:
                pos = lamp.getPos(base.render)
                c = lamp.node().getColor()
                color = Vec3(c.x, c.y, c.z)
            except Exception:
                pass
            if idx < len(_lampGeomNps):
                try:
                    groundPos = _lampGroundPosition(_lampGeomNps[idx])
                except Exception:
                    groundPos = Vec3(pos.x, pos.y, pos.z - 14.0)
            else:
                groundPos = Vec3(pos.x, pos.y, pos.z - 14.0)
        root.setShaderInput(f'osl_LampPosWorld{idx}', pos)
        root.setShaderInput(f'osl_LampGroundPos{idx}', groundPos)
        root.setShaderInput(f'osl_LampColor{idx}', color)
        base.render.setShaderInput(f'osl_LampPosWorld{idx}', pos)
        base.render.setShaderInput(f'osl_LampGroundPos{idx}', groundPos)
        base.render.setShaderInput(f'osl_LampColor{idx}', color)

    shadowOn = 0.0
    shadowTex = _getWorldShadowWhiteTexture()
    shadowMatrix = LMatrix4.identMat()
    texel = Vec2(1.0, 1.0)
    qualityRadius = {'low': 0.48, 'medium': 0.62, 'high': 0.78}.get(
        _shadowQuality(), 0.78)
    filterRadius = qualityRadius * _clamp(
        _settingsFloat('lighting-shadow-softness', 1.0), 0.35, 1.35)
    shadowBias = 0.00070
    try:
        if (_keyCastsShadows and _worldShadowBuffer is not None
                and _worldShadowCameraNp is not None
                and not _worldShadowCameraNp.isEmpty()
                and _worldShadowMap is not None):
            shadowTex = _worldShadowMap
            shadowCam = _worldShadowCameraNp
            lens = shadowCam.node().getLens()
            projection = LMatrix4(lens.getProjectionMat())
            worldToLight = LMatrix4(base.render.getMat(shadowCam))
            # The receiver emits Panda world-space coordinates.  Composing in
            # Panda's native coordinate system avoids the renderer-specific eye
            # coordinate conversion that previously offset receiver depth.
            shadowMatrix = worldToLight * projection
            width = max(1, _shadowMapRes)
            height = max(1, _shadowMapRes)
            texel = Vec2(1.0 / float(width), 1.0 / float(height))
            farClip = max(500.0, float(_shadowFollowDist * 2.0 + 800.0))
            worldTexel = float(_shadowFilmArea) / float(width)
            shadowBias = _clamp(worldTexel / farClip * 2.4, 0.00035, 0.00120)
            # Fade before full night.  The texture/FBO remains bound, so
            # switching time modes never changes geometry shader topology.
            shadowOn = _smootherstep01((0.22 - night) / 0.18)
    except Exception as error:
        _dbg(f'world-shadow input update skipped: {error!r}')
        shadowOn = 0.0
    root.setShaderInput('osl_ShadowMatrix', shadowMatrix)
    root.setShaderInput('osl_ShadowMap', shadowTex)
    root.setShaderInput('osl_ShadowTexel', texel)
    root.setShaderInput('osl_ShadowFilterRadius', filterRadius)
    root.setShaderInput('osl_ShadowBias', shadowBias)
    root.setShaderInput('osl_ShadowOn', shadowOn)
    base.render.setShaderInput('osl_ShadowMatrix', shadowMatrix)
    base.render.setShaderInput('osl_ShadowMap', shadowTex)
    base.render.setShaderInput('osl_ShadowTexel', texel)
    base.render.setShaderInput('osl_ShadowFilterRadius', filterRadius)
    base.render.setShaderInput('osl_ShadowBias', shadowBias)
    base.render.setShaderInput('osl_ShadowOn', shadowOn)


def _applyWorldShaderState(root: NodePath) -> bool:
    """Install one custom receiver shader for the complete zone lifetime."""
    global _worldShadowTextureState
    if root is None or root.isEmpty() or root == getattr(base, 'render', None):
        return False
    use_shaders = _worldShadersActive()
    if use_shaders:
        shader = _loadWorldShadowShader()
        if shader is None:
            root.clearShader()
            return False
        try:
            preprocessGeometry(root)
        except Exception:
            pass
        _clearShadersForCustomReceiver(root)
        if _worldShadowTextureState is None or _worldShadowTextureState[0] != root:
            try:
                _worldShadowTextureState = (root, root.getAttrib(TextureAttrib.getClassType()))
            except Exception:
                _worldShadowTextureState = (root, None)
        root.setTexture(TextureStage.getDefault(), _getWorldShadowWhiteTexture(), -100)
        root.setShader(shader, 100)
        _enableAllShadowCasters(root)
        _updateWorldShadowShaderInputs()
    else:
        try:
            root.clearShader()
        except Exception:
            pass
        if _worldShadowTextureState is not None and _worldShadowTextureState[0] == root:
            _np, attrib = _worldShadowTextureState
            try:
                root.clearAttrib(TextureAttrib.getClassType())
                if attrib is not None:
                    root.setAttrib(attrib)
            except Exception:
                pass
            _worldShadowTextureState = None
    return use_shaders


def _findExtraShaderState(np: NodePath) -> dict[str, Any] | None:
    for entry in _extraShaderStates:
        try:
            if entry['np'] == np:
                return entry
        except Exception:
            pass
    return None


def _restoreExtraShaderState(entry: dict[str, Any]) -> None:
    np = entry.get('np')
    if np is None or np.isEmpty() or not entry.get('applied', False):
        return
    try:
        np.clearAttrib(ShaderAttrib.getClassType())
        old_shader = entry.get('shader')
        if old_shader is not None:
            np.setAttrib(old_shader)
    except Exception:
        pass
    try:
        np.clearAttrib(TextureAttrib.getClassType())
        old_texture = entry.get('texture')
        if old_texture is not None:
            np.setAttrib(old_texture)
    except Exception:
        pass
    entry['applied'] = False


def _applyExtraShaderState(np: NodePath) -> None:
    """Apply and own the shader state for one streamed outdoor subtree."""
    global _extraShaderStates
    if np is None or np.isEmpty() or np == getattr(base, 'render', None):
        return
    entry = _findExtraShaderState(np)
    if entry is None:
        try:
            old_shader = np.getAttrib(ShaderAttrib.getClassType())
        except Exception:
            old_shader = None
        try:
            old_texture = np.getAttrib(TextureAttrib.getClassType())
        except Exception:
            old_texture = None
        entry = {
            'np': np,
            'shader': old_shader,
            'texture': old_texture,
            'applied': False,
        }
        _extraShaderStates.append(entry)
    elif entry.get('applied', False):
        _restoreExtraShaderState(entry)

    if _worldShadersActive():
        shader = _loadWorldShadowShader()
        if shader is None:
            return
        try:
            preprocessGeometry(np)
        except Exception:
            pass
        _clearShadersForCustomReceiver(np)
        np.setTexture(TextureStage.getDefault(), _getWorldShadowWhiteTexture(), -100)
        np.setShader(shader, 100)
        _enableAllShadowCasters(np)
        # Ensure render owns the full input ABI even when the session has no
        # primary geometry root.
        _updateWorldShadowShaderInputs()
    else:
        np.setShaderAuto()
    entry['applied'] = True


def begin(geom, style: str = 'playground',
          hoodId: int | None = None,
          zoneId: int | None = None,
          fogEnabled: bool = True) -> None:
    """Activate the outdoor lighting rig for the given geometry.

    Parameters
    ----------
    geom:
        Scene-geometry NodePath; receives per-pixel auto-shading.
    style:
        Fallback profile key (``'playground'``, ``'estate'``, ``'cog'``, etc.).
    hoodId:
        ToontownGlobals hood constant – overrides *style* for zone lookup.
    zoneId:
        Specific zone constant (e.g. SillyStreet) – used for street-level
        profiles.  Takes priority over *hoodId*.
    fogEnabled:
        When False, atmospheric fog is suppressed for this zone even if the
        profile defines one (used by go-kart races).
    """
    _syncBase()
    global _refCount, _activeStyle, _activeGeom, _lampGeomNps, _styleStack, _fogEnabled
    _fogEnabled = fogEnabled
    resolvedStyle = _resolveStyle(style, hoodId, zoneId)
    _styleStack.append((resolvedStyle, geom))
    oldStyle = _activeStyle
    _activeStyle = resolvedStyle
    _activeGeom = geom
    if not _wantFx() or _bisectStep() < 1:
        # Track the zone reference even while disabled so refreshSettings() can
        # enable the system in-place without requiring a zone reload.
        if _refCount == 0:
            _syncTimeOfDayFromSettings()
        _refCount += 1
        return

    if _bisectStep() == 1:
        _refCount += 1
        if geom is not None and not geom.isEmpty():
            _syncNightWorldLighting(geom, 1.0)
            _syncNightWorldTint(geom, 1.0)
        return

    if _bisectStep() in (2, 3, 5, 6, 7):
        _refCount += 1
        _activeStyle = _resolveStyle(style, hoodId, zoneId)
        _syncTimeOfDayFromSettings()
        spec = _ZONE_PROFILES.get(_activeStyle, _ZONE_PROFILES['playground'])
        cur_spec = _getActiveSpec() if spec.get('dayNightEnabled', True) else spec
        if geom is not None and not geom.isEmpty():
            _syncNightWorldLighting(geom, _nightFactor())
            _syncNightWorldTint(geom, _nightFactor())
        if _bisectStep() >= 3:
            if not _lampGeomNps:
                _lampGeomNps = _scanForLampNodes(geom)
            _syncLampLights(force=True)
        if _bisectStep() >= 6:
            _setupProceduralSky(cur_spec)
        if _bisectStep() >= 7:
            _setupPostProcess(cur_spec)
        taskMgr.add(_lightingUpdateTask, _godRaysTaskName, sort=-60)
        return

    spec          = _ZONE_PROFILES[resolvedStyle]
    _dbg(f"begin(refCount={_refCount}) style={style} resolved={resolvedStyle} hoodId={hoodId} zoneId={zoneId}")
    _maybeBindBisectHotkeys()
    # Keep OTP blob/drop shadows synced regardless of shader support.
    if _OTPDropshadow:
        try:
            _OTPDropshadow.setGlobalDropShadowFlag(1 if _settingsBool('dynamic-shadows', True) else 0)
        except Exception:
            pass
        try:
            _OTPDropshadow.setGlobalDropShadowGrayLevel(float(_settingsFloat('drop-shadow-strength', 0.5)))
        except Exception:
            pass

    _dbg(f"settings wantFx={_wantFx()} dynShadows={_wantDynamicShadows()} shadowQuality={_shadowQuality()} wantBloom={_wantBloom()} wantProceduralSky={_wantProceduralSky()}")

    if _refCount == 0:
        _dbgBisectState()
        _activeStyle = resolvedStyle
        _syncTimeOfDayFromSettings()
        cur_spec     = _getActiveSpec() if spec.get('dayNightEnabled', True) else spec

        # Hard cleanup of optional subsystems when bisecting.
        # This prevents leftovers from prior runs (or other systems) from making
        # it *look* like everything is still enabled at low bisect steps.
        try:
            if not _bisectAllows(9):
                _cleanupAllWater()
        except Exception:
            pass
        try:
            if not _bisectAllows(8):
                _destroyGodRaysOverlay()
        except Exception:
            pass
        try:
            if not _bisectAllows(7):
                _destroyPostProcess()
        except Exception:
            pass
        try:
            if not _bisectAllows(6):
                _destroyProceduralSky()
        except Exception:
            pass
        try:
            if not _bisectAllows(5):
                _clearFog()
                _clearSkyTint()
                _restoreBackgroundColor()
        except Exception:
            pass

        if not _bisectAllows(1):
            return
        # Step ladder:
        # 1: shader-auto only (no lights)
        # 2: ambient only
        # 3: sun (no shadows)
        # 4: sun (with shadows, if enabled)
        rig_spawned = False
        if _bisectAllows(2):
            enable_shadows = bool(_bisectAllows(4))
            enable_key = bool(_bisectAllows(3))
            # Further bisect within directional lighting to isolate camera-dependent RGB:
            # key-only vs key+fill vs key+fill+rim.
            try:
                mode = _DIR_LIGHT_MODE
            except Exception:
                mode = 'all'
            enable_fill = enable_key and (mode in ('key_fill', 'all'))
            enable_rim  = enable_key and (mode == 'all')
            _spawnLightRig(
                cur_spec, geom,
                enable_ambient=True,
                enable_key=enable_key,
                enable_fill=enable_fill,
                enable_rim=enable_rim,
                enable_shadows=enable_shadows,
            )
            rig_spawned = True
            _dbg(
                "spawned rig "
                f"keyCastsShadows={_keyCastsShadows} shadowRes={_shadowMapRes} "
                f"filmArea={_shadowFilmArea} followDist={_shadowFollowDist}"
            )
        else:
            _dbg("bisect: skipping light rig spawn (step<2)")

        # Only run shadow diagnostics when we actually spawned a rig.
        if rig_spawned:
            try:
                _spawnShadowTestScene()
            except Exception:
                pass
            # Renderer capability debug (shadow maps rely on these).
            try:
                gsg = base.win.getGsg() if getattr(base, 'win', None) else None
                if gsg:
                    try:
                        supportsFBO = gsg.getSupportsFramebuffer() if hasattr(gsg, 'getSupportsFramebuffer') else 'n/a'
                    except Exception:
                        supportsFBO = 'n/a'
                    try:
                        supportsBasic = gsg.getSupportsBasicShaders() if hasattr(gsg, 'getSupportsBasicShaders') else 'n/a'
                    except Exception:
                        supportsBasic = 'n/a'
                    try:
                        maxTex = gsg.getMaxTextureDimension() if hasattr(gsg, 'getMaxTextureDimension') else 'n/a'
                    except Exception:
                        maxTex = 'n/a'

                    _dbg(f"gsg supportsFBO={supportsFBO} supportsBasicShaders={supportsBasic} maxTexDim={maxTex}")
                    try:
                        _dbg(f"panda version={PandaSystem.getGlobalPtr().getVersionString()}")
                    except Exception:
                        pass
                    try:
                        import panda3d.core as _p3c
                        _dbg(f"python exe={sys.executable}")
                        _dbg(f"panda3d.core file={getattr(_p3c, '__file__', 'n/a')}")
                    except Exception:
                        pass

                    try:
                        vendor = gsg.getDriverVendor() if hasattr(gsg, 'getDriverVendor') else 'n/a'
                    except Exception:
                        vendor = 'n/a'
                    try:
                        renderer = gsg.getDriverRenderer() if hasattr(gsg, 'getDriverRenderer') else 'n/a'
                    except Exception:
                        renderer = 'n/a'
                    try:
                        version = gsg.getDriverVersion() if hasattr(gsg, 'getDriverVersion') else 'n/a'
                    except Exception:
                        version = 'n/a'
                    _dbg(f"gsg driver vendor={vendor} renderer={renderer} version={version}")

                    try:
                        pipe = getattr(base, 'pipe', None)
                        pipeType = pipe.getType().getName() if pipe and hasattr(pipe, 'getType') else 'n/a'
                    except Exception:
                        pipeType = 'n/a'
                    try:
                        iface = pipe.getInterfaceName() if pipe and hasattr(pipe, 'getInterfaceName') else 'n/a'
                    except Exception:
                        iface = 'n/a'
                    try:
                        loadDisplay = ConfigVariableString('load-display', '').value
                    except Exception:
                        loadDisplay = 'n/a'
                    _dbg(f"pipe type={pipeType} interface={iface} prc load-display={loadDisplay}")
            except Exception:
                pass
        if not _supportsBasicShaders():
            _dbg("basic shaders unsupported; shadow maps disabled (using OTP drop shadows instead)")
        # Deferred shadow buffer allocation check and fallback retry:
        # Buffer/texture allocation happens asynchronously on the Draw thread,
        # so we schedule a check after 0.5s. If allocation failed, we fall back
        # to a lower resolution or disable shadows, avoiding multiple conflicting
        # setShadowCaster calls in the same frame.
        try:
            taskMgr.remove('outdoorLightingShadowAllocDebug')
        except Exception:
            pass

        def _shadowAllocDebug(task):
            global _shadowMapRes, _keyCastsShadows
            try:
                if not (_keyLightNp and not _keyLightNp.isEmpty()):
                    _dbg("shadowAllocDebug: key light NP missing")
                    return task.done
                k = _keyLightNp.getNode(0)
                _dbg(f"shadowAllocDebug: isShadowCaster={getattr(k, 'isShadowCaster', lambda: 'n/a')()}")
                gsg = None
                try:
                    gsg = base.win.getGsg() if getattr(base, 'win', None) else None
                except Exception:
                    gsg = None
                try:
                    if gsg:
                        _dbg(
                            "shadowAllocDebug: gsg "
                            f"supportsFBO={getattr(gsg, 'getSupportsFramebuffer', lambda: 'n/a')()} "
                            f"supportsBasicShaders={getattr(gsg, 'getSupportsBasicShaders', lambda: 'n/a')()} "
                            f"supportsDepthTex={getattr(gsg, 'getSupportsDepthTexture', lambda: 'n/a')()} "
                            f"maxTexDim={getattr(gsg, 'getMaxTextureDimension', lambda: 'n/a')()}"
                        )
                except Exception:
                    pass

                def _call_shadow_accessor(name: str):
                    if not hasattr(k, name):
                        return None
                    fn = getattr(k, name)
                    # Panda3D builds differ: some take (gsg, i), others (gsg) returning list-like,
                    # others take (i) (rare). Try common signatures.
                    for args in ((gsg, 0), (gsg,), (0,)):
                        try:
                            if gsg is None and args and args[0] is gsg:
                                continue
                            v = fn(*args)
                            _dbg(f"shadowAllocDebug: {name}{args}={'ok' if v else 'none'}")
                            return v
                        except TypeError as e:
                            last = e
                        except Exception as e:
                            _dbg(f"shadowAllocDebug: {name}{args} exception: {e!r}")
                            return None
                    try:
                        _dbg(f"shadowAllocDebug: {name} signature mismatch: {last!r}")
                    except Exception:
                        pass
                    return None

                buf = _call_shadow_accessor('getShadowBuffer')

                # If the light is configured to cast shadows, but the shadow buffer isn't allocating, retry at smaller resolutions.
                if getattr(k, 'isShadowCaster', lambda: False)() and (buf is None) and hasattr(k, 'setShadowCaster') and gsg:
                    if not gsg.getSupportsBasicShaders():
                        print("[DEBUG VideoSettings] shadowAllocDebug: GSG reports getSupportsBasicShaders() is False. Disabling shadow maps to prevent GL errors.")
                        k.setShadowCaster(False)
                        _keyCastsShadows = False
                    else:
                        if _shadowMapRes > 1024:
                            retry_res = 1024
                        elif _shadowMapRes > 512:
                            retry_res = 512
                        else:
                            retry_res = 0

                        if retry_res > 0:
                            print(f"[DEBUG VideoSettings] shadowAllocDebug: shadow buffer not allocated at {_shadowMapRes}. Falling back to {retry_res}...")
                            try:
                                k.setShadowCaster(True, retry_res, retry_res)
                                try:
                                    k.setCameraMask(_SHADOW_CAM_MASK)
                                except Exception:
                                    pass
                                _shadowMapRes = retry_res
                                _dbg(f"shadowAllocDebug: retry setShadowCaster({retry_res}) scheduled")
                                taskMgr.doMethodLater(0.5, _shadowAllocDebug, 'outdoorLightingShadowAllocDebug')
                            except Exception as e:
                                print(f"[DEBUG VideoSettings] shadowAllocDebug: retry setShadowCaster({retry_res}) failed: {e!r}")
                                _dbg(f"shadowAllocDebug: retry setShadowCaster({retry_res}) exception: {e!r}")
                        else:
                            print(f"[DEBUG VideoSettings] shadowAllocDebug: shadow buffer not allocated at {_shadowMapRes}. Disabling shadow maps completely.")
                            k.setShadowCaster(False)
                            _keyCastsShadows = False
                return task.done
            except Exception as e:
                _dbg(f"shadowAllocDebug exception: {e!r}")
                return task.done

        taskMgr.doMethodLater(0.5, _shadowAllocDebug, 'outdoorLightingShadowAllocDebug')
        try:
            la = base.render.getAttrib(LightAttrib.getClassType())
            if la is not None:
                try:
                    num_on = la.getNumOnLights()
                except Exception:
                    num_on = None
                _dbg(f"render LightAttrib onLights={num_on}")
        except Exception:
            pass
        try:
            if _keyLightNp is not None and not _keyLightNp.isEmpty():
                _dbg(f"key light node={_keyLightNp.getNode(0)}")
        except Exception:
            pass
        if _bisectAllows(5):
            _applyFog(cur_spec)
            _tintSky(cur_spec)
            _applyBackgroundColor(cur_spec)
        if _bisectAllows(6):
            _setupProceduralSky(cur_spec)
        if _OUTDOOR_SHADER_BISECT_LEVEL >= 2 and _bisectAllows(7):
            _setupPostProcess(cur_spec)
        if _bisectAllows(2):
            taskMgr.add(_lightingUpdateTask, _godRaysTaskName, sort=-60)
    _refCount += 1

    if _bisectAllows(3):
        if not _lampGeomNps and geom is not None and not geom.isEmpty():
            _lampGeomNps = _scanForLampNodes(geom)
        _syncLampLights(force=True)

    if geom is not None and not geom.isEmpty():
        _forceLightingOnSubtree(geom)
        _captureShaderState(geom, avatar=False)
        # Install the custom receiver once.  It remains identical across every
        # time-of-day mode; only numeric light/shadow inputs change.
        _applyWorldShaderState(geom)
        _syncNightWorldLighting(geom, _nightFactor())
        _syncNightWorldTint(geom, _nightFactor())
        try:
            hood = _getHood()
            if hood and getattr(hood, 'sky', None) and not hood.sky.isEmpty():
                hood.sky.hide(_SHADOW_CAM_MASK)
        except Exception:
            pass
        # Do not force a specular material on the entire zone geometry. Zone
        # meshes are frequently large, use vertex colors, and may contain
        # mixed material setups; forcing a specular material globally can
        # introduce view-dependent color artifacts. Specular remains available
        # opt-in via `lighting-specular-enabled` on specific subtrees.
        try:
            la = geom.getAttrib(LightAttrib.getClassType())
            _dbg(f"geom LightAttrib present={la is not None}")
        except Exception:
            pass
        try:
            ra = base.render.getAttrib(LightAttrib.getClassType())
            _dbg(f"render LightAttrib present={ra is not None}")
        except Exception:
            pass

        # Debug: shader state + normals presence (flat lighting can be caused by missing normals).
        try:
            if _debugEnabled():
                try:
                    _dbg(f"geom hasShader={'yes' if geom.hasShader() else 'no'}")
                except Exception:
                    pass
                try:
                    _dbg(f"render hasShader={'yes' if base.render.hasShader() else 'no'}")
                except Exception:
                    pass
                try:
                    sa = base.render.getAttrib(ShaderAttrib.getClassType())
                    _dbg(f"render ShaderAttrib present={sa is not None}")
                    if sa is not None:
                        try:
                            sh = sa.getShader()
                            _dbg(f"render ShaderAttrib shader={'ok' if sh else 'none'}")
                        except Exception:
                            pass
                        for meth in ('getAutoShader', 'getFlag'):
                            if hasattr(sa, meth):
                                try:
                                    _dbg(f"render ShaderAttrib {meth}()={getattr(sa, meth)()}")
                                except Exception:
                                    pass
                except Exception:
                    pass
                try:
                    sa = geom.getAttrib(ShaderAttrib.getClassType())
                    _dbg(f"geom ShaderAttrib present={sa is not None}")
                    if sa is not None:
                        try:
                            sh = sa.getShader()
                            _dbg(f"geom ShaderAttrib shader={'ok' if sh else 'none'}")
                        except Exception:
                            pass
                        for meth in ('getAutoShader', 'getFlag'):
                            if hasattr(sa, meth):
                                try:
                                    _dbg(f"geom ShaderAttrib {meth}()={getattr(sa, meth)()}")
                                except Exception:
                                    pass
                except Exception:
                    pass
                try:
                    nodes = geom.findAllMatches('**/+GeomNode')
                    total = nodes.getNumPaths()
                    with_normals = 0
                    checked = min(total, 50)
                    for i in range(checked):
                        gnp = nodes.getPath(i).node()
                        if gnp.getNumGeoms() <= 0:
                            continue
                        g = gnp.getGeom(0)
                        vdata = g.getVertexData()
                        if vdata and vdata.hasColumn('normal'):
                            with_normals += 1
                    _dbg(f"geom geomNodes={total} checked={checked} withNormals={with_normals}")
                except Exception:
                    pass
        except Exception:
            pass
        # Scan for lamp/lantern nodes to use as night-light emitters.
        # Only scan on first begin() (refCount was 0 before increment).
        if _refCount == 1:
            _lampGeomNps = _scanForLampNodes(geom)
            _syncLampLights(force=True)
            if _bisectAllows(9) and _OUTDOOR_SHADER_BISECT_LEVEL >= 3:
                _setupAllWater(geom, cur_spec)

    lav = getattr(base, 'localAvatar', None)
    if lav is not None and not lav.isEmpty():
        try:
            lav.clearShader()
        except Exception:
            pass
    _syncAvatarNightTint(_nightFactor())


def _applyDefaultSpecularMaterial(np: NodePath) -> None:
    if np is None or np.isEmpty():
        return
    try:
        if not np.getPythonTag(_SPEC_TAG):
            m = Material('outdoorDefaultSpecular')
            m.setSpecular(Vec4(0.12, 0.12, 0.12, 1.0))
            m.setShininess(16.0)
            np.setMaterial(m, 100)
            np.setPythonTag(_SPEC_TAG, True)
    except Exception:
        pass


def _clearDefaultSpecularMaterial(np: NodePath) -> None:
    if np is None or np.isEmpty():
        return
    try:
        if np.getPythonTag(_SPEC_TAG):
            np.clearMaterial()
            np.clearPythonTag(_SPEC_TAG)
    except Exception:
        pass


def end(geom=None) -> None:
    """Deactivate one OutdoorLighting reference and restore state on final release."""
    _syncBase()
    global _refCount, _activeGeom, _lampGeomNps, _activeStyle, _styleStack, _fogEnabled
    _dbg(f"end(refCount={_refCount}) geom={'ok' if (geom is not None and not geom.isEmpty()) else 'none'}")

    if _refCount <= 0:
        return
    _refCount -= 1
    if _styleStack:
        _styleStack.pop()
    if _refCount > 0:
        if _styleStack:
            prevStyle, prevGeom = _styleStack[-1]
            if prevStyle != _activeStyle:
                _activeStyle = prevStyle
                _activeGeom = prevGeom
                spec = _ZONE_PROFILES.get(_activeStyle, _ZONE_PROFILES['playground'])
                cur_spec = _getActiveSpec() if spec.get('dayNightEnabled', True) else spec
                _setupProceduralSky(cur_spec)
                _applyFog(cur_spec)
                _setupPostProcess(cur_spec)
                _syncTimeOfDayFromSettings()
        return

    # Only the final release tears down shared render state.  This avoids one
    # nested caller disabling lighting while another outdoor owner is active.
    taskMgr.remove(_godRaysTaskName)
    try:
        taskMgr.remove('outdoorLightingShadowAllocDebug')
    except Exception:
        pass
    _destroyPostProcess()
    _cleanupAllWater()
    _clearFog()
    _fogEnabled = True
    _clearSkyTint()
    _restoreBackgroundColor()
    _destroyLightRig()
    _restoreSkyLighting()
    _destroyProceduralSky()
    global _prevViewProjMat
    _prevViewProjMat = None
    _restoreNightWorldTint()
    _restoreNightCardTints()
    _restoreAvatarNightTint()
    _restoreShaderStates()

    _lampGeomNps = []
    _activeGeom = None


def shadeExtraSubtree(np) -> None:
    """Shade an extra outdoor NodePath with the active stable receiver."""
    _syncBase()
    if not _wantFx() or np is None or np.isEmpty():
        return
    _applyExtraShaderState(np)
    _forceLightingOnSubtree(np)
    _enableAllShadowCasters(np)
    _applyDefaultSpecularMaterial(np)
    _hideLegacySkyNodes(np)


def clearExtraSubtree(np) -> None:
    """Restore the exact pre-lighting state of an extra outdoor NodePath."""
    global _extraShaderStates
    _syncBase()
    if not _wantFx() or np is None or np.isEmpty():
        return
    entry = _findExtraShaderState(np)
    if entry is not None:
        _restoreExtraShaderState(entry)
        _extraShaderStates.remove(entry)
    else:
        np.clearShader()
    _clearDefaultSpecularMaterial(np)

def _maybeRebuildLightRig(spec: dict) -> None:
    global _lightRig, _keyCastsShadows, _shadowMapRes, _prevShadowCasterState, _activeGeom
    global _lightRigSettingsSignature
    night = _nightFactor()
    current_wants_shadow = _keyCastsShadows
    current_res = _shadowMapRes

    wants_shadow = (spec.get('shadowCaster', False)
                    and _wantWorldShadows()
                    and night <= 0.04)
    
    # Determine the resolution snap-down
    res = 1024
    if wants_shadow:
        try:
            res = _scaledShadowRes(spec)
            # Apply clamps similar to _spawnLightRig
            gsg = base.win.getGsg() if getattr(base, 'win', None) else None
            if gsg:
                try:
                    max_dim = int(gsg.getMaxTextureDimension())
                    if max_dim and res > max_dim:
                        res = max_dim
                except Exception:
                    pass
            if res >= 4096: res = 4096
            elif res >= 2048: res = 2048
            elif res >= 1024: res = 1024
            else: res = 512
        except Exception:
            pass

    settings_signature = (_wantDynamicShadows(), _shadowQuality(), _wantWorldShadows())
    # A time-mode change can alter wants_shadow, but must not replace the live
    # light topology.  Only an explicit shadow setting/quality change (or a
    # missing rig) authorizes a rebuild here.
    need_rebuild = (_lightRig is None or _lightRig.isEmpty() or
                    settings_signature != _lightRigSettingsSignature)

    if need_rebuild:
        _dbg(f"rebuilding light rig: shadows={wants_shadow} resolution={res}")
        savedBounds = None
        if _shadowFocusPos is not None:
            savedBounds = (Vec3(_shadowFocusPos), float(_shadowSceneRadius))
        _destroyLightRig()
        _spawnLightRig(spec, geom=_activeGeom, shadowBounds=savedBounds)
        if _activeGeom is not None and not _activeGeom.isEmpty():
            try:
                _forceLightingOnSubtree(_activeGeom)
                _enableAllShadowCasters(_activeGeom)
                _syncNightWorldLighting(_activeGeom, night)
                _syncNightWorldTint(_activeGeom, night)
                _syncNightCardTints(_activeGeom, night)
            except Exception as e:
                _dbg(f"geometry shader regeneration failed: {e!r}")
        lav = getattr(base, 'localAvatar', None)
        if lav is not None and not lav.isEmpty():
            try:
                lav.clearShader()
            except Exception as e:
                pass
    else:
        _applyProfileLive(spec)


def refreshSettings() -> None:
    """Hot-reload active lighting settings while preserving scene state."""
    _syncBase()
    if _refCount == 0:
        return

    try:
        if base is not None and getattr(base, 'config', None) and base.config.GetString('threading-model', ''):
            base.graphicsEngine.syncFrame()
    except Exception as error:
        _dbg(f'refresh syncFrame failed: {error!r}')

    if _OTPDropshadow:
        try:
            _OTPDropshadow.setGlobalDropShadowFlag(1 if _settingsBool('dynamic-shadows', True) else 0)
            _OTPDropshadow.setGlobalDropShadowGrayLevel(float(_settingsFloat('drop-shadow-strength', 0.5)))
        except Exception as error:
            _dbg(f'drop-shadow refresh failed: {error!r}')

    global _timeOfDay
    _syncTimeOfDayFromSettings()

    if not _wantFx() or _bisectStep() < 1:
        _destroyPostProcess()
        _cleanupAllWater()
        _clearFog()
        _destroyLightRig()
        _destroyProceduralSky()
        _restoreNightWorldTint()
        _restoreNightCardTints()
        _restoreAvatarNightTint()
        # Keep streamed-root ownership records so turning the setting back on
        # can reapply receivers without waiting for the distributed object to
        # generate again.
        _restoreShaderStates(releaseExtras=False)
        _clearSkyTint()
        _restoreBackgroundColor()
        return

    spec = _getActiveSpec()
    if _activeGeom is not None and not _activeGeom.isEmpty():
        _captureShaderState(_activeGeom, avatar=False)
        # Reflect the world-shadow setting on the live zone by selecting the
        # custom receiver or the fixed-function compatibility fallback.
        _applyWorldShaderState(_activeGeom)
    for entry in list(_extraShaderStates):
        np = entry.get('np')
        if np is not None and not np.isEmpty():
            _applyExtraShaderState(np)
    lav = getattr(base, 'localAvatar', None)
    if lav is not None and not lav.isEmpty():
        _captureShaderState(lav, avatar=True)
        try:
            lav.clearShader()
        except Exception:
            pass

    _maybeRebuildLightRig(spec)
    if _activeGeom is not None and not _activeGeom.isEmpty():
        _forceLightingOnSubtree(_activeGeom)
        _syncNightWorldLighting(_activeGeom, _nightFactor())
        _syncNightWorldTint(_activeGeom, _nightFactor())

    _clearFog()
    _applyFog(spec)
    _tintSky(spec)
    _applyBackgroundColor(spec)

    if _wantProceduralSky():
        if _proceduralSky is None:
            _setupProceduralSky(spec)
        else:
            _proceduralSky.setStyle(_activeStyle)
            _proceduralSky.update(spec, _timeOfDay)
    else:
        _destroyProceduralSky()
        _tintSky(spec)

    _destroyPostProcess()
    if _OUTDOOR_SHADER_BISECT_LEVEL >= 2 and _bisectAllows(7):
        _setupPostProcess(spec)

    if _bisectAllows(9) and _activeGeom is not None and not _activeGeom.isEmpty():
        _cleanupAllWater()
        _setupAllWater(_activeGeom, spec)

    _syncLampLights(force=True)
    _syncAvatarNightTint(_nightFactor())


def setTimeOfDay(hours: float) -> None:
    """Set the current in-game time of day (0–24)."""
    _syncBase()
    global _timeOfDay
    _timeOfDay = float(hours) % 24.0
    if _refCount > 0:
        cur = _getActiveSpec()
        _applyProfileLive(cur)
        _syncNightWorldLighting(_activeGeom, _nightFactor())
        _syncNightWorldTint(_activeGeom, _nightFactor())
        _syncNightCardTints(_activeGeom, _nightFactor())
        if _bisectAllows(4):
            _syncShadowCasterWithTimeOfDay()
        _syncAvatarNightTint(_nightFactor())
        _clearFog()
        _applyFog(cur)
        _tintSky(cur)
        _applyBackgroundColor(cur)
        _updateBloomLive(cur)
        _updatePostProcessLive(cur)
        if _proceduralSky:
            _proceduralSky.update(cur, _timeOfDay)
        _syncLampLights(force=True)


def setupWaterReflection(waterNodePath, profile: dict | None = None) -> None:
    """Manually set up planar reflections on a specific water NodePath."""
    _syncBase()
    if not _wantFx() or waterNodePath is None or waterNodePath.isEmpty():
        return
    base_spec = _ZONE_PROFILES.get(_activeStyle, _ZONE_PROFILES['playground'])
    merged    = dict(base_spec)
    if profile:
        merged.update(profile)
    setup = _setupWaterNode(waterNodePath, merged)
    if setup:
        _waterSetups.append(setup)


def setWaterReflectionQuality(level: str) -> None:
    """Rebuild active reflection buffers at a new quality without retaining stale setups."""
    _syncBase()
    normalized = str(level).strip().lower()
    if normalized == 'off':
        _cleanupAllWater()
        return
    if normalized not in ('low', 'medium', 'high'):
        normalized = 'medium'
    nodes = [ws.get('np') for ws in _waterSetups
             if ws.get('np') is not None and not ws.get('np').isEmpty()]
    _cleanupAllWater()
    spec = dict(_getActiveSpec())
    spec['waterReflQuality'] = normalized
    for np in nodes:
        setup = _setupWaterNode(np, spec)
        if setup:
            _waterSetups.append(setup)


def setCelShading(enabled: bool) -> None:
    _syncBase()
    try:
        if hasattr(base, 'settings'):
            base.settings.set('lighting-cel-shading', bool(enabled))
    except Exception:
        pass
    refreshSettings()


def getCelShading() -> bool:
    return _settingsBool('lighting-cel-shading', False)


def getActiveStyle() -> str:
    return _activeStyle

_ZONE_PROFILES['cashbot_mint'] = _ZONE_PROFILES['mint_int']
_ZONE_PROFILES['lawbot_office'] = _ZONE_PROFILES['office_int']


