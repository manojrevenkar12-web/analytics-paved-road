import { motion } from "motion/react";

import { bytes } from "../../shared/api/client";
import { laneTokens, motionTokens } from "../../shared/theme/tokens";
import { FAST_LANE_MAX, position } from "./lane";

type Props = { estBytes: number | null; lane: "fast" | "spark" | null; refused: boolean; limitBytes: number | null };

/** The query on the road: bytes it will scan on a log axis, in the lane it will run in. */
export function LaneStrip({ estBytes, lane, refused, limitBytes }: Props) {
  const fastEnd = position(FAST_LANE_MAX) * 100;
  const limit = limitBytes ? position(limitBytes) * 100 : null;
  const x = estBytes != null ? position(estBytes) * 100 : 0;
  const row = lane === "spark" ? 1 : 0;
  return (
    <figure
      aria-label={
        estBytes == null ? "Lane strip: no query yet" : refused ? "Lane strip: refused before the road"
          : `Lane strip: ${bytes(estBytes)} in the ${lane} lane`
      }
      className="relative overflow-hidden rounded-[var(--radius-panel)] bg-asphalt text-[11px] text-white/70"
      style={{ height: laneTokens.height }}
    >
      <div className="absolute inset-x-0 top-1/2 border-t-2 border-dashed border-marking/80" />
      <span className="absolute left-3 top-2">Fast lane · DuckDB in the gateway</span>
      <span className="absolute bottom-2 left-3">Spark lane · Kyuubi</span>
      <div className="absolute inset-y-0 border-l-2 border-marking" style={{ left: `${fastEnd}%` }}>
        <span className="absolute right-1.5 top-2 whitespace-nowrap text-marking">256 MiB</span>
      </div>
      {limit != null && (
        <div className="absolute inset-y-0 border-l-2 border-denied" style={{ left: `${limit}%` }}>
          <span className="absolute left-1.5 bottom-2 whitespace-nowrap text-white/80">your limit {bytes(limitBytes!)}</span>
        </div>
      )}
      {laneTokens.ticks.map((t) => (
        <span key={t} className="absolute bottom-0 h-1.5 border-l border-white/40" style={{ left: `${position(t) * 100}%` }} />
      ))}
      {estBytes != null && (
        <motion.div
          className={`absolute flex h-7 items-center gap-1.5 rounded-full px-2.5 font-semibold ${
            refused ? "bg-denied text-white" : "bg-marking text-asphalt"
          }`}
          initial={false}
          animate={{
            left: refused ? "1%" : `calc(${Math.min(x, 92)}% )`,
            top: refused ? "50%" : row === 0 ? "calc(25% - 14px)" : "calc(75% - 14px)",
            y: refused ? "-50%" : 0,
          }}
          transition={motionTokens.marker}
          data-testid="lane-marker"
        >
          {refused ? "Stopped before the road" : bytes(estBytes)}
        </motion.div>
      )}
    </figure>
  );
}
