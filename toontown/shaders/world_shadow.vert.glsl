#version 120

// Stable outdoor receiver shader.  Lighting is evaluated entirely in world
// space so orbital-camera transforms cannot alter light directions/positions.

attribute vec4 p3d_Vertex;
attribute vec3 p3d_Normal;
attribute vec2 p3d_MultiTexCoord0;
attribute vec4 p3d_Color;

uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelMatrix;
uniform mat4 osl_ShadowMatrix;

varying vec4 vVertexColor;
varying vec2 vTexcoord;
varying vec4 vShadow;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;

void main() {
    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;
    vTexcoord = p3d_MultiTexCoord0;
    vVertexColor = p3d_Color;
    vec4 worldPos = p3d_ModelMatrix * p3d_Vertex;
    vec3 worldNormal = normalize(mat3(p3d_ModelMatrix) * p3d_Normal);
    vWorldPos = worldPos.xyz;
    vWorldNormal = worldNormal;
    // Push receivers slightly off their source surface.  Legacy DNA buildings
    // frequently contain overlapping facade/roof triangles that otherwise
    // create long needle-shaped self shadows.
    worldPos.xyz += worldNormal * 0.15;
    vShadow = osl_ShadowMatrix * worldPos;
}
