import type { AxisId, PFrameHandle } from "@platforma-sdk/model";
import { getColumnsFull, getSingleColumnData } from "@platforma-sdk/model";
import { ref, watch, type ComputedRef } from "vue";

/**
 * The clonotype's readable name (`C-ZDKEZ`) and its cell count, for the one place that cannot get them from
 * a table: the expansion's title.
 *
 * Upstream emits the name as a `pl7.app/label` column on the clonotype axis. A TABLE shows it without being
 * asked -- `PlAgDataTableV2` substitutes label columns into axis cells -- which is why the card's `Clone Id`
 * column reads `C-ZDKEZ` while the row event that opens the panel carries only the raw key.
 *
 * The model cannot look it up. Both columns are Parquet-stored, and the model-side label APIs
 * (`deriveAxisValuesLabels`, the older `findLabels`) skip any label column that is not JSON-backed. The
 * pFrame DRIVER reads Parquet, so the lookup goes through `getColumnsFull` / `getSingleColumnData`.
 *
 * Takes the whole column rather than one key: it is one value per clonotype and it leaves the map ready for
 * the next one. `getSingleColumnData` accepts filters if a large run ever makes that the wrong trade.
 */
export function useClonotypeLabels(
  labelsPf: ComputedRef<PFrameHandle | undefined>,
  axisId: ComputedRef<AxisId | undefined>,
) {
  const labels = ref<Record<string, string>>({});
  const cellCounts = ref<Record<string, string>>({});

  /**
   * One column's values, keyed by axis value. Returns an empty map rather than throwing: a missing title is
   * a cosmetic loss, and the caller has a generic word to fall back on.
   */
  async function valuesByAxisKey(
    handle: PFrameHandle,
    axis: AxisId,
    columnName: string,
  ): Promise<Record<string, string>> {
    const cols = await getColumnsFull(handle, {
      selectedSources: [],
      strictlyCompatible: false,
      names: [columnName],
    });
    // One axis, and it has to be OUR axis. This frame carries other one-axis columns keyed on other things,
    // and a map built from the wrong axis would attach names to keys that are not clonotypes.
    const match = cols.find(
      (c) => c.spec.axesSpec.length === 1 && c.spec.axesSpec[0].name === axis.name,
    );
    if (match === undefined) return {};
    const { axesData, data } = await getSingleColumnData(handle, match.columnId);
    const axisKeys = Object.values(axesData)[0];
    // Paired by position, so a length mismatch means these are not the same rows and every value would land
    // on the wrong clonotype.
    if (axisKeys === undefined || axisKeys.length !== data.length) return {};
    const out: Record<string, string> = {};
    for (let i = 0; i < axisKeys.length; i++) {
      const key = axisKeys[i];
      const value = data[i];
      if (key !== null && value !== null) out[String(key)] = String(value);
    }
    return out;
  }

  watch(
    [labelsPf, axisId],
    async ([handle, axis]) => {
      if (handle === undefined || axis === undefined) {
        labels.value = {};
        cellCounts.value = {};
        return;
      }
      try {
        const [resolvedLabels, resolvedCounts] = await Promise.all([
          valuesByAxisKey(handle, axis, "pl7.app/label"),
          valuesByAxisKey(handle, axis, "pl7.app/antigen/cellCount"),
        ]);
        labels.value = resolvedLabels;
        cellCounts.value = resolvedCounts;
      } catch (err) {
        // A failed lookup costs a title, never the panel. Logged rather than swallowed: a name that never
        // resolves means the driver call is wrong.
        console.warn("clonotype label lookup failed", err);
        labels.value = {};
        cellCounts.value = {};
      }
    },
    { immediate: true },
  );

  /**
   * The clonotype's name and cell count: `C-ZDKEZ — 4 cells`.
   *
   * Undefined while the lookup is in flight or if it failed, and deliberately NOT falling back to the raw
   * key. That key appears nowhere else in the block, so it names nothing and reads as a bug.
   *
   * The count is optional on its own -- a name with no count still names the clonotype.
   */
  const resolveTitle = (key: string | number | null | undefined): string | undefined => {
    if (key === null || key === undefined) return undefined;
    const label = labels.value[String(key)];
    if (label === undefined) return undefined;
    const cells = cellCounts.value[String(key)];
    return cells === undefined ? label : `${label} — ${cells} cells`;
  };

  return { labels, cellCounts, resolveTitle };
}
