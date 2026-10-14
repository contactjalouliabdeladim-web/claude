import { useEffect, useRef } from "react";
import type { OrbState } from "../types";

interface Props {
  state: OrbState;
  /** Niveau audio réel (micro ou voix jouée), 0..1, ou null si aucune analyse possible. */
  level: () => number | null;
  reduceMotion: boolean;
}

interface Palette {
  core: [number, number, number];
  glow: [number, number, number];
  intensity: number;
}

const PALETTES: Record<OrbState, Palette> = {
  idle: { core: [246, 213, 138], glow: [215, 166, 75], intensity: 0.75 },
  listening: { core: [255, 226, 160], glow: [236, 180, 80], intensity: 1 },
  transcribing: { core: [246, 213, 138], glow: [215, 166, 75], intensity: 0.85 },
  thinking: { core: [246, 213, 138], glow: [210, 160, 80], intensity: 0.85 },
  acting: { core: [250, 205, 120], glow: [225, 150, 60], intensity: 0.9 },
  waiting_approval: { core: [250, 196, 110], glow: [232, 140, 55], intensity: 0.9 },
  speaking: { core: [255, 230, 170], glow: [236, 180, 80], intensity: 1 },
  error: { core: [214, 150, 120], glow: [170, 90, 70], intensity: 0.5 },
  offline: { core: [150, 160, 180], glow: [90, 100, 125], intensity: 0.35 },
};

const ANIMATED: Record<OrbState, boolean> = {
  idle: true, listening: true, transcribing: true, thinking: true, acting: true, waiting_approval: true,
  speaking: true, error: false, offline: false,
};

function rgba(c: [number, number, number], a: number) {
  return `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${a.toFixed(3)})`;
}

