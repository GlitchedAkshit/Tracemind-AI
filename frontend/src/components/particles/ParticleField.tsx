"use client";

/**
 * AXON-inspired particle cloud: an organic, hollow, twisted ring of points.
 *
 * Tier 1 (WebGL2 + float render targets + vertex texture fetch): a small GPU fluid
 *   simulation (advection, divergence, Jacobi pressure solve, gradient subtraction) is
 *   driven by the pointer; its velocity field displaces particles in screen space. As the
 *   fluid dissipates, particles settle back into the ring — no per-particle state needed.
 * Tier 2 (any WebGL): per-particle spring/repulsion on the CPU.
 * Tier 3 (no WebGL): static SVG ring.
 * prefers-reduced-motion renders one still frame and ignores the pointer.
 * Rendering pauses off-screen and in hidden tabs. Failures never affect the page.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import * as THREE from "three";

import { usePrefersReducedMotion } from "@/lib/hooks";
import { cn } from "@/lib/utils";

type Tier = "fluid" | "cpu" | "static";

const LAVENDER = new THREE.Color("#aa88ff");

function ringPositions(count: number, tube = 1, seed = 7): Float32Array {
  // deterministic PRNG so the shape is stable between renders
  let s = seed;
  const rand = () => ((s = (s * 16807) % 2147483647) - 1) / 2147483646;
  const out = new Float32Array(count * 3);
  const R = 1.55;
  for (let i = 0; i < count; i++) {
    const u = rand() * Math.PI * 2; // around the ring
    const v = rand() * Math.PI * 2; // around the tube
    const twist = u * 1.5; // cross-section rotates 1.5 turns around the ring
    const a = (0.46 + 0.12 * Math.sin(3 * u + 0.6)) * tube; // organic, varying thickness
    const b = a * 0.42; // flattened ellipse makes the twist visible
    const shell = 1 + (rand() - 0.5) * 0.12; // hollow: points hug the surface
    const cx = Math.cos(v) * a * shell;
    const cy = Math.sin(v) * b * shell;
    const rx = cx * Math.cos(twist) - cy * Math.sin(twist);
    const ry = cx * Math.sin(twist) + cy * Math.cos(twist);
    const radius = R + rx;
    out[i * 3] = Math.cos(u) * radius;
    out[i * 3 + 1] = Math.sin(u) * radius;
    out[i * 3 + 2] = ry + 0.18 * tube * Math.sin(2 * u);
  }
  return out;
}

/**
 * Largest camera distance at which a rectangle (half-size hw×hh px, centred at cx,cy px from
 * the canvas centre, y up) lies inside the ring's hole for every spin angle, with perspective.
 * Larger distance = smaller ring, so this is the smallest ring that still frames the rectangle.
 */
function frameDistance(pos: Float32Array, tilt: number, focal: number, hw: number, hh: number, cx: number, cy: number): number {
  const n = pos.length / 3;
  const step = Math.max(1, Math.floor(n / 1200));
  const pts: [number, number][] = []; // (in-plane radius, height)
  for (let i = 0; i < n; i += step) {
    // breathing moves points up to ~0.03 inward; test the inward-most position
    pts.push([Math.max(0, Math.hypot(pos[i * 3], pos[i * 3 + 1]) - 0.03), pos[i * 3 + 2]]);
  }
  const cosT = Math.cos(tilt), sinT = Math.sin(tilt);
  const angles = Array.from({ length: 72 }, (_, k) => (k / 72) * Math.PI * 2);
  const clear = (dist: number) => {
    for (const [r, h] of pts) {
      for (const th of angles) {
        const x = r * Math.cos(th), y = r * Math.sin(th);
        // spin in the ring plane, then tilt about x (matches Euler XYZ with rotation.x = -tilt)
        const ys = y * cosT + h * sinT;
        const zs = -y * sinT + h * cosT;
        const depth = dist - zs;
        if (depth <= 0.3) continue; // at or behind the camera: off-screen, cannot cover the box
        const sx = (x * focal) / depth, sy = (ys * focal) / depth;
        if (Math.abs(sx - cx) < hw && Math.abs(sy - cy) < hh) return false;
      }
    }
    return true;
  };
  let lo = 1.2, hi = 30;
  if (!clear(lo)) return lo;
  for (let i = 0; i < 22; i++) {
    const mid = (lo + hi) / 2;
    if (clear(mid)) lo = mid;
    else hi = mid;
  }
  return lo;
}

