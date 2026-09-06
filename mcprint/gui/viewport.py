"""3D preview widget (QOpenGLWidget, flat-shaded triangles with per-face colors, orbit camera).

Uses only Qt's OpenGL wrappers (no PyOpenGL): QOpenGLShaderProgram + QOpenGLBuffer + the
QOpenGLFunctions object of the current context.  GLSL 1.20 keeps it working on old drivers.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMatrix4x4, QOpenGLContext, QSurfaceFormat, QVector3D
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..voxel.mesher import MeshSet

# OpenGL constants (avoid a PyOpenGL dependency)
GL_DEPTH_TEST = 0x0B71
GL_CULL_FACE = 0x0B44
GL_BACK = 0x0405
GL_TRIANGLES = 0x0004
GL_LINES = 0x0001
GL_FLOAT = 0x1406
GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_MULTISAMPLE = 0x809D

VERT = """
#version 120
attribute vec3 a_pos;
attribute vec3 a_nrm;
attribute vec4 a_col;
uniform mat4 u_mvp;
uniform mat4 u_model;
varying vec3 v_nrm;
varying vec4 v_col;
void main() {
    gl_Position = u_mvp * vec4(a_pos, 1.0);
    v_nrm = mat3(u_model) * a_nrm;
    v_col = a_col;
}
"""
FRAG = """
#version 120
varying vec3 v_nrm;
varying vec4 v_col;
uniform vec3 u_light;
void main() {
    vec3 n = normalize(v_nrm);
    float diff = max(dot(n, normalize(u_light)), 0.0);
    float diff2 = max(dot(n, normalize(vec3(-0.4, -0.6, 0.5))), 0.0) * 0.35;
    float l = 0.38 + 0.62 * diff + diff2;
    gl_FragColor = vec4(v_col.rgb * min(l, 1.15), v_col.a);
}
"""
LINE_VERT = """
#version 120
attribute vec3 a_pos;
attribute vec3 a_col;
uniform mat4 u_mvp;
varying vec3 v_col;
void main() { gl_Position = u_mvp * vec4(a_pos, 1.0); v_col = a_col; }
"""
LINE_FRAG = """
#version 120
varying vec3 v_col;
void main() { gl_FragColor = vec4(v_col, 1.0); }
"""


def default_surface_format() -> QSurfaceFormat:
    fmt = QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    return fmt


def _perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    m = np.zeros((4, 4), dtype=np.float64)
    m[0, 0] = f / max(aspect, 1e-6)
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def _look_at(eye: np.ndarray, target: np.ndarray, up: np.ndarray) -> np.ndarray:
    f = target - eye
    f = f / (np.linalg.norm(f) or 1)
    s = np.cross(f, up)
    s = s / (np.linalg.norm(s) or 1)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3] = s
    m[1, :3] = u
    m[2, :3] = -f
    m[0, 3] = -np.dot(s, eye)
    m[1, 3] = -np.dot(u, eye)
    m[2, 3] = np.dot(f, eye)
    return m


def _qmat(m: np.ndarray) -> QMatrix4x4:
    return QMatrix4x4(*[float(v) for v in m.reshape(-1)])


class MeshViewport(QOpenGLWidget):
    """Shows a MeshSet (millimetres, Z up) plus a printer bed outline."""

    picked_material = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFormat(default_surface_format())
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.StrongFocus)
        self._meshes: Optional[MeshSet] = None
        self._vbo_data: Optional[np.ndarray] = None    # interleaved pos(3) nrm(3) col(4) float32
        self._count = 0
        self._opaque_count = 0
        self._dirty = False
        self._prog: Optional[QOpenGLShaderProgram] = None
        self._line_prog: Optional[QOpenGLShaderProgram] = None
        self._vbo: Optional[QOpenGLBuffer] = None
        self._line_vbo: Optional[QOpenGLBuffer] = None
        self._line_count = 0
        self._line_dirty = True
        self.gl = None
        # camera
        self.yaw = 35.0
        self.pitch = 28.0
        self.distance = 200.0
        self.target = np.array([0.0, 0.0, 0.0])
        self._last: Optional[QPoint] = None
        self._button = None
        # scene
        self.bed_mm: Optional[tuple[float, float, float]] = None
        self.show_bed = True
        self.show_axes = True
        self.wireframe = False
        self.background = (0.16, 0.17, 0.20)
        self.hidden_materials: set[int] = set()

    # ---- data ------------------------------------------------------------------------
    def set_meshes(self, meshes: Optional[MeshSet], fit: bool = True) -> None:
        self._meshes = meshes
        self._rebuild_vertex_data()
        if fit and meshes is not None and meshes.meshes:
            self.fit_view()
        self.update()

    def set_bed(self, bed_mm: Optional[tuple[float, float, float]]) -> None:
        self.bed_mm = bed_mm
        self._line_dirty = True
        self.update()

    def set_hidden_materials(self, hidden: set[int]) -> None:
        self.hidden_materials = set(hidden)
        self._rebuild_vertex_data()
        self.update()

    def _rebuild_vertex_data(self) -> None:
        ms = self._meshes
        if ms is None or not ms.meshes:
            self._vbo_data = None
            self._count = 0
            self._dirty = True
            return
        chunks = []
        trans_chunks = []
        for m in ms.meshes:
            if m.material in self.hidden_materials or len(m.triangles) == 0:
                continue
            v = m.vertices.astype(np.float32)
            t = m.triangles
            a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
            n = np.cross(b - a, c - a)
            ln = np.linalg.norm(n, axis=1)
            ln[ln == 0] = 1
            n = (n / ln[:, None]).astype(np.float32)
            info = ms.materials.get(m.material, {})
            translucent = bool(info.get("translucent"))
            col = np.asarray(list(info.get("color", m.color))[:3] + [0.55 * 255 if translucent else 255.0], dtype=np.float32) / 255.0
            tri = np.stack([a, b, c], axis=1)                        # (T,3,3)
            nrm = np.repeat(n[:, None, :], 3, axis=1)                # (T,3,3)
            cols = np.broadcast_to(col, (tri.shape[0], 3, 4)).astype(np.float32)
            data = np.concatenate([tri, nrm, cols], axis=2).reshape(-1, 10)
            (trans_chunks if translucent else chunks).append(data)
        if not chunks and not trans_chunks:
            self._vbo_data = None
            self._count = 0
            self._opaque_count = 0
        else:
            parts = chunks + trans_chunks
            self._vbo_data = np.ascontiguousarray(np.concatenate(parts, axis=0), dtype=np.float32)
            self._count = len(self._vbo_data)
            self._opaque_count = int(sum(len(c) for c in chunks))
        self._dirty = True

    # ---- camera ----------------------------------------------------------------------
    def fit_view(self) -> None:
        if self._meshes is None or not self._meshes.meshes:
            if self.bed_mm:
                bx, by, bz = self.bed_mm
                self.target = np.array([bx / 2, by / 2, 0.0])
                self.distance = max(bx, by) * 1.6
            return
        lo, hi = self._meshes.bounds()
        centre = (lo + hi) / 2
        size = float(np.linalg.norm(hi - lo)) or 10.0
        self.target = centre.astype(np.float64)
        self.distance = size * 1.4
        self.update()

    def set_view(self, name: str) -> None:
        presets = {"iso": (35.0, 28.0), "top": (0.0, 89.9), "front": (0.0, 0.0), "back": (180.0, 0.0),
                   "left": (-90.0, 0.0), "right": (90.0, 0.0), "bottom": (0.0, -89.9)}
        if name in presets:
            self.yaw, self.pitch = presets[name]
            self.update()

    def _eye(self) -> np.ndarray:
        yaw = math.radians(self.yaw)
        pitch = math.radians(self.pitch)
        d = self.distance
        return self.target + d * np.array([math.sin(yaw) * math.cos(pitch), -math.cos(yaw) * math.cos(pitch), math.sin(pitch)])

    def _mvp(self) -> tuple[np.ndarray, np.ndarray]:
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        near = max(self.distance / 500.0, 0.05)
        far = self.distance * 20 + 1000
        proj = _perspective(40.0, w / h, near, far)
        view = _look_at(self._eye(), self.target, np.array([0.0, 0.0, 1.0]))
        return proj @ view, np.eye(4)

    # ---- GL ----------------------------------------------------------------------------
    def initializeGL(self) -> None:
        ctx = QOpenGLContext.currentContext()
        self.gl = ctx.functions()
        self.gl.initializeOpenGLFunctions()
        self._prog = QOpenGLShaderProgram(self)
        ok = self._prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT) and \
            self._prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG) and self._prog.link()
        if not ok:
            raise RuntimeError("shader compile failed: " + self._prog.log())
        self._line_prog = QOpenGLShaderProgram(self)
        self._line_prog.addShaderFromSourceCode(QOpenGLShader.Vertex, LINE_VERT)
        self._line_prog.addShaderFromSourceCode(QOpenGLShader.Fragment, LINE_FRAG)
        self._line_prog.link()
        pr, lp = self._prog, self._line_prog
        self._u = {"mvp": pr.uniformLocation(b"u_mvp"), "model": pr.uniformLocation(b"u_model"),
                   "light": pr.uniformLocation(b"u_light"),
                   "a_pos": pr.attributeLocation(b"a_pos"), "a_nrm": pr.attributeLocation(b"a_nrm"), "a_col": pr.attributeLocation(b"a_col"),
                   "l_mvp": lp.uniformLocation(b"u_mvp"), "l_pos": lp.attributeLocation(b"a_pos"), "l_col": lp.attributeLocation(b"a_col")}
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._vbo.create()
        self._line_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._line_vbo.create()
        self.gl.glEnable(GL_DEPTH_TEST)
        self.gl.glEnable(GL_MULTISAMPLE)
        self._dirty = True
        self._line_dirty = True

    def _upload(self) -> None:
        if self._vbo is None:
            return
        self._vbo.bind()
        if self._vbo_data is None:
            self._vbo.allocate(0)
        else:
            self._vbo.allocate(self._vbo_data.tobytes(), self._vbo_data.nbytes)
        self._vbo.release()
        self._dirty = False

    def _line_data(self) -> np.ndarray:
        segs = []

        def add(p0, p1, col):
            segs.append([*p0, *col])
            segs.append([*p1, *col])

        if self.show_bed and self.bed_mm:
            bx, by, bz = self.bed_mm
            g = (0.42, 0.44, 0.48)
            step = 10.0
            x = 0.0
            while x <= bx + 1e-6:
                add((x, 0, 0), (x, by, 0), g if int(x) % 50 else (0.55, 0.57, 0.62))
                x += step
            y = 0.0
            while y <= by + 1e-6:
                add((0, y, 0), (bx, y, 0), g if int(y) % 50 else (0.55, 0.57, 0.62))
                y += step
            e = (0.85, 0.85, 0.9)
            add((0, 0, 0), (bx, 0, 0), e); add((bx, 0, 0), (bx, by, 0), e); add((bx, by, 0), (0, by, 0), e); add((0, by, 0), (0, 0, 0), e)
            # height indicator
            add((0, 0, 0), (0, 0, bz), (0.5, 0.5, 0.6))
        if self.show_axes:
            L = 20.0
            add((0, 0, 0), (L, 0, 0), (0.95, 0.3, 0.3))
            add((0, 0, 0), (0, L, 0), (0.3, 0.9, 0.3))
            add((0, 0, 0), (0, 0, L), (0.35, 0.5, 1.0))
        if not segs:
            return np.zeros((0, 6), dtype=np.float32)
        return np.asarray(segs, dtype=np.float32)

    def _upload_lines(self) -> None:
        data = self._line_data()
        self._line_count = len(data)
        self._line_vbo.bind()
        self._line_vbo.allocate(data.tobytes(), data.nbytes)
        self._line_vbo.release()
        self._line_dirty = False

    def resizeGL(self, w: int, h: int) -> None:
        self.gl.glViewport(0, 0, max(w, 1), max(h, 1))

    def paintGL(self) -> None:
        gl = self.gl
        if gl is None:
            return
        r, g, b = self.background
        gl.glClearColor(r, g, b, 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if self._dirty:
            self._upload()
        if self._line_dirty:
            self._upload_lines()
        mvp, model = self._mvp()
        # lines
        if self._line_count and self._line_prog is not None:
            p = self._line_prog
            p.bind()
            u = self._u
            p.setUniformValue(u["l_mvp"], _qmat(mvp))
            self._line_vbo.bind()
            p.enableAttributeArray(u["l_pos"])
            p.enableAttributeArray(u["l_col"])
            p.setAttributeBuffer(u["l_pos"], GL_FLOAT, 0, 3, 24)
            p.setAttributeBuffer(u["l_col"], GL_FLOAT, 12, 3, 24)
            gl.glDrawArrays(GL_LINES, 0, self._line_count)
            p.disableAttributeArray(u["l_pos"])
            p.disableAttributeArray(u["l_col"])
            self._line_vbo.release()
            p.release()
        if self._count and self._prog is not None:
            p = self._prog
            p.bind()
            u = self._u
            p.setUniformValue(u["mvp"], _qmat(mvp))
            p.setUniformValue(u["model"], _qmat(model))
            eye = self._eye() - self.target
            p.setUniformValue(u["light"], QVector3D(float(eye[0] * 0.6 + 30), float(eye[1] * 0.6 - 40), float(abs(eye[2]) + 80)))
            self._vbo.bind()
            stride = 10 * 4
            p.enableAttributeArray(u["a_pos"])
            p.enableAttributeArray(u["a_nrm"])
            p.enableAttributeArray(u["a_col"])
            p.setAttributeBuffer(u["a_pos"], GL_FLOAT, 0, 3, stride)
            p.setAttributeBuffer(u["a_nrm"], GL_FLOAT, 12, 3, stride)
            p.setAttributeBuffer(u["a_col"], GL_FLOAT, 24, 4, stride)
            if self.wireframe:
                try:
                    gl.glPolygonMode(0x0408, 0x1B01)  # GL_FRONT_AND_BACK, GL_LINE
                except Exception:
                    pass
            if self._opaque_count:
                gl.glDrawArrays(GL_TRIANGLES, 0, self._opaque_count)
            if self._count > self._opaque_count:
                gl.glEnable(GL_BLEND)
                gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                gl.glDepthMask(False)
                gl.glEnable(GL_CULL_FACE)       # only the near faces of glass-like parts: the inside stays readable
                gl.glCullFace(GL_BACK)
                gl.glDrawArrays(GL_TRIANGLES, self._opaque_count, self._count - self._opaque_count)
                gl.glDisable(GL_CULL_FACE)
                gl.glDepthMask(True)
                gl.glDisable(GL_BLEND)
            if self.wireframe:
                try:
                    gl.glPolygonMode(0x0408, 0x1B02)  # GL_FILL
                except Exception:
                    pass
            p.disableAttributeArray(u["a_pos"])
            p.disableAttributeArray(u["a_nrm"])
            p.disableAttributeArray(u["a_col"])
            self._vbo.release()
            p.release()

    # ---- interaction -----------------------------------------------------------------
    def mousePressEvent(self, ev) -> None:
        self._last = ev.position().toPoint()
        self._button = ev.button()
        self.setFocus()

    def mouseMoveEvent(self, ev) -> None:
        if self._last is None:
            return
        pos = ev.position().toPoint()
        dx = pos.x() - self._last.x()
        dy = pos.y() - self._last.y()
        self._last = pos
        if self._button == Qt.LeftButton:
            self.yaw += dx * 0.4
            self.pitch = max(-89.0, min(89.0, self.pitch + dy * 0.4))
        elif self._button in (Qt.RightButton, Qt.MiddleButton):
            # pan in the view plane
            yaw = math.radians(self.yaw)
            right = np.array([math.cos(yaw), math.sin(yaw), 0.0])
            eye = self._eye()
            fwd = self.target - eye
            fwd /= np.linalg.norm(fwd) or 1
            up = np.cross(right, fwd)
            k = self.distance * 0.0015
            self.target = self.target - right * dx * k + up * dy * k
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        self._last = None
        self._button = None

    def wheelEvent(self, ev) -> None:
        delta = ev.angleDelta().y()
        factor = 0.9 if delta > 0 else 1.1
        self.distance = max(1.0, min(self.distance * factor, 50000.0))
        self.update()

    def keyPressEvent(self, ev) -> None:
        k = ev.key()
        if k == Qt.Key_F:
            self.fit_view()
        elif k == Qt.Key_1:
            self.set_view("front")
        elif k == Qt.Key_3:
            self.set_view("right")
        elif k == Qt.Key_7:
            self.set_view("top")
        elif k == Qt.Key_0:
            self.set_view("iso")
        elif k == Qt.Key_W:
            self.wireframe = not self.wireframe
            self.update()
        else:
            super().keyPressEvent(ev)

    def screenshot(self):
        return self.grabFramebuffer()
