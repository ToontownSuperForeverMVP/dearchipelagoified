#version 120

// Stable compositor-free atmospheric sun shafts. No scene texture is sampled.
uniform vec2 sunPos;
uniform vec4 rayColor;
uniform float rayIntensity;
uniform float time;
uniform float aspectRatio;
uniform float fogDensity;   // zone haze: dims and diffuses the shafts
uniform float sunSize;      // projected sun disc radius (fraction of viewport height)

varying vec2 texcoord;

float hash(float n) { return fract(sin(n) * 43758.5453); }

float noise(vec2 x) {
    vec2 p = floor(x);
    vec2 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    float n = p.x + p.y * 57.0;
    return mix(mix(hash(n), hash(n + 1.0), f.x),
               mix(hash(n + 57.0), hash(n + 58.0), f.x), f.y);
}

void main() {
    vec2 uv = texcoord;
    vec2 p = uv;
    vec2 s = sunPos;
    p.x *= aspectRatio;
    s.x *= aspectRatio;

    vec2 delta = p - s;
    float dist = length(delta);
    float angle = atan(delta.y, delta.x);

    float fogAmt = max(fogDensity, 0.0);
    float fogAtten = exp(-fogAmt * 1.7);
    float discR = max(sunSize, 0.006);

    float radial = pow(clamp(1.0 - dist * 0.90, 0.0, 1.0), 2.0);
    float core = exp(-dist * 7.5) * 0.38;

    // Layer broad and fine angular bands for crepuscular-ray structure.
    float bands = 0.52
        + 0.22 * sin(angle * 11.0 + time * 0.055)
        + 0.12 * sin(angle * 23.0 - time * 0.031)
        + 0.07 * sin(angle * 47.0 + time * 0.018);
    bands += (noise(vec2(angle * 3.7, time * 0.012)) - 0.5) * 0.14;
    bands = clamp(bands, 0.10, 1.0);

    float horizonFade = smoothstep(0.0, 0.12, sunPos.y) * smoothstep(1.0, 0.88, sunPos.y);
    float edgeFade = smoothstep(0.0, 0.06, sunPos.x)
                   * smoothstep(1.0, 0.94, sunPos.x)
                   * horizonFade;

    // Soft glow anchored at the sun disc itself so the shafts visibly emanate
    // from the same sun the sky renders.  Haze broadens the glow and dims the
    // crisp core, mimicking light scattering through fog.
    float glow = exp(-dist / max(discR * 8.0, 0.02)) * 0.55
               + exp(-dist * (3.0 + fogAmt * 2.5)) * 0.42;
    vec3 sunGlow = rayColor.rgb * glow * clamp(rayIntensity, 0.0, 2.0);

    float shaft = (radial * bands * 0.62 + core) * clamp(rayIntensity, 0.0, 2.0) * edgeFade;
    // Haze scatters the shafts into diffuse glow rather than crisp beams.
    shaft *= fogAtten * (1.0 + fogAmt * 0.35);
    float alpha = clamp(shaft * 0.46 + glow * 0.10, 0.0, 0.40);
    gl_FragColor = vec4(rayColor.rgb * shaft + sunGlow, alpha);
}