const particleVertex = /* glsl */ `
  attribute vec3 aOffset;
  attribute float aSeed;
  uniform float uTime;
  uniform float uSize;
  uniform float uPixelRatio;
  uniform float uScale;
  uniform float uUseFluid;
  uniform float uDisplace;
  uniform float uDepthNear;
  uniform sampler2D uVelocity;
  varying float vEnergy;
  varying float vDepth;
  void main() {
    vec3 p = position;
    // slow breathing so the cloud feels alive without the pointer
    float t = uTime * 0.35 + aSeed * 6.2831;
    p += normalize(p + 1e-4) * 0.025 * sin(t) + vec3(0.0, 0.0, 0.02 * cos(t * 1.3));
    p += aOffset;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    vEnergy = length(aOffset) * 3.0;
    if (uUseFluid > 0.5) {
      vec2 uv = gl_Position.xy / gl_Position.w * 0.5 + 0.5;
      vec2 vel = texture2D(uVelocity, clamp(uv, 0.0, 1.0)).xy;
      gl_Position.xy += vel * uDisplace * gl_Position.w;
      vEnergy = clamp(length(vel) * 1.6, 0.0, 1.0);
    }
    vDepth = clamp((-mv.z - uDepthNear) / 4.0, 0.0, 1.0);
    // world-space size projected to pixels, so density reads the same at any canvas size
    gl_PointSize = max(1.5, uSize * uScale * uPixelRatio / -mv.z * (0.7 + 0.6 * aSeed));
  }
`;

const particleFragment = /* glsl */ `
  uniform vec3 uColor;
  varying float vEnergy;
  varying float vDepth;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = length(c);
    if (d > 0.5) discard;
    float core = smoothstep(0.5, 0.0, d);
    vec3 col = mix(uColor, vec3(1.0), clamp(vEnergy, 0.0, 1.0) * 0.45);
    float alpha = core * mix(0.9, 0.28, vDepth) * (0.85 + 0.4 * vEnergy);
    gl_FragColor = vec4(col, alpha);
  }
`;

const quadVertex = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }
`;
const advectFrag = /* glsl */ `
  uniform sampler2D uVelocity; uniform float uDt; uniform float uDissipation; varying vec2 vUv;
  void main() {
    vec2 coord = vUv - uDt * texture2D(uVelocity, vUv).xy;
    gl_FragColor = vec4(uDissipation * texture2D(uVelocity, coord).xy, 0.0, 1.0);
  }
`;
const splatFrag = /* glsl */ `
  uniform sampler2D uTarget; uniform vec2 uPoint; uniform vec2 uForce; uniform float uRadius; uniform float uAspect; varying vec2 vUv;
  void main() {
    vec2 p = vUv - uPoint; p.x *= uAspect;
    float s = exp(-dot(p, p) / uRadius);
    gl_FragColor = vec4(texture2D(uTarget, vUv).xy + s * uForce, 0.0, 1.0);
  }
`;
const divergenceFrag = /* glsl */ `
  uniform sampler2D uVelocity; uniform vec2 uTexel; varying vec2 vUv;
  void main() {
    float L = texture2D(uVelocity, vUv - vec2(uTexel.x, 0.0)).x;
    float R = texture2D(uVelocity, vUv + vec2(uTexel.x, 0.0)).x;
    float B = texture2D(uVelocity, vUv - vec2(0.0, uTexel.y)).y;
    float T = texture2D(uVelocity, vUv + vec2(0.0, uTexel.y)).y;
    gl_FragColor = vec4(0.5 * (R - L + T - B), 0.0, 0.0, 1.0);
  }
`;
const pressureFrag = /* glsl */ `
  uniform sampler2D uPressure; uniform sampler2D uDivergence; uniform vec2 uTexel; varying vec2 vUv;
  void main() {
    float L = texture2D(uPressure, vUv - vec2(uTexel.x, 0.0)).x;
    float R = texture2D(uPressure, vUv + vec2(uTexel.x, 0.0)).x;
    float B = texture2D(uPressure, vUv - vec2(0.0, uTexel.y)).x;
    float T = texture2D(uPressure, vUv + vec2(0.0, uTexel.y)).x;
    float div = texture2D(uDivergence, vUv).x;
    gl_FragColor = vec4((L + R + B + T - div) * 0.25, 0.0, 0.0, 1.0);
  }
