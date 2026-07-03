import { ProgressPattern } from "@platforma-open/milaboratories.feature-integration.model";

type ParsedProgress = {
  raw?: string;
  stage?: string;
  percentage?: string;
  eta?: string;
  etaLabel?: string;
};

// Parse a prefix-stripped mitool progress line ("<stage>: <pct>% ETA: <eta>") into its parts.
// Mirrors blocks/peptide-extraction ui/src/parseProgress.ts.
export function parseProgressString(progressString: string | undefined | null): ParsedProgress {
  const raw = progressString ?? "Unknown";

  const res: ParsedProgress = { raw };

  if (!raw) return res;

  const match = raw.match(ProgressPattern);

  if (match) {
    const { stage, progress, eta } = match.groups!;
    res.stage = stage;
    res.percentage = progress;
    res.eta = eta;
  } else {
    res.stage = raw;
  }

  if (res.eta) {
    res.etaLabel = `ETA: ${res.eta}`;
  }

  return res;
}
