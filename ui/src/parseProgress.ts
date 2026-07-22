import { ProgressPattern } from "@platforma-open/milaboratories.feature-integration.model";

// Parse one mitool progress line (already stripped of the [==PROGRESS==] prefix by the accessor) into
// its stage / percent / ETA parts. Ported from blocks/peptide-extraction (same ProgressPattern).
export type ParsedProgress = {
  raw?: string;
  stage?: string;
  percentage?: string;
  eta?: string;
  etaLabel?: string;
};

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
