/**
 * The words a reader sees for each sample QC measurement.
 *
 * `whenBad` is what a bad value MEANS, and only measurements with a line behind them carry one: with no
 * threshold there is no bad value to interpret.
 */
export type QcMeasurementDescription = {
  label: string;
  /** What the measurement counts. Always shown. */
  description: string;
  /** What a BAD value means. Shown only where the row carries a non-OK status, so a healthy
   * row stays quiet */
  whenBad?: string;
};

export const QC_MEASUREMENT_DESCRIPTIONS: Record<string, QcMeasurementDescription> = {
  matchedFraction: {
    label: "Fraction of reads matching the read pattern",
    description:
      "Reads whose layout matched the read pattern, so the cell barcode, the UMI and the antigen barcode could be cut out of them, over every read in this sample's files.",
    whenBad:
      "A low share means most reads do not fit the read geometry this run was configured with. The pattern is all fixed-length positions, so there is nothing in it to mutate: a read either covers the geometry or it does not, which makes a low share a fact about the read-geometry preset or the read length rather than about the library. A 15-base antigen barcode at a 10-base offset takes 25 bases of Read 2. Both thresholds are this block's own estimate rather than a published number.",
  },
  cellBarcodeValidFraction: {
    label: "Fraction of reads whose cell barcode passed quality correction",
    description:
      "Reads whose cell barcode survived correction, out of the reads that entered it. Correction clusters the cell barcodes the reads actually carry and merges rare ones onto close, frequent neighbours; a read whose barcode is too poor in quality to place is dropped.",
    whenBad:
      "A low share means the cell barcodes were read at poor quality, so reads are being lost before anything can be counted per cell. Both thresholds are this block's own estimate rather than a published number, and a shallow library can dip under the warn line without anything being wrong -- correction rescues a poor barcode by clustering it onto a frequent neighbour, and a shallow run has fewer neighbours to rescue onto.",
  },
  panelAssignedFraction: {
    label: "Fraction of reads matching a panel barcode",
    description:
      "Reads whose antigen barcode matches the panel, out of the reads matching the read pattern.",
    whenBad:
      "A low share means most reads carry an antigen barcode the panel does not declare, which points at the wrong panel file or a reagent that is not in it.",
  },
  refineRescuedShare: {
    label: "Fraction of reads rescued by barcode correction",
    description:
      "Reads carrying an antigen barcode the panel does not list, close enough to a listed one that correction moved it there. Up to two substituted bases and two indels are allowed, three errors in total, and a barcode seen in few reads or at low quality gets a tighter limit than that. Without the correction these reads would have been thrown away.",
    whenBad:
      "A high share means a large part of the library reached the panel by inference rather than by matching a declared barcode outright. Both numbers are this block's own estimate, and the reading is two-sided by nature: a high share can mean correction is doing real work or that base quality is poor, and a low one can mean a clean run or a panel whose barcodes sit too far apart for anything to need rescuing.",
  },
  cellsDetected: {
    label: "Cell barcodes detected",
    description: "Distinct cell barcodes seen, before cell calling. Most are empty droplets.",
    whenBad: "Zero cells means nothing downstream can be computed for this sample.",
  },
  aggregateBarcodeFraction: {
    label: "Fraction of reads in aggregate barcodes",
    description:
      "Reads in cell barcodes carrying far more signal than the rest, which points at clumped droplets. The threshold is automatic.",
    whenBad:
      "A high share means much of the run's antigen signal comes from a small number of clumped droplets rather than single cells.",
  },
  uniqueCountsPerCell: {
    label: "Median antigen count per V(D)J-matched cell barcode",
    description:
      "How much antigen a V(D)J-matched cell barcode holds. Counts from all antigens are added up per barcode to then compute this median. Barcodes with no V(D)J match are left out.",
    whenBad:
      "A low median means the typical analysed cell carries little antigen signal for a call to rest on.",
  },
  usableReadFraction: {
    label: "Fraction of reads usable for antigen calls",
    description:
      "Reads that both match the panel and come from a cell the V(D)J data matched, over every read parsed.",
    whenBad:
      "A low share means most of the library's reads are lost before reaching a called cell with a panel-recognised barcode.",
  },
  readsPerCell: {
    label: "Mean reads per V(D)J-matched cell",
    description: "The average antigen read depth of one analysed cell.",
    whenBad:
      "A low value means the analysed cells are thinly sequenced for antigen, so each call rests on few reads.",
  },
  floorRemoved: {
    label: "Cell-antigen readings zeroed by the minimum",
    description:
      "Each cell-and-antigen reading below the minimum count is set to zero rather than dropped: too small to be evidence of binding, but still a reading that happened, so it votes not bound. The baseline tag is exempt.",
  },
  medianControlReading: {
    label: "Median control-tag reading per V(D)J-matched cell",
    description:
      "The control tag is declared to bind nothing, so this is the run's own noise floor -- and the number to read when choosing where to put the admissibility gate.",
  },
  cellsSetAside: {
    label: "Cells set aside as sticky",
    description:
      "Cells whose control-tag count is above the admissibility gate. A cell set aside answers nothing at any antigen, so it leaves every verdict rather than voting in one.",
  },
};

/** The description for `id`, or a visible placeholder rather than a blank row. */
export function qcMeasurementDescription(id: string): QcMeasurementDescription {
  return (
    QC_MEASUREMENT_DESCRIPTIONS[id] ?? {
      label: id,
      description: `No description declared for ${id}.`,
    }
  );
}
