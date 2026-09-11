import { useEffect, useRef } from 'react';

/**
 * The moving background: a grid of points whose brightness follows a slowly
 * drifting noise field, with a faint radial sweep passing over it.
 *
 * Drawn on a canvas rather than shipped as a video. A video is megabytes,
 * loops visibly, and cannot react to the viewport; this is a few hundred lines
 * of arithmetic per frame and scales to any screen. It is also deliberately
 * restrained -- one colour, low contrast, slow -- so it reads as an instrument
 * rather than a screensaver, and never competes with the text in front of it.
 *
 * Honours `prefers-reduced-motion`: a single static frame is drawn and the
 * animation loop never starts.
 */
export function Backdrop({ intensity = 1 }: { intensity?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const noise = makeNoise(7);
    let frame = 0;
    let width = 0;
    let height = 0;
    let dpr = 1;

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas!.width = Math.floor(width * dpr);
      canvas!.height = Math.floor(height * dpr);
      canvas!.style.width = `${width}px`;
      canvas!.style.height = `${height}px`;
      context!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function draw(time: number) {
      const t = time * 0.00004; // slow: a full drift takes minutes
      const spacing = 26;
      const cols = Math.ceil(width / spacing) + 1;
      const rows = Math.ceil(height / spacing) + 1;

      context!.clearRect(0, 0, width, height);

      // The sweep: a soft radial highlight orbiting the centre-right, so the
      // field has somewhere the eye is drawn to, faintly, like a scan.
      const cx = width * 0.62 + Math.cos(t * 9) * width * 0.18;
      const cy = height * 0.45 + Math.sin(t * 7) * height * 0.16;
      const reach = Math.max(width, height) * 0.55;

      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols; col++) {
          const x = col * spacing;
          const y = row * spacing;

          // Two octaves of noise: broad drift plus fine texture.
          const n =
            noise(x * 0.0035 + t * 6, y * 0.0035 - t * 4) * 0.7 +
            noise(x * 0.012 - t * 3, y * 0.012 + t * 5) * 0.3;

          const dx = x - cx;
          const dy = y - cy;
          const sweep = Math.max(0, 1 - Math.sqrt(dx * dx + dy * dy) / reach);

          const brightness = Math.max(0, n * 0.75 + sweep * sweep * 0.55);
          if (brightness < 0.06) continue;

          const alpha = Math.min(0.55, brightness * 0.5) * intensity;
          const radius = 0.6 + brightness * 1.5;

          // Amber where the field is strongest, cool grey elsewhere -- so the
          // accent appears only as highlights rather than tinting everything.
          const warm = brightness > 0.62;
          context!.fillStyle = warm
            ? `rgba(240, 180, 69, ${alpha})`
            : `rgba(143, 152, 170, ${alpha * 0.7})`;
          context!.beginPath();
          context!.arc(x, y, radius, 0, Math.PI * 2);
          context!.fill();
        }
      }
    }

    resize();
    window.addEventListener('resize', resize);

    if (reduceMotion) {
      draw(0);
      return () => window.removeEventListener('resize', resize);
    }

    function loop(time: number) {
      draw(time);
      frame = window.requestAnimationFrame(loop);
    }
    frame = window.requestAnimationFrame(loop);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
    };
  }, [intensity]);

  return (
    <canvas
      ref={ref}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 -z-10"
    />
  );
}

/**
 * Value noise with smooth interpolation, seeded so every load looks the same.
 * Not Perlin or simplex -- those are better, and this is enough for a
 * background that nobody should be looking at directly.
 */
function makeNoise(seed: number): (x: number, y: number) => number {
  const size = 256;
  const table = new Float32Array(size * size);
  let s = seed >>> 0;
  for (let i = 0; i < table.length; i++) {
    // xorshift32: tiny, deterministic, good enough.
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    table[i] = ((s >>> 0) % 10_000) / 10_000;
  }

  const smooth = (v: number) => v * v * (3 - 2 * v);
  const at = (x: number, y: number) =>
    table[(((y % size) + size) % size) * size + (((x % size) + size) % size)]!;

  return (x, y) => {
    const x0 = Math.floor(x);
    const y0 = Math.floor(y);
    const fx = smooth(x - x0);
    const fy = smooth(y - y0);
    const top = at(x0, y0) + (at(x0 + 1, y0) - at(x0, y0)) * fx;
    const bottom = at(x0, y0 + 1) + (at(x0 + 1, y0 + 1) - at(x0, y0 + 1)) * fx;
    return top + (bottom - top) * fy;
  };
}
