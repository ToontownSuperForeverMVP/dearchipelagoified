#version 130

uniform sampler2D sceneColor;
uniform sampler2D sceneDepth;
uniform vec2  sunScreenPos;
uniform vec4  rayColor;
uniform float rayIntensity;
uniform float bloomIntensity;
uniform float bloomThreshold;
uniform float exposure;
uniform float tonemapMode;
uniform float contactShadowsEnabled;
uniform float celShadingMode;
uniform float vignetteStrength;
uniform float motionBlurEnabled;
uniform float motionBlurStrength;
uniform mat4  currToPrevMat;
uniform vec4  mbExcludedBounds[16];
uniform vec2  mbExcludedDepth[16];
uniform int   mbNumExcluded;
uniform float time;
uniform vec2  texelSize;
uniform vec2  texScale;

in vec2 uv;
in vec2 screenUV;
out vec4 fragColor;

vec3 acesTonemap(vec3 x) {
    const float a = 2.51;
    const float b = 0.03;
    const float c = 2.43;
    const float d = 0.59;
    const float e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

vec3 reinhardTonemap(vec3 x) {
    return x / (1.0 + x);
}

float luminance(vec3 c) {
    return dot(c, vec3(0.2126, 0.7152, 0.0722));
}

float calcContactShadows(vec2 pUV) {
    float centerDepth = texture(sceneDepth, pUV).r;
    if (centerDepth >= 0.9995) return 1.0;

    float occ = 0.0;
    const int SAMPLES = 8;
    vec2 offsets[8] = vec2[](
        vec2( 1.0,  0.0), vec2(-1.0,  0.0),
        vec2( 0.0,  1.0), vec2( 0.0, -1.0),
        vec2( 0.7,  0.7), vec2(-0.7,  0.7),
        vec2( 0.7, -0.7), vec2(-0.7, -0.7)
    );

    // Work in pixels.  The previous radius multiplied normalized texels by
    // 600, which could jump a third of the screen and turn unrelated depth
    // edges into long, spiky "shadows" as the sun moved.
    float radiusPx = mix(3.0, 1.25, smoothstep(0.15, 0.98, centerDepth));
    float depthEpsilon = max(0.00025, fwidth(centerDepth) * 2.0);

    for (int i = 0; i < SAMPLES; ++i) {
        vec2 sUV = pUV + offsets[i] * texelSize * radiusPx;
        sUV = clamp(sUV, vec2(0.001), texScale - vec2(0.001));
        float sDepth = texture(sceneDepth, sUV).r;
        float diff = centerDepth - sDepth;
        float maxDiff = depthEpsilon * 18.0;
        if (diff > depthEpsilon && diff < maxDiff) {
            occ += 1.0 - smoothstep(depthEpsilon, maxDiff, diff);
        }
    }
    occ /= float(SAMPLES);
    return clamp(1.0 - occ * 0.62, 0.72, 1.0);
}

float calcToonOutlines(vec2 pUV) {
    float centerDepth = texture(sceneDepth, pUV).r;
    if (centerDepth >= 0.9995) return 1.0;

    vec2 offsets[4] = vec2[](
        vec2( 1.0,  0.0), vec2(-1.0,  0.0),
        vec2( 0.0,  1.0), vec2( 0.0, -1.0)
    );
    float depthDiff = 0.0;
    for (int i = 0; i < 4; ++i) {
        vec2 sUV = pUV + offsets[i] * texelSize * 1.5;
        sUV = clamp(sUV, vec2(0.001), texScale - vec2(0.001));
        float sDepth = texture(sceneDepth, sUV).r;
        depthDiff += abs(centerDepth - sDepth);
    }
    float edge = smoothstep(0.0006, 0.0040, depthDiff);
    return clamp(1.0 - edge * 0.65, 0.35, 1.0);
}

vec3 godRays(vec2 pixelUV, float intensity) {
    if (intensity <= 0.001) return vec3(0.0);

    const int   NUM_SAMPLES     = 64;
    const float DECAY           = 0.965;
    const float DENSITY         = 0.82;
    // Keep shafts as a localized accent.  The previous accumulation constants
    // could add nearly a full unit of white over most of the frame, which made
    // the sky and distant scenery look fogged out.
    const float WEIGHT          = 0.18;
    const float EXPOSURE_RAY    = 0.040;
    const float DEPTH_THRESHOLD = 0.9995;

    vec2 sunEdgeDist = min(sunScreenPos, 1.0 - sunScreenPos);
    float edgeFade   = smoothstep(0.0, 0.08, min(sunEdgeDist.x, sunEdgeDist.y));
    if (edgeFade <= 0.0) return vec3(0.0);

    vec2  sunUV   = sunScreenPos * texScale;
    float sunVisible = step(DEPTH_THRESHOLD,
                            texture(sceneDepth, clamp(sunUV, vec2(0.001),
                                                      texScale - vec2(0.001))).r);
    if (sunVisible <= 0.0) return vec3(0.0);

    vec2  delta   = (pixelUV - sunUV) * (DENSITY / float(NUM_SAMPLES));
    vec2  sampleUV = pixelUV;
    float illum    = 0.0;
    float decay    = 1.0;

    for (int i = 0; i < NUM_SAMPLES; ++i) {
        sampleUV -= delta;
        vec2 cUV = clamp(sampleUV, vec2(0.001), texScale - vec2(0.001));
        float d   = texture(sceneDepth, cUV).r;
        float sky = step(DEPTH_THRESHOLD, d);
        illum    += sky * decay * WEIGHT;
        decay    *= DECAY;
    }
    // Fade rays away from the sun so open sky cannot become a full-screen veil.
    float radial = clamp(1.0 - length((pixelUV - sunUV) / texScale) * 1.25,
                         0.0, 1.0);
    radial *= radial;
    illum *= EXPOSURE_RAY * intensity * edgeFade * sunVisible * radial;

    return rayColor.rgb * illum;
}

vec3 bloom(vec2 pixUV, float threshold, float intensity) {
    if (intensity <= 0.001) return vec3(0.0);

    vec3 acc = vec3(0.0);
    float total = 0.0;

    float blurRadius = mix(2.5, 7.5, intensity);

    vec2 offsets[12] = vec2[](
        vec2( 1.0,  0.0), vec2(-1.0,  0.0),
        vec2( 0.0,  1.0), vec2( 0.0, -1.0),
        vec2( 1.5,  1.5), vec2(-1.5,  1.5),
        vec2( 1.5, -1.5), vec2(-1.5, -1.5),
        vec2( 3.0,  0.0), vec2(-3.0,  0.0),
        vec2( 0.0,  3.0), vec2( 0.0, -3.0)
    );

    float weights[12] = float[](
        1.00, 1.00, 1.00, 1.00,
        0.70, 0.70, 0.70, 0.70,
        0.35, 0.35, 0.35, 0.35
    );

    for (int i = 0; i < 12; ++i) {
        vec2  sUV   = pixUV + offsets[i] * texelSize * blurRadius;
        sUV = clamp(sUV, vec2(0.001), texScale - vec2(0.001));
        vec3  col   = texture(sceneColor, sUV).rgb;
        float lum   = luminance(col);
        float bright = max(0.0, lum - threshold);
        acc   += col * bright * weights[i];
        total += weights[i];
    }
    if (total > 0.0) acc /= total;

    return acc * intensity * 1.5;
}

float vignette(vec2 u, float strength) {
    vec2 d = u - 0.5;
    return 1.0 - dot(d, d) * strength * 3.2;
}

void main() {
    vec3 col;
    if (motionBlurEnabled > 0.5 && motionBlurStrength > 0.001) {
        float centerDepth = texture(sceneDepth, uv).r;
        bool isExcluded = false;
        if (centerDepth < 0.9995) {
            for (int k = 0; k < mbNumExcluded; ++k) {
                vec4 b = mbExcludedBounds[k];
                vec2 d = mbExcludedDepth[k];
                if (screenUV.x >= b.x && screenUV.x <= b.z && screenUV.y >= b.y && screenUV.y <= b.w &&
                    centerDepth >= d.x && centerDepth <= d.y) {
                    isExcluded = true;
                    break;
                }
            }
        }
        if (centerDepth < 0.9995 && !isExcluded) {
            vec4 currentNDC = vec4(screenUV * 2.0 - 1.0, centerDepth * 2.0 - 1.0, 1.0);
            vec4 prevNDC = currToPrevMat * currentNDC;
            if (prevNDC.w != 0.0) {
                prevNDC /= prevNDC.w;
                vec2 velocity = (currentNDC.xy - prevNDC.xy) * 0.5 * texScale * motionBlurStrength;
                float maxVel = 0.055 * length(texScale);
                float velLen = length(velocity);
                if (velLen > maxVel) {
                    velocity = (velocity / velLen) * maxVel;
                }
                if (velLen > 0.0003) {
                    const int MB_SAMPLES = 10;
                    vec3 mbAcc = vec3(0.0);
                    for (int i = 0; i < MB_SAMPLES; ++i) {
                        float t = float(i) / float(MB_SAMPLES - 1) - 0.5;
                        vec2 sUV = clamp(uv + velocity * t, vec2(0.001), texScale - vec2(0.001));
                        mbAcc += texture(sceneColor, sUV).rgb;
                    }
                    col = (mbAcc / float(MB_SAMPLES)) * exposure;
                } else {
                    col = texture(sceneColor, uv).rgb * exposure;
                }
            } else {
                col = texture(sceneColor, uv).rgb * exposure;
            }
        } else {
            col = texture(sceneColor, uv).rgb * exposure;
        }
    } else {
        col = texture(sceneColor, uv).rgb * exposure;
    }

    if (contactShadowsEnabled > 0.5) {
        float cs = calcContactShadows(uv);
        col *= cs;
    }

    if (celShadingMode > 0.5) {
        float edge = calcToonOutlines(uv);
        col *= edge;
    }

    col += godRays(uv, rayIntensity);
    col += bloom(uv, bloomThreshold, bloomIntensity);

    if (tonemapMode < 0.5) {
        col = acesTonemap(col);
    } else if (tonemapMode < 1.5) {
        col = reinhardTonemap(col);
    } else {
        col = clamp(col, 0.0, 1.0);
    }

    if (vignetteStrength > 0.001) {
        col *= clamp(vignette(screenUV, vignetteStrength), 0.0, 1.0);
    }

    fragColor = vec4(col, 1.0);
}
