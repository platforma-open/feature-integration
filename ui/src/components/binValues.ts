// Values reconstructed from binned weights, spread evenly inside each bin.
//
// For `PlChartHistogram`'s `basic` form, the only one it draws on a linear axis. `basic` bins raw values
// itself; a uniform spread inside each bin makes that re-binning proportional to the emitted weights, to
// within one observation per boundary.
//
// Resolution is capped by the source: the caller's `nBins` must not exceed `weights.length`.
//
// Empty bins are skipped. `createHistogramLinear` takes its domain from the values it is given, and an
// empty leading bin would otherwise widen the axis past any observation.
export function valuesFromBins(edges: number[], weights: number[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < weights.length; i++) {
    const weight = weights[i]!;
    if (weight === 0) continue;
    const from = edges[i]!;
    const step = (edges[i + 1]! - from) / weight;
    for (let j = 0; j < weight; j++) out.push(from + (j + 0.5) * step);
  }
  return out;
}
