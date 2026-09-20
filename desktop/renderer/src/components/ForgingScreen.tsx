import type { CSSProperties } from "react";

export const FORGING_MESSAGE = "Your guide is being forged";

/**
 * A flat, 2D "forging your guide" loop: a hammer swings down onto an anvil and
 * sparks fly off the hit, forever. No libraries and no images - one SVG, CSS
 * keyframes only, ported from `assets/forging-anvil.html`.
 *
 * The pose at 0% and 100% is identical, so the loop has no seam. The strike
 * lands at 58% of the cycle; the flash, sparks, puffs and anvil jolt are all
 * keyed off that same mark, and the cycle lives in `--forge-cycle`.
 */
type ForgeStyle = CSSProperties & { [key: `--forge-${string}`]: string };

interface SparkShape {
  radius: number;
  dx: string;
  dy: string;
  peak: string;
}

interface PuffShape {
  radius: number;
  dx: string;
  dy: string;
}

// Every spark shares one rule; --forge-dx/--forge-dy/--forge-peak give each its
// own arc away from the strike point.
const SPARKS: SparkShape[] = [
  { radius: 1.9, dx: "-38px", dy: "-6px", peak: "-30px" },
  { radius: 1.5, dx: "-26px", dy: "22px", peak: "-34px" },
  { radius: 2.1, dx: "-14px", dy: "6px", peak: "-46px" },
  { radius: 1.4, dx: "-52px", dy: "12px", peak: "-26px" },
  { radius: 1.8, dx: "-8px", dy: "-16px", peak: "-58px" },
  { radius: 1.6, dx: "12px", dy: "-12px", peak: "-54px" },
  { radius: 2, dx: "22px", dy: "14px", peak: "-38px" },
  { radius: 1.5, dx: "34px", dy: "-4px", peak: "-32px" },
  { radius: 1.9, dx: "46px", dy: "18px", peak: "-28px" },
  { radius: 1.4, dx: "58px", dy: "8px", peak: "-22px" },
  { radius: 1.7, dx: "-46px", dy: "28px", peak: "-20px" },
  { radius: 1.5, dx: "40px", dy: "30px", peak: "-18px" },
];

const PUFFS: PuffShape[] = [
  { radius: 5, dx: "-14px", dy: "-8px" },
  { radius: 4, dx: "16px", dy: "-10px" },
];

export interface ForgingScreenProps {
  /** What the request is doing right now, from the queue row. */
  detail?: string | null;
}

export function ForgingScreen({ detail = null }: ForgingScreenProps) {
  return (
    <div className="forging-screen" role="status" aria-live="polite">
      <svg className="forge-loop" viewBox="0 0 240 190" aria-hidden="true" focusable="false">
        {/* anvil: face, horn, body, waist, base, foot */}
        <path
          className="forge-anvil"
          d="M48 94 H168 V108 H156 V128 H132 V148 H160 V162 H174 V170 H42 V162 H56 V148 H84 V128 H60 V108 H48 L4 101 Z"
        />

        {/* hammer: handle from the pivot, head at the far end */}
        <g className="forge-hammer">
          <rect className="forge-handle" x="-3.5" y="0" width="7" height="64" rx="3.5" />
          <rect className="forge-head" x="-22" y="64" width="44" height="28" rx="3" />
        </g>

        {/* impact burst, drawn over the metal: flash, dust, sparks */}
        <circle className="forge-flash" cx="120" cy="94" r="7" />
        {PUFFS.map((puff, index) => (
          <circle
            className="forge-puff"
            key={`puff-${index}`}
            cx="120"
            cy="90"
            r={puff.radius}
            style={{ "--forge-px": puff.dx, "--forge-py": puff.dy } as ForgeStyle}
          />
        ))}
        <g>
          {SPARKS.map((spark, index) => (
            <circle
              className="forge-spark"
              key={`spark-${index}`}
              cx="120"
              cy="94"
              r={spark.radius}
              style={{
                "--forge-dx": spark.dx,
                "--forge-dy": spark.dy,
                "--forge-peak": spark.peak,
              } as ForgeStyle}
            />
          ))}
        </g>
      </svg>
      <span className="forging-kicker">Study guide</span>
      <strong className="forging-heading">{FORGING_MESSAGE}</strong>
      {detail === null || detail === "" ? null : (
        <p className="forging-detail">{detail}</p>
      )}
    </div>
  );
}
