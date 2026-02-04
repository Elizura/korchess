"use client";

import { useState, useEffect, useRef } from "react";

/** Ease-out cubic: fast at start, slows as it approaches target */
function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

export interface UseCountUpOptions {
  /** Duration in ms. Default 1400. */
  duration?: number;
  /** Start animation only when this is true (e.g. when summary is visible). */
  enabled?: boolean;
  /** For decimals (e.g. score %): number of decimal places. Default 0. */
  decimals?: number;
}

/**
 * Returns a value that animates from 0 to `target` with ease-out (fast then slow).
 * When `target` or `enabled` changes, animation restarts from 0.
 */
export function useCountUp(
  target: number,
  options: UseCountUpOptions = {}
): number {
  const { duration = 1400, enabled = true, decimals = 0 } = options;
  const [displayValue, setDisplayValue] = useState(0);
  const rafRef = useRef<number>(0);
  const startRef = useRef<number>(0);
  const targetRef = useRef(target);

  targetRef.current = target;

  useEffect(() => {
    if (!enabled || target === 0) {
      setDisplayValue(target);
      return;
    }

    startRef.current = performance.now();
    setDisplayValue(0);

    const tick = (now: number) => {
      const elapsed = now - startRef.current;
      const progress = Math.min(elapsed / duration, 1);
      const eased = easeOutCubic(progress);
      const value = eased * targetRef.current;

      if (decimals > 0) {
        setDisplayValue(Number(value.toFixed(decimals)));
      } else {
        setDisplayValue(Math.round(value));
      }

      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        setDisplayValue(targetRef.current);
      }
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, enabled, duration, decimals]);

  return displayValue;
}
