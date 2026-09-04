#version 130

uniform vec3  sunDir;
uniform float sunWorldElev;
uniform vec3  sunColor;
uniform vec3  sunLightColor;   // sunColor after Rayleigh+Mie extinction (matches scene key light)
uniform vec3  zenithColor;
uniform vec3  horizonColor;
uniform vec3  fogColor;
uniform float fogDensity;      // 0..~1.4 zone fog density for aerial perspective
uniform vec3  ambientColor;
uniform vec3  fillColor;
uniform vec3  rimColor;
uniform vec3  rayColor;
uniform float rayIntensity;
uniform vec3  deptSmogTint;
uniform float cloudCoverage;
uniform float cloudSpeed;
uniform float cloudSharpness;
uniform float cloudQuality;
uniform float turbidity;
uniform float starBrightness;
uniform float moonEnabled;
uniform vec3  moonDir;
uniform vec4  moonColor;
uniform float moonPhase;
uniform float auroraEnabled;
uniform float milkyWayStrength;
uniform float nightFactor;
uniform float twilightFactor;
uniform float cloudShadowStrength;
uniform float skyExposure;
uniform float moonAngularRadius;
uniform float sunAngularRadius;
uniform float time;
uniform vec4  skyScale;
uniform float sunDiscEnabled;
uniform float sunBlindStrength;
uniform float timeOfDay;

in  vec3 vDir;
in  vec2 vUV;
out vec4 fragColor;

float _hash(vec2 p) {
    p  = fract(p * vec2(127.1, 311.7));
    p += dot(p, p + 19.19);
    return fract(p.x * p.y);
}

vec2 _hash22(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.xx + p3.yz) * p3.zy);
}

float _vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    return mix(
        mix(_hash(i),                _hash(i + vec2(1.0, 0.0)), u.x),
        mix(_hash(i + vec2(0.0, 1.0)), _hash(i + vec2(1.0, 1.0)), u.x),
        u.y
    );
}

float _fbm(vec2 p) {
    float v   = 0.0;
    float amp = 0.5;
    mat2  rot = mat2(1.6, 1.2, -1.2, 1.6);
    for (int i = 0; i < 5; ++i) {
        v   += amp * _vnoise(p);
        p    = rot * p * 2.05;
        amp *= 0.48;
    }
    return v;
}

float _cellular(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    float minDist = 1.0;
    for (int y = -1; y <= 1; ++y) {
        for (int x = -1; x <= 1; ++x) {
            vec2 neighbor = vec2(float(x), float(y));
            vec2 point = _hash22(i + neighbor);
            vec2 diff = neighbor + point - f;
            minDist = min(minDist, length(diff));
        }
    }
    return minDist;
}

float _mie(float cosA, float g) {
    float g2 = g * g;
    return (1.0 - g2) / (pow(max(1.0e-4, 1.0 + g2 - 2.0 * g * cosA), 1.5) * 4.0 * 3.14159265);
}

float _mieDual(float cosA, float g1, float g2, float w) {
    return mix(_mie(cosA, g2), _mie(cosA, g1), w);
}

float _rayleigh(float cosA) {
    return 0.0596831 * (1.0 + cosA * cosA);
}

