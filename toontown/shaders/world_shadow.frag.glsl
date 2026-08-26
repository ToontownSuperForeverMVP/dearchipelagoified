#version 120

// Stable world-space outdoor lighting with crisp Gaussian-filtered shadows.
// The receiver stays attached for the complete zone lifetime. Day/night only
// changes numeric inputs, which is essential for flattened legacy DNA batches.

uniform sampler2D p3d_Texture0;
uniform sampler2D osl_ShadowMap;
uniform vec4 p3d_ColorScale;
uniform vec3 osl_SunDirWorld;
uniform vec3 osl_SunColor;
uniform vec3 osl_Ambient;
uniform vec3 osl_SkyAmbient;
uniform vec3 osl_GroundAmbient;
uniform vec3 osl_WorldGrade;
uniform vec3 osl_CameraPosWorld;
uniform vec3 osl_FogColor;
uniform vec3 osl_FogParams;
uniform float osl_FogMode;
uniform vec3 osl_LampPosWorld0;
uniform vec3 osl_LampPosWorld1;
uniform vec3 osl_LampPosWorld2;
uniform vec3 osl_LampPosWorld3;
uniform vec3 osl_LampGroundPos0;
uniform vec3 osl_LampGroundPos1;
uniform vec3 osl_LampGroundPos2;
uniform vec3 osl_LampGroundPos3;
uniform vec3 osl_LampColor0;
uniform vec3 osl_LampColor1;
uniform vec3 osl_LampColor2;
uniform vec3 osl_LampColor3;
uniform float osl_PointShadowOn;
uniform vec2 osl_ShadowTexel;
uniform float osl_ShadowOn;
uniform float osl_ShadowFloor;
uniform float osl_ShadowFilterRadius;
uniform float osl_ShadowBias;
uniform float osl_SpecularStrength;
uniform float osl_CelShadingMode;
uniform float osl_DebugMode;

varying vec4 vVertexColor;
varying vec2 vTexcoord;
varying vec4 vShadow;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;

float shadowTap(vec2 uv, float receiverDepth, float bias) {
    float storedDepth = texture2D(osl_ShadowMap, uv).r;
    float transition = max(0.00016, bias * 0.22);
    return smoothstep(receiverDepth - bias - transition,
                      receiverDepth - bias + transition,
                      storedDepth);
}

float filteredShadow(vec2 uv, float receiverDepth, float ndl) {
    vec2 radius = osl_ShadowTexel * osl_ShadowFilterRadius;
    float slopeBias = osl_ShadowBias * mix(2.4, 1.0, clamp(ndl * 3.0, 0.0, 1.0));
    float shadow = shadowTap(uv, receiverDepth, slopeBias) * 3.5;
    shadow += shadowTap(uv + vec2(-radius.x, 0.0), receiverDepth, slopeBias) * 2.0;
    shadow += shadowTap(uv + vec2( radius.x, 0.0), receiverDepth, slopeBias) * 2.0;
    shadow += shadowTap(uv + vec2(0.0, -radius.y), receiverDepth, slopeBias) * 2.0;
    shadow += shadowTap(uv + vec2(0.0,  radius.y), receiverDepth, slopeBias) * 2.0;
    shadow += shadowTap(uv + vec2(-radius.x * 0.707, -radius.y * 0.707), receiverDepth, slopeBias) * 1.5;
    shadow += shadowTap(uv + vec2( radius.x * 0.707, -radius.y * 0.707), receiverDepth, slopeBias) * 1.5;
    shadow += shadowTap(uv + vec2(-radius.x * 0.707,  radius.y * 0.707), receiverDepth, slopeBias) * 1.5;
    shadow += shadowTap(uv + vec2( radius.x * 0.707,  radius.y * 0.707), receiverDepth, slopeBias) * 1.5;
    shadow += shadowTap(uv + vec2(-radius.x * 1.45, 0.0), receiverDepth, slopeBias) * 0.75;
    shadow += shadowTap(uv + vec2( radius.x * 1.45, 0.0), receiverDepth, slopeBias) * 0.75;
    shadow += shadowTap(uv + vec2(0.0, -radius.y * 1.45), receiverDepth, slopeBias) * 0.75;
    shadow += shadowTap(uv + vec2(0.0,  radius.y * 1.45), receiverDepth, slopeBias) * 0.75;
    return shadow / 20.0;
}

