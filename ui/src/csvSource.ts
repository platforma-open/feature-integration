import type { CsvMeta } from "@platforma-open/milaboratories.feature-integration.model";
import type {
  ImportFileHandle,
  LocalBlobHandleAndSize,
  LocalImportFileHandle,
} from "@platforma-sdk/model";
import { getRawPlatformaInstance, isImportFileHandleUpload } from "@platforma-sdk/model";
import { ReactiveFileContent } from "@platforma-sdk/ui-vue";
import type { ComputedRef } from "vue";
import { computed } from "vue";
import { parseTagCsvMeta } from "./csvMeta";

/**
 * The panel CSV's metadata, read straight off the user's disk.
 *
 * Returns undefined for a REMOTE pick, which is not a failure: an `index://` handle names a file in
 * remote storage that this machine cannot open, so those picks are served by the blob path below
 * instead. Any real failure — the file vanished between the pick and the read, the bytes are not a
 * readable CSV — throws, and the caller shows it.
 *
 * Same shape as blocks/immune-assay-data (setFile) and blocks/synthetic-repertoire-profiler: guard on
 * isImportFileHandleUpload, then read through the ls driver.
 */
export async function readLocalCsvMeta(handle: ImportFileHandle): Promise<CsvMeta | undefined> {
  if (!isImportFileHandleUpload(handle)) return undefined;

  // The cast is unavoidable and is the one assumption this module makes. isImportFileHandleUpload proves
  // the handle is an `upload://` one, but LocalImportFileHandle is a SEPARATE brand meaning "openable on
  // this machine, in this session", and no SDK predicate tests for it. What makes the cast sound is the
  // caller: this runs synchronously from the file-picker gesture, so the handle came from the dialog this
  // session just opened. Never call this with a handle read back out of `data` — a project reopened on
  // another machine carries handles whose files are not here.
  const localHandle = handle as LocalImportFileHandle;
  const bytes = await getRawPlatformaInstance().lsDriver.getLocalFileContent(localHandle);
  return parseTagCsvMeta(bytes);
}

/**
 * The bytes of the panel CSV as the prerun imported it, for picks this machine cannot read from disk.
 *
 * The prerun already exports the uploaded CSV — it has to, so that staging demands the blob and the
 * upload starts before production needs it — so this costs the workflow nothing. It serves a remote pick,
 * and it also serves a project opened somewhere the original file never existed.
 *
 * Mirrors the assayFileBytes computed in blocks/immune-assay-data, which feeds the same parser from the
 * same kind of handle. Bytes rather than text because parseTagCsvMeta owns the decoding.
 */
export function useRemoteCsvBytes(
  getHandle: () => LocalBlobHandleAndSize | undefined,
): ComputedRef<Uint8Array | undefined> {
  const fileContent = ReactiveFileContent.useGlobal();
  return computed(() => {
    const handle = getHandle();
    if (handle === undefined) return undefined;
    return fileContent.getContentBytes(handle.handle).value;
  });
}
