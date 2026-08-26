// Vertex shader for the HDR scene composite quad rendered through a
// 3D orthographic FilterManager camera.  Unlike the render2dp overlay
// path (sunrays.vert.glsl), the quad lives in model-space (XZ plane,
// Y=0), so the full ModelViewProjection transform is required to map it
// to clip space correctly.
#version 130

uniform mat4 p3d_ModelViewProjectionMatrix;
uniform vec2 texScale;
in  vec4 p3d_Vertex;
in  vec2 p3d_MultiTexCoord0;
out vec2 uv;
out vec2 screenUV;

void main() {
    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;
    screenUV = p3d_MultiTexCoord0;
    uv = p3d_MultiTexCoord0 * texScale;
}
