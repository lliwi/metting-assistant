import { openDB, type DBSchema, type IDBPDatabase } from "idb";

/**
 * Copia local de cada segmento hasta que el servidor confirma su checksum.
 * Sobrevive a cortes de red, recargas y cierres del navegador.
 */

export type StoredSegment = {
  id: string; // UUID generado en cliente: clave de idempotencia en el servidor
  recordingId: string;
  track: number;
  seq: number;
  durationMs: number;
  mimeType: string;
  sha256: string;
  blob: Blob;
  createdAt: number;
  status: "pending" | "failed";
  // Usuario que grabó. En un dispositivo compartido, la cola solo sube (y muestra) lo de quien
  // tiene la sesión. Ausente en segmentos guardados antes de existir este campo.
  ownerId?: string;
  attempts: number;
  error?: string;
};

/** Estado de una captura en este dispositivo: cuántos segmentos se produjeron por pista. */
export type CaptureState = {
  recordingId: string;
  tracks: Record<number, number>;
  // El usuario pulsó "finalizar": cerrar en el servidor en cuanto no quede nada pendiente.
  finishRequested: boolean;
  updatedAt: number;
};

/** undefined: sin filtrar. null: nadie con sesión (nada es suyo). string: id de usuario. */
export type Owner = string | null | undefined;

export function belongsTo(segment: StoredSegment, owner: Owner): boolean {
  if (owner === undefined) return true;
  if (owner === null) return false;
  return segment.ownerId === undefined || segment.ownerId === owner;
}

interface RecorderDB extends DBSchema {
  segments: {
    key: string;
    value: StoredSegment;
    indexes: { byRecording: string; byCreated: number };
  };
  captures: {
    key: string;
    value: CaptureState;
  };
}

const DB_NAME = "metting-agent";
const DB_VERSION = 1;

export class SegmentStore {
  private db: Promise<IDBPDatabase<RecorderDB>>;

  constructor(name = DB_NAME) {
    this.db = openDB<RecorderDB>(name, DB_VERSION, {
      upgrade(db) {
        const segments = db.createObjectStore("segments", { keyPath: "id" });
        segments.createIndex("byRecording", "recordingId");
        segments.createIndex("byCreated", "createdAt");
        db.createObjectStore("captures", { keyPath: "recordingId" });
      },
    });
  }

  /** Guarda el segmento y actualiza el recuento de la captura en una sola transacción. */
  async addSegment(segment: StoredSegment): Promise<void> {
    const db = await this.db;
    const tx = db.transaction(["segments", "captures"], "readwrite");
    await tx.objectStore("segments").put(segment);
    const captures = tx.objectStore("captures");
    const capture = (await captures.get(segment.recordingId)) ?? {
      recordingId: segment.recordingId,
      tracks: {},
      finishRequested: false,
      updatedAt: Date.now(),
    };
    capture.tracks[segment.track] = Math.max(
      capture.tracks[segment.track] ?? 0,
      segment.seq + 1,
    );
    capture.updatedAt = Date.now();
    await captures.put(capture);
    await tx.done;
  }

  /** El segmento pendiente más antiguo (orden de grabación) del usuario indicado. */
  async nextPending(owner?: Owner): Promise<StoredSegment | undefined> {
    const db = await this.db;
    let cursor = await db
      .transaction("segments")
      .store.index("byCreated")
      .openCursor();
    while (cursor) {
      if (cursor.value.status === "pending" && belongsTo(cursor.value, owner)) {
        return cursor.value;
      }
      cursor = await cursor.continue();
    }
    return undefined;
  }

  async listByRecording(recordingId: string): Promise<StoredSegment[]> {
    const db = await this.db;
    return db.getAllFromIndex("segments", "byRecording", recordingId);
  }

  async listAll(owner?: Owner): Promise<StoredSegment[]> {
    const db = await this.db;
    return (await db.getAll("segments")).filter((s) => belongsTo(s, owner));
  }

  async update(segment: StoredSegment): Promise<void> {
    const db = await this.db;
    await db.put("segments", segment);
  }

  async delete(id: string): Promise<void> {
    const db = await this.db;
    await db.delete("segments", id);
  }

  async getCapture(recordingId: string): Promise<CaptureState | undefined> {
    const db = await this.db;
    return db.get("captures", recordingId);
  }

  async listCaptures(): Promise<CaptureState[]> {
    const db = await this.db;
    return db.getAll("captures");
  }

  async ensureTrack(recordingId: string, track: number): Promise<void> {
    const db = await this.db;
    const capture = (await db.get("captures", recordingId)) ?? {
      recordingId,
      tracks: {},
      finishRequested: false,
      updatedAt: Date.now(),
    };
    capture.tracks[track] ??= 0;
    capture.updatedAt = Date.now();
    await db.put("captures", capture);
  }

  async requestFinish(recordingId: string): Promise<void> {
    const db = await this.db;
    const capture = await db.get("captures", recordingId);
    if (!capture) return;
    capture.finishRequested = true;
    capture.updatedAt = Date.now();
    await db.put("captures", capture);
  }

  async deleteCapture(recordingId: string): Promise<void> {
    const db = await this.db;
    await db.delete("captures", recordingId);
  }

  /** Pide al navegador que no borre estos datos ante presión de almacenamiento. */
  static async requestPersistence(): Promise<boolean> {
    if (typeof navigator === "undefined" || !navigator.storage?.persist)
      return false;
    return navigator.storage
      .persisted()
      .then((p) => p || navigator.storage.persist());
  }
}
