/*
 * capture.js — microphone capture worklet.
 *
 * Runs on the audio thread. Resamples the AudioContext's native rate (usually
 * 48000, sometimes 44100) down to the 16 kHz the recogniser wants, converts to
 * signed 16-bit, and posts fixed-size frames to the main thread.
 *
 * WHY A WORKLET AND NOT MediaRecorder: MediaRecorder is the obvious API and it
 * is a trap here. It emits container-framed WebM/Opus blobs on a timeslice,
 * which are painful to feed a streaming recogniser and add hundreds of ms of
 * latency. Raw PCM at 16 kHz is ~32 kB/s — irrelevant for one user — and drops
 * straight into the provider socket.
 *
 * NEVER assume the input is already 16 kHz. `sampleRate` is whatever the
 * hardware gave us, and Bluetooth headsets in particular change it mid-session.
 *
 * An RMS level ships with every frame so the main thread can decide when speech
 * started and stopped. The decision lives there, not here, because it also
 * needs to know whether Lana is currently speaking (barge-in) — state the audio
 * thread has no business tracking.
 */

class CaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetRate = opts.targetRate || 16000;
    this.frameSamples = opts.frameSamples || 1024;

    // Input samples consumed per output sample. Fractional for 44.1 kHz.
    this.step = sampleRate / this.targetRate;

    this.frame = new Int16Array(this.frameSamples);
    this.filled = 0;

    // Resampler carry-over across process() blocks: `frac` is the fractional
    // read position into the next block, `prev` is the final sample of the last
    // one, so interpolation at index -1 has something real to work with. Without
    // these the seams between blocks click audibly.
    this.frac = 0;
    this.prev = 0;

    this.enabled = false;
    this.port.onmessage = (event) => {
      const data = event.data || {};
      if (data.type === "enable") {
        this.enabled = !!data.value;
        if (!this.enabled) {
          this.filled = 0;
          this.frac = 0;
        }
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    if (!this.enabled) return true;

    const channel = input[0];
    const n = channel.length;
    let pos = this.frac;

    while (pos < n) {
      const i0 = Math.floor(pos);
      const i1 = i0 + 1;
      const a = i0 < 0 ? this.prev : channel[i0];
      const b = i1 < n ? channel[i1] : channel[n - 1];
      const t = pos - i0;
      let sample = a + (b - a) * t;

      if (sample > 1) sample = 1;
      else if (sample < -1) sample = -1;
      this.frame[this.filled++] = (sample * 32767) | 0;

      if (this.filled === this.frameSamples) {
        let sum = 0;
        for (let i = 0; i < this.frameSamples; i++) {
          const v = this.frame[i];
          sum += v * v;
        }
        const rms = Math.sqrt(sum / this.frameSamples);

        // Copy: the buffer is transferred, so it cannot be the live one.
        const out = new Int16Array(this.frame);
        this.port.postMessage({ type: "frame", pcm: out.buffer, rms: rms }, [
          out.buffer,
        ]);
        this.filled = 0;
      }

      pos += this.step;
    }

    this.frac = pos - n;
    this.prev = channel[n - 1];
    return true;
  }
}

registerProcessor("capture-processor", CaptureProcessor);
