#version 120

// Z-up planar water for Panda3D / Toontown.
attribute vec4 p3d_Vertex;
attribute vec3 p3d_Normal;
attribute vec2 p3d_MultiTexCoord0;

uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelMatrix;

uniform float osl_Time;
uniform float osl_WaveScale;
uniform float osl_WaveSpeed;

varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vTexcoord;
varying vec4 vClipPos;
varying float vWaveHeight;

void addWave(in vec2 p, in vec2 k, in float amp, in float speed,
             inout float h, inout vec2 grad) {
    float phase = dot(p, k) + osl_Time * speed * osl_WaveSpeed;
    float s = sin(phase);
    float c = cos(phase);
    h += s * amp;
    grad += c * amp * k;
}

void main() {
    vec4 vertex = p3d_Vertex;
    vec2 p = vertex.xy;

    float h = 0.0;
    vec2 grad = vec2(0.0);
    float scale = max(0.0, osl_WaveScale);

    addWave(p, vec2(0.095, 0.135), 0.070 * scale, 0.85, h, grad);
    addWave(p, vec2(-0.165, 0.105), 0.045 * scale, 1.10, h, grad);
    addWave(p, vec2(0.310, -0.245), 0.022 * scale, 1.55, h, grad);
    addWave(p, vec2(-0.520, -0.380), 0.010 * scale, 2.10, h, grad);

    vertex.z += h;
    vec3 modelNormal = normalize(vec3(-grad.x, -grad.y, 1.0));

    vec4 world = p3d_ModelMatrix * vertex;
    vWorldPos = world.xyz;
    vWorldNormal = normalize(mat3(p3d_ModelMatrix) * modelNormal);
    vTexcoord = p3d_MultiTexCoord0;
    vWaveHeight = h;

    gl_Position = p3d_ModelViewProjectionMatrix * vertex;
    vClipPos = gl_Position;
}
