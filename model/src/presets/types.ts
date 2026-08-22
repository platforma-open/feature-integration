/** A read-geometry preset for the Feature Integration pattern builder, mirroring the shape
 *  blocks/peptide-extraction uses. A fixed-kit preset carries its `pattern` directly. A
 *  user-configurable preset leaves `pattern` empty and drives it from `data.pattern` in the UI builder. */
export type Preset = {
  id: string;
  vendor: string;
  kit: string;
  label: string; // dropdown text
  description: string;
  pattern: string; // "" for a userConfigurable preset, whose pattern lives in data.pattern
  notes?: string;
  userConfigurable?: boolean;
};
