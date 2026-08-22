import type { CsvMeta } from "@platforma-open/milaboratories.feature-integration.model";
import { parse } from "csv-parse/browser/esm/sync";

/**
 * Reads the tag→feature CSV's headers, each header's distinct values, and its row count.
 *
 * This is the block's ONLY panel parser. Until 2026-08 the same job ran in the workflow, as the
 * emit-csv-meta Python entrypoint, and the dropdowns waited for an upload and a staging exec to fill
 * them. Reading the file here fills them on the pick instead. Nothing downstream re-derives this, so the
 * semantics below are the block's definition of what a panel column and a panel row ARE, not an
 * approximation of some other parser.
 *
 * Parsing is delegated to `csv-parse`, as in blocks/xsv-import — RFC 4180 quoting, doubled quotes,
 * commas and newlines inside quoted fields, and both LF and CRLF endings. Real panel files use CRLF, so
 * that last one is load-bearing. The shape of this function follows readFileForImport in
 * blocks/samples-and-data: bytes in, a value out, and a throw where the file has no header to read.
 */
export function parseTagCsvMeta(bytes: Uint8Array): CsvMeta {
  // Decoding is done here rather than left to csv-parse so the block owns the one decision that a BOM
  // forces. TextDecoder strips a UTF-8 BOM, which is what an Excel-exported panel needs: left in place it
  // becomes part of the first header's name, and every later match against that name fails.
  const text = new TextDecoder("utf-8").decode(bytes);

  // relax_column_count: a panel whose rows are shorter or longer than its header is readable — the value
  // loop below simply finds nothing at the missing indices. Refusing the file would be worse than
  // reading the columns that ARE there.
  const records: string[][] = parse(text, {
    relax_column_count: true,
    skip_empty_lines: true,
  });

  if (records.length === 0)
    throw new Error("The panel CSV is empty: it has no header row and no data rows.");

  // Blank header cells are dropped, so a trailing comma on the header line does not become a nameless
  // column in three dropdowns. The INDEX is kept from the original header, not from the compacted list:
  // dropping cell 1 of `Barcode,,Name` must not make `Name` look like column 1 when its values are at 2.
  //
  // A repeated header keeps both entries in `columns` and resolves to its LAST index for values. Both
  // halves are deliberate: the dropdowns show the file's headers as the file has them, and a later
  // column silently shadowing an earlier one of the same name is the same rule a spreadsheet applies.
  const columns: string[] = [];
  const indexByColumn = new Map<string, number>();
  records[0].forEach((cell, index) => {
    const name = cell.trim();
    if (name === "") return;
    columns.push(name);
    indexByColumn.set(name, index);
  });

  if (columns.length === 0)
    throw new Error(
      "The panel CSV has no usable header: its first row is empty or every heading is blank.",
    );

  const distinct = new Map<string, Set<string>>();
  for (const name of indexByColumn.keys()) distinct.set(name, new Set<string>());

  // A row whose every cell is blank is not a row. Panels exported from a spreadsheet routinely carry a
  // few of these at the end, and counting them would make rowCount disagree with the number of barcodes
  // declared — the comparison the duplicate-mapping gate is built on.
  let rowCount = 0;
  for (let r = 1; r < records.length; r++) {
    const row = records[r];
    if (row.every((cell) => cell.trim() === "")) continue;
    rowCount++;
    for (const [name, index] of indexByColumn) {
      if (index >= row.length) continue;
      const value = row[index].trim();
      if (value === "") continue;
      const seen = distinct.get(name);
      if (seen === undefined) continue;
      seen.add(value);
    }
  }

  // Sorted so the dropdowns are stable: the same panel read twice must offer its values in the same
  // order, whatever order the rows happened to be in.
  const valuesByColumn: Record<string, string[]> = {};
  for (const [name, seen] of distinct) valuesByColumn[name] = [...seen].sort();

  return { columns, valuesByColumn, rowCount };
}
