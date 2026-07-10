/** A read-geometry preset for the Feature Integration pattern builder (mirrors the shape used by
 *  blocks/peptide-extraction). A fixed-kit preset carries its `pattern` directly; a user-configurable
 *  preset leaves `pattern` empty and drives it from `data.pattern` via the UI builder. */
export type Preset = {
  id: string;
  vendor: string;
  kit: string;
  label: string; // dropdown text
  description: string;
  pattern: string; // "" for userConfigurable presets (pattern lives in data.pattern)
  notes?: string;
  userConfigurable?: boolean;
};
