#version 120

// Depth is written by the fixed pipeline. Alpha testing preserves foliage,
// fence and sign silhouettes instead of casting their complete rectangular
// billboard cards into the high-quality shadow map.
uniform sampler2D p3d_Texture0;

varying vec2 vTexcoord;
varying float vVertexAlpha;

void main() {
    float casterAlpha = texture2D(p3d_Texture0, vTexcoord).a * vVertexAlpha;
    if (casterAlpha < 0.18)
        discard;
    gl_FragColor = vec4(1.0);
}
