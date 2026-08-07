/*
 * playback.js — speaker playback worklet.
 *
 * A ring buffer the main thread pushes PCM into and the audio thread drains.
 *
 * WHY NOT <audio>, MediaSource, OR BLOB URLs: all three fail the one hard
 * requirement — barge-in must drop queued audio within ~100 ms. None of them
 * can be flushed promptly; they are built to play what they were given. A ring
 * buffer we own can be zeroed in a single message, which is the whole reason
 * the downlink is raw PCM rather than MP3 (a decode step cannot be flushed
 * either).
 *
 * `drained` is posted exactly once per utterance, when the buffer empties AND
 * the server has said no more audio is coming. That message becomes
 * playback_finished on the wire, which is the only way the backend learns an
 * utterance was actually heard — it is what tells a completed reply apart from
 * an interrupted one.
 */

class PlaybackProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    // ~30 s at the downlink rate. Generous: reading a long email aloud can
    // outrun playback, and overflowing would drop the tail of an utterance.
    this.capacity = opts.capacity || 24000 * 30;
    this.ring = new Float32Array(this.capacity);
    this.read = 0;
    this.write = 0;
    this.count = 0;

    this.ended = false;
    this.drainedSent = false;

    this.port.onmessage = (event) => {
      const data = event.data || {};

      if (data.type === "push") {
        const pcm = new Int16Array(data.pcm);
        for (let i = 0; i < pcm.length; i++) {
          if (this.count >= this.capacity) break; // full: drop rather than wrap
          this.ring[this.write] = pcm[i] / 32768;
          this.write = (this.write + 1) % this.capacity;
          this.count++;
        }
      } else if (data.type === "end") {
        // No more audio is coming; report drained once the buffer empties.
        this.ended = true;
      } else if (data.type === "flush") {
        // BARGE-IN. Everything queued is discarded immediately, and no
        // `drained` is sent — nothing was heard to the end, so claiming
        // otherwise would tell the backend a lie it records in history.
        this.read = 0;
        this.write = 0;
        this.count = 0;
        this.ended = false;
        this.drainedSent = true;
      } else if (data.type === "reset") {
        this.read = 0;
        this.write = 0;
        this.count = 0;
        this.ended = false;
        this.drainedSent = false;
      }
    };
  }

  process(inputs, outputs) {
    const output = outputs[0];
    if (!output || output.length === 0) return true;
    const channel = output[0];

    for (let i = 0; i < channel.length; i++) {
      if (this.count > 0) {
        channel[i] = this.ring[this.read];
        this.read = (this.read + 1) % this.capacity;
        this.count--;
      } else {
        channel[i] = 0;
      }
    }

    // Mirror to any additional output channels so the utterance is not quieter
    // on one side of a stereo device.
    for (let c = 1; c < output.length; c++) {
      output[c].set(channel);
    }

    // No `started` guard here: `ended` is only set once the server has sent
    // every chunk, so an utterance that produced no audio at all must still
    // report drained — otherwise speak() blocks until its grace period expires
    // and then wrongly reports the reply as lost.
    if (this.ended && this.count === 0 && !this.drainedSent) {
      this.drainedSent = true;
      this.port.postMessage({ type: "drained" });
    }

    return true;
  }
}

registerProcessor("playback-processor", PlaybackProcessor);
