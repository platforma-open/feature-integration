import { describe, expect, it } from "vitest";
// parseTagCsvMeta lives in the block UI package; imported directly (pure function, no Vue or driver deps).
import { parseTagCsvMeta } from "../../ui/src/csvMeta";

const bytes = (s: string) => new TextEncoder().encode(s);
const parse = (s: string) => parseTagCsvMeta(bytes(s));

// These cases describe behaviour the block's dropdowns and its duplicate-mapping gate depend on. There is
// only one parser, so they are not an agreement check between two.
describe("parseTagCsvMeta — headers", () => {
  it("keeps the file's header order", () => {
    const m = parse("Barcode,Name,Type\nAAA,Ag1,Target\n");
    expect(m.columns).toStrictEqual(["Barcode", "Name", "Type"]);
  });

  it("trims surrounding whitespace from headers and values", () => {
    const m = parse("  Barcode , Name \nAAA ,  Ag1\n");
    expect(m.columns).toStrictEqual(["Barcode", "Name"]);
    expect(m.valuesByColumn["Barcode"]).toStrictEqual(["AAA"]);
    expect(m.valuesByColumn["Name"]).toStrictEqual(["Ag1"]);
  });

  it("drops blank header cells without shifting the columns that follow", () => {
    // The trailing/middle empty heading must not make `Name` read its values from the wrong index.
    const m = parse("Barcode,,Name\nAAA,junk,Ag1\n");
    expect(m.columns).toStrictEqual(["Barcode", "Name"]);
    expect(m.valuesByColumn["Name"]).toStrictEqual(["Ag1"]);
  });

  it("keeps both entries for a duplicated header and reads values from the last one", () => {
    const m = parse("Name,Name\nfirst,second\n");
    expect(m.columns).toStrictEqual(["Name", "Name"]);
    expect(m.valuesByColumn["Name"]).toStrictEqual(["second"]);
  });
});

describe("parseTagCsvMeta — values", () => {
  it("deduplicates and sorts each column's values", () => {
    const m = parse("Name\nzebra\nalpha\nzebra\nmid\n");
    expect(m.valuesByColumn["Name"]).toStrictEqual(["alpha", "mid", "zebra"]);
  });

  it("ignores blank cells rather than collecting an empty value", () => {
    const m = parse("Barcode,Name\nAAA,\nCCC,Ag2\n");
    expect(m.valuesByColumn["Name"]).toStrictEqual(["Ag2"]);
    expect(m.valuesByColumn["Barcode"]).toStrictEqual(["AAA", "CCC"]);
  });

  it("tolerates a row shorter than the header", () => {
    const m = parse("Barcode,Name,Type\nAAA\nCCC,Ag2,Target\n");
    expect(m.valuesByColumn["Barcode"]).toStrictEqual(["AAA", "CCC"]);
    expect(m.valuesByColumn["Type"]).toStrictEqual(["Target"]);
    expect(m.rowCount).toBe(2);
  });

  it("emits an empty value list for every column of a header-only file", () => {
    const m = parse("Barcode,Name\n");
    expect(m.columns).toStrictEqual(["Barcode", "Name"]);
    expect(m.valuesByColumn).toStrictEqual({ Barcode: [], Name: [] });
    expect(m.rowCount).toBe(0);
  });
});

describe("parseTagCsvMeta — rowCount", () => {
  it("counts data rows and not the header", () => {
    expect(parse("Barcode\nAAA\nCCC\nGGG\n").rowCount).toBe(3);
  });

  it("ignores trailing blank rows", () => {
    expect(parse("Barcode\nAAA\nCCC\n\n\n").rowCount).toBe(2);
  });

  it("ignores a row whose every cell is blank, not only truly empty lines", () => {
    expect(parse("Barcode,Name\nAAA,Ag1\n  ,  \nCCC,Ag2\n").rowCount).toBe(2);
  });

  // This is the comparison the duplicate-mapping gate is built on: more rows than distinct barcodes means
  // a barcode is declared more than once, which is legal only for a sample-keyed panel.
  it("exceeds the distinct barcode count when a barcode is repeated", () => {
    const m = parse("Barcode,Sample\nAAA,s1\nAAA,s2\nCCC,s1\n");
    expect(m.rowCount).toBe(3);
    expect(m.valuesByColumn["Barcode"]).toStrictEqual(["AAA", "CCC"]);
  });
});

describe("parseTagCsvMeta — real-world dialects", () => {
  it("reads CRLF line endings", () => {
    // Verified against a real customer panel CSV, which is CRLF.
    const m = parse("Barcode,Name\r\nAAA,Ag1\r\nCCC,Ag2\r\n");
    expect(m.columns).toStrictEqual(["Barcode", "Name"]);
    expect(m.valuesByColumn["Name"]).toStrictEqual(["Ag1", "Ag2"]);
    expect(m.rowCount).toBe(2);
  });

  it("strips a UTF-8 BOM instead of gluing it to the first header", () => {
    // An Excel-exported CSV carries a BOM. Left in place it becomes part of the first header's name and
    // every later match against that name fails.
    const m = parse("﻿Barcode,Name\nAAA,Ag1\n");
    expect(m.columns).toStrictEqual(["Barcode", "Name"]);
    expect(m.valuesByColumn["Barcode"]).toStrictEqual(["AAA"]);
  });

  it("honours quoting: commas, newlines and doubled quotes inside a field", () => {
    const m = parse('Barcode,Name\nAAA,"Ag1, variant b"\nCCC,"multi\nline"\nGGG,"say ""hi"""\n');
    // Sorted by code point, so "multi\nline" precedes 'say "hi"'.
    expect(m.valuesByColumn["Name"]).toStrictEqual(["Ag1, variant b", "multi\nline", 'say "hi"']);
    expect(m.rowCount).toBe(3);
  });
});

describe("parseTagCsvMeta — refusals", () => {
  it("throws on an empty file rather than reporting no columns", () => {
    expect(() => parse("")).toThrow(/empty/i);
  });

  it("throws where every heading is blank", () => {
    expect(() => parse(",,\nAAA,BBB,CCC\n")).toThrow(/header/i);
  });
});