vec3 lampContribution(vec3 lampPos, vec3 lampGroundPos, vec3 lampColor, vec3 normal, vec3 viewDir) {
    vec3 delta = lampPos - vWorldPos;
    float distanceToLamp = length(delta);
    if (distanceToLamp >= 24.0)
        return vec3(0.0);

    vec3 lightDir = delta / max(distanceToLamp, 0.001);
    float ndl = dot(normal, lightDir);
    float wrappedNdl = clamp((ndl + 0.22) / 1.22, 0.0, 1.0);
    if (osl_CelShadingMode > 0.5) {
        wrappedNdl = smoothstep(0.12, 0.36, wrappedNdl);
    }

    float heightDiff = vWorldPos.z - lampPos.z;
    float hoodOcclusion = 1.0 - smoothstep(0.35, 2.4, heightDiff);

    float contactShadow = 1.0;
    if (osl_PointShadowOn > 0.5 && lampGroundPos.z < lampPos.z - 0.5) {
        vec2 groundDelta = vWorldPos.xy - lampGroundPos.xy;
        float groundDist = length(groundDelta);
        float poleBaseShadow = smoothstep(0.35, 1.25, groundDist);
        contactShadow = poleBaseShadow;
    }

    float pointShadow = wrappedNdl * hoodOcclusion * contactShadow;
    float rangeFade = 1.0 - smoothstep(16.0, 24.0, distanceToLamp);
    float attenuation = rangeFade / (1.2 + 0.06 * distanceToLamp + 0.008 * distanceToLamp * distanceToLamp);

    vec3 halfVector = normalize(lightDir + viewDir);
    float specNdh = max(dot(normal, halfVector), 0.0);
    float highlight;
    if (osl_CelShadingMode > 0.5) {
        highlight = smoothstep(0.85, 0.92, specNdh) * osl_SpecularStrength * 0.35;
    } else {
        highlight = pow(specNdh, 32.0) * osl_SpecularStrength * 0.25;
    }

    return min(lampColor * attenuation * (pointShadow + highlight * pointShadow), vec3(1.45));
}