vec3 _starLayer(vec2 sphericalUV, float grid, float seedOffset) {
    // Cell-local analytic stars remain round and anti-aliased at every window
    // resolution; unlike quantised direction cells, they never become squares.
    vec2 scaled = sphericalUV * vec2(grid, grid * 0.50);
    vec2 cellId = floor(scaled);
    vec2 local = fract(scaled);
    vec2 starPos = 0.12 + _hash22(cellId + seedOffset) * 0.76;
    vec2 delta = local - starPos;
    float latitude = (sphericalUV.y - 0.5) * 3.14159265;
    delta.x *= max(0.18, cos(latitude));
    float distanceToStar = length(delta);

    float seed = _hash(cellId + vec2(seedOffset, seedOffset * 2.31));
    float exists = smoothstep(0.978, 0.998, seed);
    float sizeSeed = _hash(cellId.yx + seedOffset * 4.17);
    float radius = mix(0.016, 0.068, sizeSeed * sizeSeed);
    float aa = max(fwidth(distanceToStar) * 1.35, 0.0015);
    float core = 1.0 - smoothstep(radius - aa, radius + aa, distanceToStar);
    float halo = exp(-distanceToStar * mix(38.0, 16.0, sizeSeed)) * 0.22;

    float spikeCross = 0.0;
    if (sizeSeed > 0.82) {
        float sW = 0.006;
        float spikeH = max(0.0, 1.0 - abs(delta.y) / sW) * exp(-abs(delta.x) * 18.0);
        float spikeV = max(0.0, 1.0 - abs(delta.x) / sW) * exp(-abs(delta.y) * 18.0);
        spikeCross = (spikeH + spikeV) * (sizeSeed - 0.82) * 4.5;
    }

    float airmassScint = 1.0 / max(0.12, abs(sin(latitude)) + 0.08);
    float twinkleSeed = _hash(cellId + seedOffset * 7.73);
    float twinkleSpeed = mix(0.8, 3.5, twinkleSeed) * sqrt(airmassScint);
    float twinkleDepth = mix(0.15, 0.45, twinkleSeed);
    float twinkle = (1.0 - twinkleDepth) + twinkleDepth * sin(time * twinkleSpeed + twinkleSeed * 6.28318530);

    float specSeed = _hash(cellId + seedOffset * 11.9);
    vec3 starColor;
    if (specSeed < 0.30) {
        starColor = mix(vec3(0.68, 0.82, 1.00), vec3(0.85, 0.92, 1.00), specSeed / 0.30);
    } else if (specSeed < 0.70) {
        starColor = mix(vec3(0.95, 0.98, 1.00), vec3(1.00, 0.92, 0.75), (specSeed - 0.30) / 0.40);
    } else {
        starColor = mix(vec3(1.00, 0.75, 0.45), vec3(1.00, 0.48, 0.28), (specSeed - 0.70) / 0.30);
    }
    return starColor * (core + halo * sizeSeed + spikeCross) * exists * twinkle;
}

