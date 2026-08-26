#version 120

uniform sampler2D osl_ReflectionTex;
uniform vec4  osl_WaterColor;
uniform vec4  osl_SunColor;
uniform vec3  osl_SkyReflectionColor;
uniform vec3  osl_SunDir;
uniform vec3  osl_CameraPos;
uniform float osl_Time;
uniform float osl_WaveScale;
uniform float osl_WaveSpeed;
uniform float osl_FresnelPower;
uniform float osl_Roughness;
uniform float osl_ReflectionStrength;
uniform float osl_ReflectionAvailable;

varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vTexcoord;
varying vec4 vClipPos;
varying float vWaveHeight;

float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}

float noise2(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash21(i), hash21(i + vec2(1.0, 0.0)), f.x),
               mix(hash21(i + vec2(0.0, 1.0)), hash21(i + vec2(1.0, 1.0)), f.x), f.y);
}

void main() {
    vec3 V = normalize(osl_CameraPos - vWorldPos);
    vec3 N = normalize(vWorldNormal);

    // Fine ripples only perturb shading/reflection; geometry carries the broad waves.
    vec2 rippleUV = vWorldPos.xy * 0.18 + vec2(osl_Time * 0.020, -osl_Time * 0.016) * osl_WaveSpeed;
    float n1 = noise2(rippleUV * 2.1);
    float n2 = noise2(rippleUV.yx * 3.7 + vec2(7.3, 2.1));
    vec2 ripple = (vec2(n1, n2) - 0.5) * (0.10 + 0.035 * osl_WaveScale);
    N = normalize(N + vec3(ripple, 0.0));

    float NoV = clamp(dot(N, V), 0.0, 1.0);
    float fresnel = 0.02 + 0.98 * pow(1.0 - NoV, max(1.0, osl_FresnelPower));

    vec2 screenUV = (vClipPos.xy / max(0.0001, vClipPos.w)) * 0.5 + 0.5;
    float distortion = mix(0.010, 0.003, clamp(osl_Roughness, 0.0, 1.0));
    screenUV += N.xy * distortion + ripple * 0.006;
    screenUV = clamp(screenUV, vec2(0.002), vec2(0.998));
    vec3 planarReflection = texture2D(osl_ReflectionTex, screenUV).rgb;
    vec3 analyticSkyReflection = mix(osl_SkyReflectionColor * 0.42,
                                     osl_SkyReflectionColor * 1.18,
                                     pow(1.0 - NoV, 0.55));
    vec3 reflection = mix(analyticSkyReflection, planarReflection,
                          clamp(osl_ReflectionAvailable, 0.0, 1.0));

    // Deep/near-normal views favor the zone water colour; grazing angles reflect the sky.
    vec3 deepColor = osl_WaterColor.rgb * mix(0.58, 0.95, NoV);
    vec3 base = mix(deepColor, reflection,
                    clamp(fresnel * osl_ReflectionStrength, 0.0, 0.96));

    vec3 L = normalize(-osl_SunDir);
    vec3 H = normalize(L + V);
    float sunFacing = max(0.0, dot(N, L));
    float gloss = mix(96.0, 18.0, clamp(osl_Roughness, 0.0, 1.0));
    float specular = pow(max(0.0, dot(N, H)), gloss) * sunFacing;
    base += osl_SunColor.rgb * specular * (1.25 - 0.70 * osl_Roughness);

    // Tiny crest sparkle adds motion without fake screen-space caustics.
    float crest = smoothstep(0.035, 0.085, abs(vWaveHeight));
    float sparkle = pow(max(0.0, 1.0 - abs(n1 - n2) * 2.0), 8.0) * crest;
    base += vec3(0.10, 0.14, 0.16) * sparkle;

    float alpha = clamp(osl_WaterColor.a + fresnel * 0.22, 0.18, 0.98);
    gl_FragColor = vec4(max(base, vec3(0.0)), alpha);
}
