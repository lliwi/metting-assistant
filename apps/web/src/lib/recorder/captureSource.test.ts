import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  meetingSource,
  microphoneSource,
  NoDisplayAudioError,
  requestDisplayAudio,
} from "./captureSource";

class FakeTrack {
  readyState = "live";
  constructor(readonly kind: "audio" | "video") {}
  stop = vi.fn(() => {
    this.readyState = "ended";
  });
}

class FakeStream {
  constructor(public tracks: FakeTrack[]) {}
  getTracks = () => [...this.tracks];
  getAudioTracks = () => this.tracks.filter((t) => t.kind === "audio");
  getVideoTracks = () => this.tracks.filter((t) => t.kind === "video");
  removeTrack = (t: FakeTrack) => {
    this.tracks = this.tracks.filter((x) => x !== t);
  };
}

const asStream = (s: FakeStream) => s as unknown as MediaStream;

class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  sources: FakeStream[] = [];
  destination = { stream: new FakeStream([new FakeTrack("audio")]) };
  close = vi.fn(async () => undefined);
  resume = vi.fn(async () => undefined);
  constructor() {
    FakeAudioContext.instances.push(this);
  }
  createMediaStreamDestination() {
    return this.destination;
  }
  createMediaStreamSource(stream: FakeStream) {
    this.sources.push(stream);
    return { connect: vi.fn() };
  }
}

describe("captureSource", () => {
  beforeEach(() => {
    FakeAudioContext.instances = [];
    vi.stubGlobal("AudioContext", FakeAudioContext);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("micrófono: graba el propio stream y lo libera al terminar", () => {
    const mic = new FakeStream([new FakeTrack("audio")]);
    const source = microphoneSource(asStream(mic));
    expect(source.stream).toBe(mic);
    expect(source.inputs).toEqual(mic.tracks);
    source.dispose();
    expect(mic.tracks[0].stop).toHaveBeenCalled();
  });

  it("reunión: mezcla pestaña y micrófono, vigila ambos y lo libera todo una vez", () => {
    const display = new FakeStream([new FakeTrack("audio")]);
    const mic = new FakeStream([new FakeTrack("audio")]);
    const source = meetingSource(asStream(display), asStream(mic));
    const [context] = FakeAudioContext.instances;

    expect(context.sources).toEqual([display, mic]);
    expect(source.stream).toBe(context.destination.stream);
    expect(source.inputs).toEqual([display.tracks[0], mic.tracks[0]]);

    source.dispose();
    source.dispose();
    expect(display.tracks[0].stop).toHaveBeenCalledTimes(1);
    expect(mic.tracks[0].stop).toHaveBeenCalledTimes(1);
    expect(context.close).toHaveBeenCalledTimes(1);
  });

  it("reunión sin micrófono: solo la pestaña", () => {
    const display = new FakeStream([new FakeTrack("audio")]);
    const source = meetingSource(asStream(display), null);
    expect(FakeAudioContext.instances[0].sources).toEqual([display]);
    expect(source.inputs).toEqual(display.tracks);
  });

  it("descarta el vídeo compartido y exige audio", async () => {
    const withAudio = new FakeStream([
      new FakeTrack("video"),
      new FakeTrack("audio"),
    ]);
    const video = withAudio.tracks[0];
    vi.stubGlobal("navigator", {
      mediaDevices: { getDisplayMedia: vi.fn(async () => withAudio) },
    });
    const display = await requestDisplayAudio();
    expect(video.stop).toHaveBeenCalled();
    expect(display.getVideoTracks()).toHaveLength(0);

    const silent = new FakeStream([new FakeTrack("video")]);
    vi.stubGlobal("navigator", {
      mediaDevices: { getDisplayMedia: vi.fn(async () => silent) },
    });
    await expect(requestDisplayAudio()).rejects.toBeInstanceOf(
      NoDisplayAudioError,
    );
  });
});