void main() {
    vec3  dir      = normalize(vDir);
    float elev     = dir.z;
    float elevAbs  = abs(elev);
    float cosTheta = dot(dir, sunDir);
    float sunElevW = sunWorldElev;
    float sunPower = clamp(sunElevW * 3.5 + 0.28, 0.0, 1.0)
                   * (1.0 - nightFactor);

    float airMassView = 1.0 / max(0.028, elev + 0.085 * exp(-max(0.0, elev) * 4.0));
    float airMassSun  = 1.0 / max(0.028, sunElevW + 0.085 * exp(-max(0.0, sunElevW) * 4.0));
    airMassView = clamp(airMassView, 1.0, 25.0);
    airMassSun  = clamp(airMassSun, 1.0, 25.0);

    vec3 betaRayleigh = vec3(0.145, 0.380, 0.950);
    vec3 betaMie = vec3(0.045) * turbidity;
    vec3 sunTrans = exp(-(betaRayleigh * 0.65 + betaMie * 0.25) * airMassSun * (1.0 - nightFactor));

    float rayPhase = _rayleigh(cosTheta);
    vec3 rayleighCol = betaRayleigh * rayPhase * sunTrans * (1.45 * sunPower);

    // sunLightColor already includes Rayleigh + Mie extinction (computed in
    // Python from the same model), so the glow, disc and clouds all agree with
    // the colour of the scene's directional key light.  Fall back to the local
    // transmittance if the uniform was never pushed.
    vec3 sunLight = (length(sunLightColor) > 1e-4) ? sunLightColor : sunColor.rgb * sunTrans;

    float miePhase = _mieDual(cosTheta, 0.84, 0.50, 0.68);
    // Haze strengthens the forward-scattered glow so the sun blooms into the
    // sky instead of floating as an isolated disc.
    float mieIntensity = turbidity * 0.095 * (1.0 + max(fogDensity, 0.0) * 0.35);
    vec3 mieCol = sunLight * (miePhase * mieIntensity) * sunPower;

    vec2  sunAz    = vec2(sunDir.x, sunDir.y);
    float sunAzLen = max(0.001, length(sunAz));
    float horizDot = dot(vec2(dir.x, dir.y), sunAz / sunAzLen);
    float horizBand = pow(max(0.0, horizDot), 3.0)
                    * max(0.0, 1.0 - abs(sunElevW) * 3.5)
                    * (1.0 - abs(elev) * 2.5)
                    * 0.85
                    * sunPower;
    vec3  horizGlowCol = mix(
        vec3(1.00, 0.48, 0.08),
        vec3(1.00, 0.85, 0.48),
        clamp(sunPower * 1.5, 0.0, 1.0)
    ) * horizBand;

    float ozone     = max(0.0, 1.0 - elevAbs * 1.4);
    vec3  ozoneCol  = vec3(0.00, 0.16, 0.28) * ozone * sunPower;

    float horizonT  = pow(clamp(elev * 1.45 + 0.08, 0.0, 1.0), 0.58);
    vec3  gradCol   = mix(horizonColor, zenithColor, horizonT);

    float sunsetBand = max(0.0, 1.0 - abs(sunElevW) * 2.2) * (1.0 - horizonT * 0.75) * sunPower;
    vec3  sunsetTint = mix(
        vec3(1.00, 0.42, 0.08),
        vec3(0.60, 0.28, 0.78),
        horizonT
    ) * sunsetBand * 0.75;
    gradCol += sunsetTint;

    // Approximate the longer optical path at the horizon.  This adds aerial
    // perspective and the violet anti-solar twilight arch without a costly
    // per-pixel atmosphere integration.
    float airMass = 1.0 / max(0.065, elev + 0.105);
    airMass = clamp(airMass, 1.0, 15.0);
    vec3 extinction = exp(-vec3(0.022, 0.045, 0.105)
                          * airMass * (0.72 + turbidity * 0.085));
    float antiSolar = pow(max(0.0, -horizDot), 2.0)
                    * (1.0 - horizonT) * twilightFactor;
    vec3 twilightArch = mix(vec3(0.18, 0.24, 0.62),
                            vec3(0.58, 0.18, 0.46),
                            horizonT) * antiSolar * 0.38;

    float earthShadowHeight = clamp((-sunElevW * 2.2 + 0.04), 0.0, 0.28);
    float earthShadow = smoothstep(earthShadowHeight + 0.06, earthShadowHeight - 0.02, elev)
                      * max(0.0, -horizDot) * clamp(1.0 - abs(sunElevW) * 4.0, 0.0, 1.0) * sunPower;
    vec3 earthShadowCol = vec3(0.04, 0.06, 0.14) * earthShadow;

    vec3 multiScatter = (ambientColor + fillColor * 0.35) * (0.28 * sunPower * (1.0 - horizonT * 0.4));

    vec3 sky = gradCol
             + rayleighCol  * 0.52
             + mieCol
             + ozoneCol
             + horizGlowCol
             + twilightArch
             + earthShadowCol
             + multiScatter;
    sky = mix(sky, sky * extinction + horizonColor * (1.0 - extinction),
              clamp((1.0 - horizonT) * 0.48, 0.0, 0.48));

    if (length(deptSmogTint) > 0.01) {
        float smogThick = clamp(turbidity * 0.12, 0.0, 1.0);
        sky = mix(sky, deptSmogTint * (1.15 - elev * 0.45), smogThick * 0.65);
    }

    if (elev < 0.0) {
        float below = clamp(-elev * 6.0, 0.0, 1.0);
        sky = mix(sky, horizonColor * 0.75, below);
    }

    // Fog pushes aerial perspective into the sky itself: hazy zones melt the
    // horizon into the fog colour, clear zones stay crisp.  fogDensity == 0
    // reproduces the legacy behaviour exactly.
    float fogAmt = max(fogDensity, 0.0);
    float hazeStr = mix(0.32, 0.16, nightFactor) * (1.0 + fogAmt * 0.9);
    float horizonHaze = exp(-abs(elev) * (15.0 - fogAmt * 6.0)) * hazeStr;
    sky = mix(sky, fogColor, clamp(horizonHaze, 0.0, 0.34 + fogAmt * 0.16));

    if (starBrightness > 0.02 && elev > 0.04) {
        vec3 galNormal = normalize(vec3(0.45, 0.35, 0.82));
        float galDist = abs(dot(dir, galNormal));
        float galBand = exp(-galDist * 8.5);
        vec2 galUV = vec2(atan(dir.x, dir.y) * 4.0, dir.z * 10.0);
        float dust1 = _vnoise(galUV * 2.0 + vec2(1.2, 4.3));
        float dust2 = _vnoise(galUV * 5.0 + vec2(7.8, 2.1));
        float dust = dust1 * 0.65 + dust2 * 0.35;
        float dustLane = clamp(1.25 - dust * 1.5, 0.0, 1.0);
        float galCore = exp(-length(dir - vec3(0.3, -0.4, 0.6)) * 3.5) * 1.5;
        vec3 galColor = mix(vec3(0.65, 0.58, 0.85), vec3(0.95, 0.85, 0.75), galCore);
        float nebGlow = _vnoise(galUV * 3.2 + vec2(4.1, 9.8)) * exp(-galDist * 12.0);
        vec3 nebColor = vec3(0.88, 0.32, 0.65) * nebGlow * 0.45;
        float galIntensity = (galBand * dustLane + galCore) * starBrightness * clamp(1.0 - sunPower * 3.0, 0.0, 1.0) * milkyWayStrength;
        sky += (galColor * galIntensity + nebColor * milkyWayStrength * starBrightness) * 0.38;
    }

    if (auroraEnabled > 0.5 && elev > 0.02 && sunPower < 0.35) {
        float northFactor = clamp(-dir.y * 1.35 + 0.38, 0.0, 1.0);
        float azAngle = atan(dir.x, max(0.001, -dir.y));
        vec2 aurUV = vec2(azAngle * 2.5, (elev - 0.025) / 0.68);
        float waveA = sin(aurUV.x * 2.0 + time * 0.30) * 0.14;
        float waveB = cos(aurUV.x * 4.2 - time * 0.45) * 0.07;
        float waveC = sin(aurUV.x * 7.8 + time * 0.75) * 0.035;
        float curtainCenter = 0.28 + waveA + waveB + waveC;
        float rayHarmonics = sin(aurUV.x * 16.0 + aurUV.y * 2.2 + time * 0.35) * 0.25
                           + cos(aurUV.x * 28.0 - time * 0.50) * 0.15;
        float smoothRays = _vnoise(vec2(aurUV.x * 22.0 + time * 0.20, aurUV.y * 3.5)) * 0.35 + rayHarmonics + 0.50;
        float distToCenter = abs(aurUV.y - curtainCenter);
        float curtain1 = exp(-distToCenter * 7.5) * clamp(smoothRays, 0.35, 1.45);
        float curtainCenter2 = 0.46
                             + sin(aurUV.x * 2.8 - time * 0.22) * 0.10
                             + cos(aurUV.x * 6.2 + time * 0.38) * 0.04;
        float distToCenter2 = abs(aurUV.y - curtainCenter2);
        float rayHarmonics2 = sin(aurUV.x * 22.0 - time * 0.28) * 0.20
                            + _vnoise(vec2(aurUV.x * 32.0 - time * 0.15, aurUV.y * 4.2)) * 0.30 + 0.50;
        float curtain2 = exp(-distToCenter2 * 9.5) * clamp(rayHarmonics2, 0.30, 1.30) * 0.75;
        float totalCurtain = clamp(curtain1 + curtain2, 0.0, 1.50);
        vec3 emerald = vec3(0.06, 0.98, 0.46);
        vec3 cyan    = vec3(0.08, 0.88, 0.98);
        vec3 magenta = vec3(0.88, 0.16, 0.85);
        vec3 violet  = vec3(0.52, 0.18, 0.95);
        float colorGrad = clamp((aurUV.y - curtainCenter + 0.10) * 2.6, 0.0, 1.0);
        vec3 aurColorLow = mix(emerald, cyan, colorGrad * 0.75);
        vec3 aurColorHigh = mix(violet, magenta, colorGrad);
        vec3 aurRGB = mix(aurColorLow, aurColorHigh, colorGrad);
        float lowerFade = smoothstep(0.02, 0.14, elev);
        float upperFade = 1.0 - smoothstep(0.62, 0.92, elev);
        float aurAlpha = totalCurtain * northFactor * lowerFade * upperFade
                       * clamp((0.35 - sunPower) * 3.5, 0.0, 1.0);
        sky += aurRGB * aurAlpha * 1.85;
    }

    float cloudAlpha = 0.0;
    vec3  cloudRGB   = vec3(1.0);

    if (cloudQuality > 0.5 && cloudCoverage > 0.01 && elev > 0.04) {
        vec2 cirrusUV = vec2(dir.x, dir.y) / max(0.12, elev + 0.12) * 0.055
                      + vec2(time * cloudSpeed * 0.00032, time * cloudSpeed * 0.00014);
        float cirWarp = _vnoise(cirrusUV * 3.2);
        float cirDens = _vnoise(cirrusUV * 6.5 + cirWarp) * _vnoise(cirrusUV * 13.0);
        float cirVal = smoothstep(0.18, 0.62, cirDens) * 0.38 * clamp(cloudCoverage * 1.4, 0.0, 1.0);
        vec3 cirCol = mix(vec3(1.0), vec3(1.0, 0.68, 0.45), clamp(1.0 - abs(sunElevW) * 3.2, 0.0, 1.0) * sunPower);
        cirCol = mix(cirCol, ambientColor * 1.5, nightFactor * 0.7);
        sky = mix(sky, cirCol, cirVal * smoothstep(0.04, 0.18, elev));
    }

    if (cloudQuality > 0.5 && cloudCoverage > 0.02 && elev > 0.015) {
        // Never let the planar cloud projection approach its horizon
        // singularity.  Besides stretching detail into broad bands, the old
        // denominator amplified any dark cloud sample into a black smudge.
        float layerScale = 1.0 / max(0.22, elev + 0.20);
        vec2  cloudUV = vec2(dir.x, dir.y) * layerScale * 0.14
                      + vec2(time * cloudSpeed * 0.00115, time * cloudSpeed * 0.00045);

        vec2 warp1 = vec2(
            _fbm(cloudUV * 1.55),
            _fbm(cloudUV * 1.55 + vec2(5.20, 1.30))
        ) * 0.32;

        vec2 warp2 = vec2(
            _vnoise(cloudUV * 2.10 + vec2(1.70, 9.20)),
            _vnoise(cloudUV * 2.10 + vec2(8.30, 2.80))
        ) * 0.12;

        vec2 warpedUV = cloudUV + warp1 + warp2;

        float densityLow = _fbm(warpedUV * 1.35);
        float densityMid = cloudQuality > 1.5
                         ? _fbm(warpedUV * 3.15 + vec2(1.70, 3.10))
                         : _vnoise(warpedUV * 3.15 + vec2(1.70, 3.10));
        float densityFine = _vnoise(warpedUV * 13.0 + vec2(-2.30, 0.80));
        float billowCells = cloudQuality > 1.5
                          ? 1.0 - _cellular(warpedUV * 2.6)
                          : 0.5;
        vec2 highUV = warpedUV + vec2(dir.x, dir.y) * 0.16
                    + vec2(0.37, -0.21);
        float densityHigh = cloudQuality > 2.5
                          ? _fbm(highUV * 2.05)
                          : _vnoise(highUV * 2.05);
        float density = densityLow * 0.44 + densityMid * 0.22
                      + densityHigh * 0.18 + billowCells * 0.16;

        float threshold = mix(0.64, 0.26, cloudCoverage);
        float raw       = density - threshold;
        float edgeWidth = mix(0.24, 0.040, cloudSharpness);
        float shaped    = smoothstep(0.0, edgeWidth, raw);

        if (cloudQuality > 1.5) {
            float edgeErosion = _vnoise(warpedUV * 24.0
                                        + vec2(time * 0.004, -time * 0.002));
            float erodedRaw = raw + (edgeErosion - 0.52) * edgeWidth * 0.72;
            shaped = mix(shaped, smoothstep(-edgeWidth * 0.08,
                                             edgeWidth * 0.92, erodedRaw), 0.42);
        }

        shaped *= smoothstep(0.025, 0.22, elev);

        float volDepth = clamp(densityMid * 0.38 + densityHigh * 0.32
                             + densityFine * 0.14 + billowCells * 0.16, 0.0, 1.0);

        float sunDot    = max(0.0, dot(dir, sunDir));
        float shadowing = clamp(1.0 - shaped * 0.55, 0.28, 1.0);
        vec2 sunPlanar = normalize(vec2(sunDir.x, sunDir.y) + vec2(0.001));
        float lightProbeNear = _vnoise(warpedUV * 7.0 + sunPlanar * 0.18);
        float lightProbeMid  = _vnoise(warpedUV * 7.0 + sunPlanar * 0.52);
        float lightProbeFar  = _vnoise(warpedUV * 7.0 + sunPlanar * 1.08);
        float directionalOcclusion = clamp(
            densityFine * 0.28 + lightProbeNear * 0.34
            + lightProbeMid * 0.24 + lightProbeFar * 0.14, 0.0, 1.0);
        float lightTransport = exp(-directionalOcclusion
                                   * mix(0.35, 2.10, shaped)
                                   * cloudShadowStrength);
        lightTransport = clamp(lightTransport + (lightProbeNear - densityFine) * 0.30,
                               0.16, 1.0);

        float sugarScatter = 1.0 - exp(-volDepth * 2.8);
        lightTransport = mix(lightTransport, 1.0, sugarScatter * 0.32);

        vec3 nightSmogTop = mix(vec3(0.08, 0.10, 0.20), max(zenithColor * 1.75, fogColor * 1.50), min(1.0, length(zenithColor) * 2.0));
        if (length(deptSmogTint) > 0.01) {
            nightSmogTop = mix(nightSmogTop, deptSmogTint * 2.2, 0.75);
        }
        vec3  litTop    = mix(
            mix(nightSmogTop, vec3(2.45, 2.50, 2.60), sunPower),
            max(sunLight, vec3(0.95)) * 2.25,
            0.20 * sunPower
        );

        float depthSh   = mix(0.42, 0.72, volDepth);
        vec3  litBase   = litTop * vec3(depthSh * 0.76, depthSh * 0.86, depthSh * 1.02);
        litBase = mix(litBase, litBase * (ambientColor * 1.8 + fillColor * 0.8), sunPower * 0.55);

        float sunsetC   = max(0.0, 1.0 - abs(sunElevW) * 4.0) * sunPower;
        vec3  sunsetBelly = mix(
            vec3(1.0, 0.58, 0.25),
            vec3(1.0, 0.82, 0.56),
            clamp(sunDot, 0.0, 1.0)
        ) * sunsetC * 0.80;
        litBase += sunsetBelly;

        float billowLight = smoothstep(0.28, 0.82,
                                       densityLow * 0.72 + densityFine * 0.28);
        billowLight = mix(billowLight * 0.75, 1.0, sunDot * 0.26 * sunPower);
        cloudRGB = mix(litBase, litTop,
                       billowLight * shadowing * lightTransport);
        cloudRGB += vec3(0.12, 0.15, 0.20) * densityFine * (1.0 - volDepth) * sunPower;
        float brightCore = smoothstep(edgeWidth * 0.10, edgeWidth * 1.55, raw);
        cloudRGB = mix(cloudRGB, litTop * 1.20, brightCore * 0.62 * sunPower);

        float forwardSilver = _mieDual(dot(dir, sunDir), 0.86, 0.45, 0.75) * 0.18;
        float backGlory     = _mie(dot(dir, sunDir), -0.25) * 0.06;
        float silverEdge = (1.0 - smoothstep(0.0, edgeWidth * 0.75, abs(raw))) * (forwardSilver + backGlory);
        cloudRGB += vec3(1.00, 0.95, 0.76) * silverEdge * sunPower * 1.85;

        if (moonEnabled > 0.5 && sunPower < 0.30) {
            float mnFade  = clamp((0.30 - sunPower) * 4.0, 0.0, 1.0);
            float mnDot   = max(0.0, dot(dir, normalize(moonDir)));
            vec3  mnLight = vec3(0.40, 0.48, 0.70) * mnDot * 0.45;
            float nDepth  = mix(0.08, 0.55, shaped);
            cloudRGB = mix(cloudRGB, vec3(0.06, 0.08, 0.16) + mnLight * shaped,
                           mnFade * nDepth);
        }

        // Clouds retain atmospheric fill even when their directional light is
        // occluded.  This prevents crushed black cores at dusk/night while
        // preserving the modeled silver edges and volumetric depth.
        vec3 nightSmogFill = mix(vec3(0.045, 0.060, 0.115), max(zenithColor * 0.70, fogColor * 0.60), min(1.0, length(zenithColor) * 2.0));
        if (length(deptSmogTint) > 0.01) {
            nightSmogFill = mix(nightSmogFill, deptSmogTint * 0.85, 0.80);
        }
        vec3 cloudFill = mix(nightSmogFill,
                             max(fogColor * 0.38, vec3(0.16, 0.18, 0.22)),
                             sunPower);
        cloudRGB = max(cloudRGB, cloudFill);

        // Aerial perspective: in foggy zones the cloud deck fades into the
        // scene fog colour toward the horizon, so distant clouds read as part
        // of the atmosphere instead of floating geometry.
        if (fogDensity > 0.02) {
            float cloudHaze = exp(-abs(elev) * (5.0 + fogDensity * 18.0))
                            * clamp(fogDensity * 0.85, 0.0, 1.0);
            cloudRGB = mix(cloudRGB, fogColor, clamp(cloudHaze, 0.0, 0.5));
        }

        cloudAlpha = shaped;
    }

    sky = mix(sky, cloudRGB, cloudAlpha);

    if (starBrightness > 0.004 && elev > 0.02) {
        float nearSun = max(0.0, dot(dir, sunDir));
        float celestialFade = max(0.0, 1.0 - nearSun * nearSun * 4.0)
                             * smoothstep(0.02, 0.14, elev)
                             * max(0.0, 1.0 - cloudAlpha * 1.25);
        vec3 fineStars = _starLayer(vUV, 420.0, 17.0);
        vec3 medStars  = _starLayer(vUV + vec2(0.0043, 0.0021), 180.0, 41.0) * 1.35;
        vec3 heroStars = _starLayer(vUV + vec2(0.0017, 0.0), 85.0, 83.0) * 2.10;
        sky += (fineStars + medStars + heroStars) * starBrightness * celestialFade;
    }

    if (sunDiscEnabled > 0.5 && sunElevW > -0.05 && sunPower > 0.01) {
        float angDist = acos(clamp(cosTheta, -1.0, 1.0));
        // Angular radius of the disc in radians.  The base 0.006 rad is a
        // subtly stylised ~0.7 degree sun; sky-sun-size scales it up.
        float sunR    = max(sunAngularRadius, 0.0060);

        float refractFlatten = clamp(1.0 - max(0.0, 0.10 - sunElevW) * 4.5, 0.65, 1.0);
        vec3 sunDirLocal = dir - sunDir;
        float angDistRefract = sqrt(sunDirLocal.x * sunDirLocal.x + (sunDirLocal.z / refractFlatten) * (sunDirLocal.z / refractFlatten) + sunDirLocal.y * sunDirLocal.y * 0.01);

        float limbT   = clamp(1.0 - angDistRefract / sunR, 0.0, 1.0);
        float sunAA   = max(fwidth(angDistRefract) * 1.20, 0.000025);
        float limb    = (1.0 - smoothstep(sunR - sunAA,
                                          sunR + sunAA, angDistRefract))
                      * smoothstep(0.0, 1.0, limbT);
        float limbDrk = 1.0 - 0.65 * (1.0 - sqrt(max(0.0, limbT)));

        // The sun collides with the atmosphere: haze dims the crisp disc and
        // spreads its glow into a soft bloom — a real hazy/setting sun.
        float sunFogAmt  = max(fogDensity, 0.0);
        float sunFogDim  = exp(-sunFogAmt * 2.0);
        float haloSpread = 1.0 + sunFogAmt * 1.1;

        float sunsetT = clamp(1.0 - sunElevW * 5.5, 0.0, 1.0);
        vec3  discCol = mix(
            vec3(1.00, 0.98, 0.92) * 5.5,
            vec3(1.00, 0.44, 0.05) * 3.2,
            sunsetT
        ) * limbDrk * sunPower * sunFogDim;

        float c1 = exp(-angDist * 650.0 / haloSpread) * 0.95;
        float c2 = exp(-angDist * 210.0 / haloSpread) * 0.35;
        float c3 = exp(-angDist *  65.0 / haloSpread) * 0.12;
        float c4 = exp(-angDist *  18.0 / haloSpread) * 0.035;
        vec3  coronaCol = sunLight * (c1 + c2 + c3 + c4)
                        * sunPower * mix(1.0, 1.6, min(1.0, sunFogAmt));

        float streakH = exp(-abs(dir.x - sunDir.x) * 120.0) * exp(-max(0.0, 1.0 - cosTheta) * 140.0) * 0.18;
        float streakV = exp(-abs(dir.z - sunDir.z) * 90.0) * exp(-max(0.0, 1.0 - cosTheta) * 150.0) * 0.28;
        vec3  streakCol = sunLight * (streakH + streakV) * sunPower * sunFogDim;

        float blindAmt  = pow(max(0.0, cosTheta), 55.0) * sunBlindStrength * sunPower;
        vec3  blindCol  = vec3(
            pow(max(0.0, cosTheta), 38.0) * sunBlindStrength * sunPower * 1.35,
            blindAmt * 0.90,
            blindAmt * 0.65
        );
        blindCol *= mix(1.0, 1.5, min(1.0, sunFogAmt));

        float sunVis = (1.0 - cloudAlpha)
                     * clamp((sunElevW + 0.05) * 10.0, 0.0, 1.0) * sunPower;

        sky += (discCol * limb + coronaCol + streakCol + blindCol) * sunVis;
    }

    if (moonEnabled > 0.5) {
        vec3  mDir     = normalize(moonDir);
        float moonDot  = dot(dir, mDir);
        float moonAngle = acos(clamp(moonDot, -1.0, 1.0));
        float moonAA = max(fwidth(moonAngle) * 1.25, 0.00005);
        float moonDisc = 1.0 - smoothstep(moonAngularRadius - moonAA,
                                          moonAngularRadius + moonAA,
                                          moonAngle);

        // Build a stable tangent frame.  normalize(dir - mDir*moonDot) is
        // undefined at the exact moon centre and caused occasional NaN pixels.
        vec3 refAxis = abs(mDir.z) < 0.92 ? vec3(0.0, 0.0, 1.0) : vec3(0.0, 1.0, 0.0);
        vec3 mRight = normalize(cross(refAxis, mDir));
        vec3 mUp = normalize(cross(mDir, mRight));
        vec2 mLocal = vec2(dot(dir, mRight), dot(dir, mUp))
                    / max(0.0001, sin(moonAngularRadius));
        float radiusSq = dot(mLocal, mLocal);
        float sphereZ = sqrt(max(0.0, 1.0 - radiusSq));
        vec3 moonNormal = normalize(vec3(mLocal, sphereZ));

        float broadDetail = _fbm(mLocal * 3.8 + vec2(3.7, 8.1));
        float craterField = _vnoise(mLocal * 17.0 + vec2(30.0, 70.0));
        float craterRims = smoothstep(0.58, 0.72, craterField)
                         - smoothstep(0.72, 0.84, craterField);
        float craterRays = smoothstep(0.75, 0.92, _vnoise(mLocal * 35.0 + vec2(12.0, 45.0))) * 0.18;
        float mDetail = mix(0.72, 1.05, broadDetail) - craterRims * 0.24 + craterRays;
        float mLimb = mix(0.55, 1.0, sphereZ);

        float phaseAngle = moonPhase * 6.28318530;
        vec3 lunarLightDir = normalize(vec3(sin(phaseAngle), 0.08,
                                             -cos(phaseAngle)));
        float phaseLight = smoothstep(-0.035, 0.055,
                                      dot(moonNormal, lunarLightDir));
        vec3  litMoon = vec3(1.00, 0.97, 0.82) * mDetail * mLimb * 1.72;
        vec3  earthshine = vec3(0.055, 0.085, 0.16) * mDetail * mLimb;
        vec3  moonSurf = mix(earthshine, litMoon, phaseLight);

        float haloAngle = max(0.0, moonAngle - moonAngularRadius);
        float mH1 = exp(-haloAngle * 210.0) * 0.12;
        float mH2 = exp(-haloAngle *  52.0) * 0.055;
        float mH3 = exp(-haloAngle *  13.0) * 0.018;
        vec3  moonGlow = moonColor.rgb * (mH1 + mH2 + mH3) * 0.85;

        float halo22 = exp(-pow((haloAngle - 0.384) * 18.0, 2.0)) * 0.045;
        moonGlow += vec3(0.72, 0.85, 1.00) * halo22;

        float moonVis = max(0.0, 1.0 - cloudAlpha * 0.90);
        sky = mix(sky, moonSurf, moonDisc * moonVis);
        sky += moonGlow * moonVis;
    }

    sky *= skyScale.rgb;
    // Smooth filmic display mapping preserves cloud and solar highlight detail
    // instead of clipping every value above one to the same flat white.
    vec3 mappedSky = 1.0 - exp(-max(sky, vec3(0.0)) * skyExposure);
    mappedSky = pow(mappedSky, vec3(0.94));
    float dither = (_hash(gl_FragCoord.xy + vec2(19.0, 73.0)) - 0.5) / 255.0;
    fragColor = vec4(clamp(mappedSky + dither, 0.0, 1.0), 1.0);
}