`;
const gradientFrag = /* glsl */ `
  uniform sampler2D uPressure; uniform sampler2D uVelocity; uniform vec2 uTexel; varying vec2 vUv;
  void main() {
    float L = texture2D(uPressure, vUv - vec2(uTexel.x, 0.0)).x;
    float R = texture2D(uPressure, vUv + vec2(uTexel.x, 0.0)).x;
    float B = texture2D(uPressure, vUv - vec2(0.0, uTexel.y)).x;
    float T = texture2D(uPressure, vUv + vec2(0.0, uTexel.y)).x;
    vec2 v = texture2D(uVelocity, vUv).xy - 0.5 * vec2(R - L, T - B);
    gl_FragColor = vec4(v, 0.0, 1.0);
  }
`;

class Fluid {
  private scene = new THREE.Scene();
  private camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private quad: THREE.Mesh;
  private velocity: [THREE.WebGLRenderTarget, THREE.WebGLRenderTarget];
  private pressure: [THREE.WebGLRenderTarget, THREE.WebGLRenderTarget];
  private divergence: THREE.WebGLRenderTarget;
  private texel: THREE.Vector2;
  private mats: Record<string, THREE.ShaderMaterial>;

  constructor(private renderer: THREE.WebGLRenderer, private size = 128) {
    const opts = { type: THREE.HalfFloatType, format: THREE.RGBAFormat, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false };
    const rt = () => new THREE.WebGLRenderTarget(size, size, opts);
    this.velocity = [rt(), rt()];
    this.pressure = [rt(), rt()];
    this.divergence = rt();
    this.texel = new THREE.Vector2(1 / size, 1 / size);
    const mk = (fragmentShader: string, uniforms: Record<string, THREE.IUniform>) =>
      new THREE.ShaderMaterial({ vertexShader: quadVertex, fragmentShader, uniforms, depthTest: false, depthWrite: false });
    this.mats = {
      advect: mk(advectFrag, { uVelocity: { value: null }, uDt: { value: 0.016 }, uDissipation: { value: 0.965 } }),
      splat: mk(splatFrag, { uTarget: { value: null }, uPoint: { value: new THREE.Vector2() }, uForce: { value: new THREE.Vector2() }, uRadius: { value: 0.0025 }, uAspect: { value: 1 } }),
      divergence: mk(divergenceFrag, { uVelocity: { value: null }, uTexel: { value: this.texel } }),
      pressure: mk(pressureFrag, { uPressure: { value: null }, uDivergence: { value: null }, uTexel: { value: this.texel } }),
      gradient: mk(gradientFrag, { uPressure: { value: null }, uVelocity: { value: null }, uTexel: { value: this.texel } }),
    };
    this.quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.mats.advect);
    this.scene.add(this.quad);
  }

  get texture() {
    return this.velocity[0].texture;
  }

  private pass(material: THREE.ShaderMaterial, target: THREE.WebGLRenderTarget) {
    this.quad.material = material;
    this.renderer.setRenderTarget(target);
    this.renderer.render(this.scene, this.camera);
  }

  private swapVelocity() {
    this.velocity = [this.velocity[1], this.velocity[0]];
  }

  splat(x: number, y: number, fx: number, fy: number, aspect: number) {
    const m = this.mats.splat;
    m.uniforms.uTarget.value = this.velocity[0].texture;
    m.uniforms.uPoint.value.set(x, y);
    m.uniforms.uForce.value.set(fx, fy);
    m.uniforms.uAspect.value = aspect;
    this.pass(m, this.velocity[1]);
    this.swapVelocity();
  }

  step(dt: number) {
    const a = this.mats.advect;
    a.uniforms.uVelocity.value = this.velocity[0].texture;
    a.uniforms.uDt.value = dt;
    this.pass(a, this.velocity[1]);
    this.swapVelocity();
    this.mats.divergence.uniforms.uVelocity.value = this.velocity[0].texture;
    this.pass(this.mats.divergence, this.divergence);
    const p = this.mats.pressure;
    p.uniforms.uDivergence.value = this.divergence.texture;
    for (let i = 0; i < 12; i++) {
      p.uniforms.uPressure.value = this.pressure[0].texture;
      this.pass(p, this.pressure[1]);
      this.pressure = [this.pressure[1], this.pressure[0]];
    }
    const g = this.mats.gradient;
    g.uniforms.uPressure.value = this.pressure[0].texture;
    g.uniforms.uVelocity.value = this.velocity[0].texture;
    this.pass(g, this.velocity[1]);
    this.swapVelocity();
    this.renderer.setRenderTarget(null);
  }

  dispose() {
    [...this.velocity, ...this.pressure, this.divergence].forEach((t) => t.dispose());
    Object.values(this.mats).forEach((m) => m.dispose());
    this.quad.geometry.dispose();
  }
}

let detectedTier: Tier | undefined;
const noSubscribe = () => () => undefined;

function detectTier(): Tier {
  if (detectedTier) return detectedTier;
  detectedTier = probeTier();
  return detectedTier;
}

function probeTier(): Tier {
  try {
    const canvas = document.createElement("canvas");
    const gl2 = canvas.getContext("webgl2");
    if (gl2) {
      const ok = gl2.getExtension("EXT_color_buffer_float") && gl2.getParameter(gl2.MAX_VERTEX_TEXTURE_IMAGE_UNITS) > 0;
      gl2.getExtension("WEBGL_lose_context")?.loseContext();
      return ok ? "fluid" : "cpu";
    }
    const gl = canvas.getContext("webgl");
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
    return gl ? "cpu" : "static";
  } catch {
    return "static";
  }
}

export function ParticleField({ className, density = 1, zoom = 1, tube = 1, frameRef, label = "Animated particle field" }: {
  className?: string; density?: number; zoom?: number; label?: string;
  /** Tube thickness multiplier; thinner tubes leave a larger hollow centre. */
  tube?: number;
  /** Element to keep inside the ring's hole. Used while it sits within the canvas (e.g. a centred card). */
  frameRef?: React.RefObject<HTMLElement | null>;
}) {
  const host = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();
  // capability probe runs once per page on the client; the server renders the pending shell
  const detected = useSyncExternalStore(noSubscribe, detectTier, () => null);
  const [degraded, setDegraded] = useState<Tier | null>(null);
  const tier = degraded ?? detected;

  useEffect(() => {
    const el = host.current;
    if (!el || !tier || tier === "static") return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true, powerPreference: "high-performance" });
    } catch {
      // WebGL context creation failed at runtime: fall back to the static ring.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDegraded("static");
      return;
    }
    const mobile = window.matchMedia("(max-width: 768px)").matches;
    const count = Math.round((mobile ? 9000 : tier === "fluid" ? 26000 : 12000) * density);
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    renderer.setPixelRatio(pixelRatio);
    renderer.setClearColor(0x000000, 0);
    renderer.domElement.setAttribute("aria-hidden", "true");
    renderer.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block";
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 50);
    camera.position.set(0, 0, 5.6);
    const geometry = new THREE.BufferGeometry();
    const homes = ringPositions(count, tube);
    geometry.setAttribute("position", new THREE.BufferAttribute(homes, 3));
    const offsets = new Float32Array(count * 3);
    const offsetAttr = new THREE.BufferAttribute(offsets, 3);
    offsetAttr.setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute("aOffset", offsetAttr);
    const seeds = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const h = Math.abs(Math.sin(i * 12.9898) * 43758.5453);
      seeds[i] = h - Math.floor(h); // stable per-particle value in [0, 1)
    }
    geometry.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));

    const fluid = tier === "fluid" ? new Fluid(renderer) : null;
    const material = new THREE.ShaderMaterial({
      vertexShader: particleVertex,
      fragmentShader: particleFragment,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uTime: { value: 0 }, uSize: { value: mobile ? 0.022 : 0.017 }, uPixelRatio: { value: pixelRatio }, uScale: { value: 400 },
        uUseFluid: { value: fluid ? 1 : 0 }, uDisplace: { value: 0.09 }, uDepthNear: { value: 2.5 }, uVelocity: { value: fluid?.texture ?? null },
        uColor: { value: LAVENDER },
      },
    });
    const points = new THREE.Points(geometry, material);
    points.rotation.x = -0.95;
    scene.add(points);

    const baseSize = material.uniforms.uSize.value as number;
    const FRAME_TILT = 0.35; // nearly face-on, so the hole is about as tall as it is wide
    let width = 1, height = 1;
    let framed = false;
    const resize = () => {
      width = Math.max(1, el.clientWidth);
      height = Math.max(1, el.clientHeight);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      const focal = height / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)));
      material.uniforms.uScale.value = focal;

      // framing mode: size the ring so the given element sits inside its hollow centre
      const target = frameRef?.current;
      const hostRect = el.getBoundingClientRect();
      const box = target?.getBoundingClientRect();
      const boxCx = box ? box.left + box.width / 2 : 0, boxCy = box ? box.top + box.height / 2 : 0;
      framed = !!box && camera.aspect >= 0.6 && boxCx > hostRect.left && boxCx < hostRect.right
        && boxCy > hostRect.top && boxCy < hostRect.bottom;
      if (framed && box) {
        const margin = 20; // breathing room between the card edge and the nearest particles
        points.rotation.x = -FRAME_TILT;
        camera.position.z = frameDistance(
          homes, FRAME_TILT, focal, box.width / 2 + margin, box.height / 2 + margin,
          boxCx - (hostRect.left + hostRect.width / 2), (hostRect.top + hostRect.height / 2) - boxCy,
        );
      } else {
        // keep the whole ring (outer radius ≈ 2.2) in frame on narrow/portrait canvases
        const fitWidth = 2.25 / (Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * camera.aspect);
        // `zoom` enlarges the ring on landscape canvases; portrait canvases always fit the full ring
        camera.position.z = camera.aspect >= 1 ? Math.max(5.6, fitWidth) / zoom : Math.min(14, Math.max(5.6, fitWidth));
        // portrait screens: tilt the ring less so it reads as a ring instead of a thin band
        points.rotation.x = camera.aspect < 0.8 ? -0.5 : -0.95;
      }
      // a closer camera magnifies points; damp that so a large ring stays fine-grained
      material.uniforms.uSize.value = baseSize * Math.min(1, Math.max(0.65, camera.position.z / 5.6));
      material.uniforms.uDepthNear.value = camera.position.z - 3.1;
      camera.updateProjectionMatrix();
    };
    resize();
    let pending = 0;
    const scheduleResize = () => {
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(resize);
    };
    const ro = new ResizeObserver(scheduleResize);
    ro.observe(el);
    if (frameRef?.current) ro.observe(frameRef.current);

    // pointer state (normalized 0..1, y up)
    const pointer = { x: 0.5, y: 0.5, px: 0.5, py: 0.5, active: false, moved: false };
    const onMove = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      pointer.x = (e.clientX - r.left) / r.width;
      pointer.y = 1 - (e.clientY - r.top) / r.height;
      pointer.active = pointer.x >= 0 && pointer.x <= 1 && pointer.y >= 0 && pointer.y <= 1;
      pointer.moved = true;
    };
    if (!reduced) window.addEventListener("pointermove", onMove, { passive: true });

    // CPU fallback state
    const velocities = tier === "cpu" ? new Float32Array(count * 3) : null;
    const base = geometry.getAttribute("position").array as Float32Array;
    const projected = new THREE.Vector3();

    let visible = true;
    const io = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; }, { threshold: 0.01 });
    io.observe(el);

    let last = performance.now();
    let raf = 0;
    let camX = 0, camY = 0;
    const frame = () => {
      raf = requestAnimationFrame(frame);
      const now = performance.now();
      const dt = Math.min((now - last) / 1000, 1 / 30);
      last = now;
      if (!visible || document.hidden) return;
      material.uniforms.uTime.value += dt;
      points.rotation.z += dt * 0.035;

      // camera drifts subtly toward the pointer (much less when the ring frames content)
      const drift = framed ? 0.2 : 1;
      const tx = pointer.active ? (pointer.x - 0.5) * 0.9 * drift : 0;
      const ty = pointer.active ? (pointer.y - 0.5) * 0.6 * drift : 0;
      camX += (tx - camX) * 0.04;
      camY += (ty - camY) * 0.04;
      camera.position.x = camX;
      camera.position.y = camY;
      camera.lookAt(0, 0, 0);

      if (fluid) {
        if (pointer.moved && pointer.active) {
          const fx = (pointer.x - pointer.px) / dt;
          const fy = (pointer.y - pointer.py) / dt;
          fluid.splat(pointer.x, pointer.y, fx * 0.6, fy * 0.6, width / height);
        }
        fluid.step(dt);
        material.uniforms.uVelocity.value = fluid.texture;
      } else if (velocities) {
        // CPU: repel from the pointer in screen space, spring back home
        const px = pointer.x * 2 - 1, py = pointer.y * 2 - 1;
        const radius = 0.18, strength = pointer.active && pointer.moved ? 0.9 : 0;
        points.updateMatrixWorld();
        for (let i = 0; i < count; i++) {
          const j = i * 3;
          let fx = -offsets[j] * 4, fy = -offsets[j + 1] * 4, fz = -offsets[j + 2] * 4;
          if (strength) {
            projected.set(base[j] + offsets[j], base[j + 1] + offsets[j + 1], base[j + 2] + offsets[j + 2])
              .applyMatrix4(points.matrixWorld).project(camera);
            const dx = projected.x - px, dy = (projected.y - py) * (height / width);
            const d2 = dx * dx + dy * dy;
            if (d2 < radius * radius) {
              const f = strength * (1 - Math.sqrt(d2) / radius);
              fx += dx * f * 30; fy += dy * f * 30; fz += f * 2;
            }
          }
          velocities[j] = (velocities[j] + fx * dt) * 0.9;
          velocities[j + 1] = (velocities[j + 1] + fy * dt) * 0.9;
          velocities[j + 2] = (velocities[j + 2] + fz * dt) * 0.9;
          offsets[j] += velocities[j] * dt * 4;
          offsets[j + 1] += velocities[j + 1] * dt * 4;
          offsets[j + 2] += velocities[j + 2] * dt * 4;
        }
        offsetAttr.needsUpdate = true;
      }
      pointer.px = pointer.x;
      pointer.py = pointer.y;
      pointer.moved = false;
      renderer.render(scene, camera);
    };

    const onLost = (e: Event) => {
      e.preventDefault();
      cancelAnimationFrame(raf);
      setDegraded("static");
    };
    renderer.domElement.addEventListener("webglcontextlost", onLost);

    try {
      if (reduced) {
        material.uniforms.uTime.value = 2.0;
        if (fluid) fluid.step(0.016);
        renderer.render(scene, camera); // a single still frame
      } else {
        frame();
      }
    } catch {
      setDegraded(tier === "fluid" ? "cpu" : "static");
    }

    return () => {
      cancelAnimationFrame(raf);
      cancelAnimationFrame(pending);
      window.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("webglcontextlost", onLost);
      ro.disconnect();
      io.disconnect();
      fluid?.dispose();
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [tier, reduced, density, zoom, tube, frameRef]);

  return (
    <div ref={host} className={cn("pointer-events-none relative overflow-hidden", className)} role="img" aria-label={label} data-particle-tier={tier ?? "pending"}>
      {tier === "static" && <StaticRing />}
    </div>
  );
}

function StaticRing() {
  return (
    <svg viewBox="-200 -120 400 240" className="absolute inset-0 h-full w-full opacity-70" aria-hidden="true">
      <defs>
        <radialGradient id="tm-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#aa88ff" stopOpacity="0.25" />
          <stop offset="100%" stopColor="#aa88ff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="0" cy="0" rx="150" ry="60" fill="url(#tm-glow)" />
      {Array.from({ length: 9 }, (_, i) => (
        <ellipse key={i} cx="0" cy="0" rx={120 + i * 4} ry={42 + Math.sin(i) * 10} fill="none" stroke="#aa88ff"
          strokeOpacity={0.12 + (i % 3) * 0.06} strokeDasharray="1 5" transform={`rotate(${i * 7 - 28})`} />
      ))}
    </svg>
  );
}