void main() {
    vec4 texel = texture2D(p3d_Texture0, vTexcoord);
    vec3 normal = normalize(vWorldNormal);
    if (!gl_FrontFacing)
        normal = -normal;

    vec3 sunDir = normalize(osl_SunDirWorld);
    vec3 viewDir = normalize(osl_CameraPosWorld - vWorldPos);
    float ndl = max(dot(normal, sunDir), 0.0);
    float shadow = 1.0;
    vec3 dbgCoords = vec3(0.0);
    float dbgDepth = 1.0;

    if (osl_ShadowOn > 0.001) {
        vec3 ndc = vShadow.xyz / max(abs(vShadow.w), 1.0e-5);
        vec3 p = ndc * 0.5 + 0.5;
        dbgCoords = p;
        if (p.x >= 0.0 && p.x <= 1.0 && p.y >= 0.0 && p.y <= 1.0
                && p.z >= 0.0 && p.z <= 1.0) {
            float rawShadow = filteredShadow(p.xy, p.z, ndl);
            float borderFade = clamp(min(min(p.x, 1.0 - p.x), min(p.y, 1.0 - p.y)) * 8.0, 0.0, 1.0);
            shadow = mix(1.0, rawShadow, borderFade);
            dbgDepth = texture2D(osl_ShadowMap, p.xy).r;
        }
    }

    if (osl_DebugMode > 2.5) {
        gl_FragColor = vec4(vec3(dbgDepth), 1.0);
        return;
    }
    if (osl_DebugMode > 1.5) {
        gl_FragColor = vec4(dbgCoords, 1.0);
        return;
    }
    if (osl_DebugMode > 0.5) {
        gl_FragColor = vec4(vec3(shadow), 1.0);
        return;
    }

    float directShadow = mix(1.0, mix(osl_ShadowFloor, 1.0, shadow), osl_ShadowOn);
    if (osl_CelShadingMode > 0.5 && osl_ShadowOn > 0.5) {
        directShadow = mix(osl_ShadowFloor, 1.0, smoothstep(0.35, 0.65, shadow));
    }
    float hemisphere = smoothstep(-0.40, 0.80, normal.z);
    vec3 ambient = mix(osl_GroundAmbient, osl_SkyAmbient, hemisphere);
    ambient = mix(osl_Ambient, ambient, 0.68) * osl_WorldGrade;
    float ambientVisibility = mix(1.0, mix(0.78, 1.0, shadow), osl_ShadowOn);
    ambient *= ambientVisibility;

    float toonWrap = clamp((dot(normal, sunDir) + 0.38) / 1.38, 0.0, 1.0);
    float diffuseTerm;
    if (osl_CelShadingMode > 0.5) {
        float d1 = smoothstep(0.08, 0.14, toonWrap) * 0.28;
        float d2 = smoothstep(0.36, 0.42, toonWrap) * 0.36;
        float d3 = smoothstep(0.66, 0.72, toonWrap) * 0.36;
        diffuseTerm = d1 + d2 + d3;
    } else {
        diffuseTerm = pow(smoothstep(0.04, 0.96, toonWrap), 0.88);
    }
    vec3 direct = osl_SunColor * diffuseTerm * directShadow;
    vec3 halfVector = normalize(sunDir + viewDir);
    float sunSpec;
    if (osl_CelShadingMode > 0.5) {
        sunSpec = smoothstep(0.88, 0.93, max(dot(normal, halfVector), 0.0))
                * osl_SpecularStrength * 0.45;
    } else {
        sunSpec = smoothstep(0.70, 0.94, max(dot(normal, halfVector), 0.0))
                * pow(max(ndl, 0.0), 0.25) * osl_SpecularStrength * 0.35;
    }
    direct += osl_SunColor * sunSpec * directShadow;

    vec3 lamps = lampContribution(osl_LampPosWorld0, osl_LampGroundPos0, osl_LampColor0, normal, viewDir)
               + lampContribution(osl_LampPosWorld1, osl_LampGroundPos1, osl_LampColor1, normal, viewDir)
               + lampContribution(osl_LampPosWorld2, osl_LampGroundPos2, osl_LampColor2, normal, viewDir)
               + lampContribution(osl_LampPosWorld3, osl_LampGroundPos3, osl_LampColor3, normal, viewDir);

    float grazing = pow(1.0 - max(dot(normal, viewDir), 0.0), 3.2);
    vec3 skyRim = osl_SkyAmbient * grazing * 0.075;
    vec3 light = min(ambient + direct + lamps + skyRim, vec3(1.35));
    vec4 baseColor = texel * vVertexColor * p3d_ColorScale;
    vec3 shadedColor = baseColor.rgb * light;

    // World-space aerial perspective keeps the explicit receiver visually tied
    // to the procedural horizon. A gentle height term gives streets and valleys
    // depth without the hard horizontal boundary of a traditional height fog.
    if (osl_FogMode > 0.5) {
        float viewDistance = length(osl_CameraPosWorld - vWorldPos);
        float heightDensity = mix(1.30, 0.72,
                                  smoothstep(0.0, 55.0, vWorldPos.z));
        float fogAmount;
        if (osl_FogMode > 1.5) {
            fogAmount = 1.0 - exp(-viewDistance * osl_FogParams.z
                                  * heightDensity);
        } else {
            float adjustedDistance = viewDistance * heightDensity;
            fogAmount = smoothstep(osl_FogParams.x, osl_FogParams.y,
                                   adjustedDistance);
        }
        fogAmount = clamp(fogAmount, 0.0, 0.94);
        float forwardScatter = pow(max(dot(-viewDir, sunDir), 0.0), 12.0)
                             * (0.65 + shadow * 0.35);
        vec3 aerialColor = osl_FogColor
                         + osl_SunColor * forwardScatter * 0.10;
        shadedColor = mix(shadedColor, aerialColor, fogAmount);
    }

    gl_FragColor = vec4(shadedColor, baseColor.a);
}
