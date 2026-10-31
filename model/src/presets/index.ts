import type { Preset } from "./types";
import tenxBeam from "./tenx/beam";
import genericFbUmi from "./generic/feature-barcode-umi";

export type { Preset } from "./types";

export const allPresets: readonly Preset[] = [tenxBeam, genericFbUmi] as const;

export const presetsById: Readonly<Record<string, Preset>> = Object.freeze(
  Object.fromEntries(allPresets.map((p) => [p.id, p])),
);

export function getPreset(id: string | undefined): Preset | undefined {
  return id ? presetsById[id] : undefined;
}