function mix(a: [number, number, number], b: [number, number, number], t: number): [number, number, number] {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

export default function Orb({ state, level, reduceMotion }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  const levelRef = useRef(level);
  stateRef.current = state;
  levelRef.current = level;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    let last = 0;
    let t = 0;
    let smoothLevel = 0;
    const cur: Palette = { ...PALETTES[stateRef.current], core: [...PALETTES[stateRef.current].core] as any, glow: [...PALETTES[stateRef.current].glow] as any };

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
    };
    resize();

    const draw = (dt: number) => {
      const s = stateRef.current;
      const target = PALETTES[s];
      const k = Math.min(1, dt * 3);
      cur.core = mix(cur.core, target.core, k);
      cur.glow = mix(cur.glow, target.glow, k);
      cur.intensity += (target.intensity - cur.intensity) * k;

      const raw = levelRef.current();
      const lvl = s === "listening" || s === "speaking" ? (raw === null ? 0.35 + 0.15 * Math.sin(t * 4) : raw) : 0;
      smoothLevel += (lvl - smoothLevel) * Math.min(1, dt * (lvl > smoothLevel ? 18 : 6));

      const w = canvas.width;
      const h = canvas.height;
      const cx = w / 2;
      const cy = h / 2;
      const base = Math.min(w, h) * 0.26;
      const breathe = reduceMotion ? 0 : Math.sin(t * (s === "idle" ? 0.9 : 1.6)) * 0.018;
      const r = base * (1 + breathe + smoothLevel * 0.14);
      ctx.clearRect(0, 0, w, h);

      // Halo extérieur
      const halo = ctx.createRadialGradient(cx, cy, r * 0.6, cx, cy, r * 2.1);
      halo.addColorStop(0, rgba(cur.glow, 0.32 * cur.intensity + smoothLevel * 0.15));
      halo.addColorStop(0.45, rgba(cur.glow, 0.1 * cur.intensity));
      halo.addColorStop(1, rgba(cur.glow, 0));
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, w, h);

      // Sphère
      const core = ctx.createRadialGradient(cx - r * 0.32, cy - r * 0.36, r * 0.05, cx, cy, r);
      core.addColorStop(0, rgba([255, 248, 228], 0.95 * cur.intensity + 0.05));
      core.addColorStop(0.35, rgba(cur.core, 0.95));
      core.addColorStop(0.78, rgba(cur.glow, 0.9));
      core.addColorStop(1, rgba(mix(cur.glow, [60, 40, 15], 0.55), 0.85));
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = core;
      ctx.fill();

      // Lumière intérieure en mouvement (clippée dans la sphère)
      if (!reduceMotion && ANIMATED[s]) {
        ctx.save();
        ctx.beginPath();
        ctx.arc(cx, cy, r * 0.98, 0, Math.PI * 2);
        ctx.clip();
        ctx.globalCompositeOperation = "lighter";
        const speed = s === "thinking" || s === "acting" ? 0.9 : s === "idle" ? 0.25 : 0.55;
        for (let i = 0; i < 3; i++) {
          const a = t * speed * (1 + i * 0.35) + (i * Math.PI * 2) / 3;
          const bx = cx + Math.cos(a) * r * 0.42;
          const by = cy + Math.sin(a * 1.3) * r * 0.32;
          const br = r * (0.55 + smoothLevel * 0.25);
          const blob = ctx.createRadialGradient(bx, by, 0, bx, by, br);
          blob.addColorStop(0, rgba([255, 240, 205], 0.16 + smoothLevel * 0.2));
          blob.addColorStop(1, rgba([255, 240, 205], 0));
          ctx.fillStyle = blob;
          ctx.fillRect(cx - r, cy - r, r * 2, r * 2);
        }
        ctx.restore();
      }

      // Anneaux selon l'état
      ctx.lineCap = "round";
      if (s === "thinking" || s === "transcribing") {
        const segs = 3;
        for (let i = 0; i < segs; i++) {
          const start = (reduceMotion ? 0 : t * 1.1) + (i * Math.PI * 2) / segs;
          ctx.beginPath();
          ctx.arc(cx, cy, r * 1.22, start, start + 0.9);
          ctx.strokeStyle = rgba(cur.core, 0.55);
          ctx.lineWidth = Math.max(1.5, r * 0.025);
          ctx.stroke();
        }
      } else if (s === "acting") {
        const orbit = r * 1.28;
        ctx.setLineDash([r * 0.05, r * 0.08]);
        ctx.beginPath();
        ctx.arc(cx, cy, orbit, 0, Math.PI * 2);
        ctx.strokeStyle = rgba(cur.core, 0.35);
        ctx.lineWidth = Math.max(1, r * 0.015);
        ctx.stroke();
        ctx.setLineDash([]);
        const a = reduceMotion ? -Math.PI / 4 : t * 1.6;
        ctx.beginPath();
        ctx.arc(cx + Math.cos(a) * orbit, cy + Math.sin(a) * orbit, r * 0.06, 0, Math.PI * 2);
        ctx.fillStyle = rgba([255, 240, 200], 0.95);
        ctx.fill();
      } else if (s === "waiting_approval") {
        const p = reduceMotion ? 0.6 : 0.45 + 0.35 * (0.5 + 0.5 * Math.sin(t * 2.2));
        ctx.beginPath();
        ctx.arc(cx, cy, r * 1.2, 0, Math.PI * 2);
        ctx.strokeStyle = rgba([240, 160, 70], p);
        ctx.lineWidth = Math.max(2, r * 0.03);
        ctx.stroke();
      } else if (s === "listening" || s === "speaking") {
        for (let i = 0; i < 2; i++) {
          const rr = r * (1.14 + i * 0.12 + smoothLevel * (0.22 + i * 0.1));
          ctx.beginPath();
          ctx.arc(cx, cy, rr, 0, Math.PI * 2);
          ctx.strokeStyle = rgba(cur.core, (0.35 - i * 0.14) * (0.4 + smoothLevel));
          ctx.lineWidth = Math.max(1, r * 0.018);
          ctx.stroke();
        }
      } else if (s === "error" || s === "offline") {
        ctx.beginPath();
        ctx.arc(cx, cy, r * 1.18, 0, Math.PI * 2);
        ctx.strokeStyle = rgba(cur.glow, 0.35);
        ctx.lineWidth = Math.max(1, r * 0.015);
        ctx.setLineDash([r * 0.03, r * 0.06]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    };

    const frame = (now: number) => {
      raf = 0;
      if (document.hidden) return;
      const s = stateRef.current;
      const active = s === "listening" || s === "speaking";
      const minInterval = active ? 1000 / 60 : 1000 / 30; // repos : 30 i/s suffisent
      if (now - last >= minInterval - 1) {
        const dt = last ? Math.min(0.1, (now - last) / 1000) : 1 / 30;
        last = now;
        t += dt;
        draw(dt);
      }
      if (!reduceMotion || active) raf = requestAnimationFrame(frame);
    };

    const kick = () => {
      if (!raf && !document.hidden) raf = requestAnimationFrame(frame);
    };
    const onVisibility = () => {
      last = 0;
      kick();
    };
    const onResize = () => {
      resize();
      draw(0);
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("resize", onResize);
    draw(1);
    kick();
    // En mouvement réduit, l'orbe ne s'anime pas : on redessine juste aux changements d'état.
    const interval = window.setInterval(() => {
      if (reduceMotion) {
        draw(1);
        kick();
      }
    }, 400);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("resize", onResize);
    };
  }, [reduceMotion]);

  return <canvas ref={canvasRef} className="orb-canvas" aria-hidden="true" />;
}
