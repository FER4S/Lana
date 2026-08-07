/*
 * audio.js — the browser side of Lana's voice.
 *
 * Owns the microphone, the /audio socket, and the two worklets. Exposes a small
 * surface to app.js: connect(), disconnect(), startTurn(), and a few callbacks.
 *
 * TWO AUDIO CONTEXTS, ON PURPOSE. Capture runs at the hardware's native rate
 * (the worklet resamples to 16 kHz for the recogniser). Playback gets its own
 * context created at whatever rate the speech provider says it is sending, so
 * the downlink needs no resampling at all — one less place to introduce
 * artefacts, and one less thing to get wrong when the provider changes.
 *
 * ECHO CANCELLATION IS LOAD-BEARING. echoCancellation:true is what stops Lana's
 * own voice bleeding into the microphone and being heard as the user talking
 * over her. On the old desktop build this was unsolvable without it — Lana had
 * to be forbidden from saying her own name, because her voice on the speakers
 * re-triggered the wake word. The browser gives us AEC for free. Do not turn it
 * off to "improve audio quality".
 *
 * SECURE CONTEXT REQUIRED. getUserMedia and AudioWorklet both need HTTPS.
 * localhost and 127.0.0.1 count, so local development needs no certificate;
 * every other host does. In an iframe the frame needs allow="microphone" AND
 * the page needs a Permissions-Policy header delegating it. Miss either and the
 * microphone fails silently with no console error.
 */

(function (global) {
  "use strict";

  var UPLINK_RATE = 16000;
  var FRAME_SAMPLES = 1024; // ~64 ms at 16 kHz

  // Speech detection. The frame is 64 ms, so these are in units of that.
  var SILENCE_FRAMES_TO_END_TURN = 12; // ~0.75 s — replaces the old fixed 2 s
  var SPEECH_FRAMES_TO_BARGE_IN = 3; // ~0.2 s, enough to reject a cough/click
  var NOISE_FLOOR_ALPHA = 0.05; // how fast the ambient estimate adapts
  var SPEECH_FACTOR = 3.0; // speech is this much above the floor
  var MIN_SPEECH_RMS = 180; // absolute floor, guards a silent room

  function LanaAudio(options) {
    this.opts = options || {};
    this.ws = null;
    this.stream = null;

    this.captureCtx = null;
    this.captureNode = null;
    this.playbackCtx = null;
    this.playbackNode = null;
    this.playbackRate = 0;

    this.listening = false;
    this.speaking = false;

    this.noiseFloor = 0;
    this.silentFrames = 0;
    this.speechFrames = 0;
    this.sawSpeech = false;

    this.onstate = this.opts.onstate || function () {};
    this.onerror = this.opts.onerror || function () {};
  }

  /* ── lifecycle ──────────────────────────────────────────────────────── */

  LanaAudio.prototype.connect = function (token) {
    var self = this;
    if (!global.isSecureContext) {
      this.onerror(
        "Microphone access needs a secure context (HTTPS, or localhost)."
      );
      return Promise.reject(new Error("insecure context"));
    }

    return this._openMic()
      .then(function () {
        return self._openSocket(token);
      })
      .then(function () {
        self.onstate("connected");
      });
  };

  LanaAudio.prototype.disconnect = function () {
    this._setCapture(false);
    if (this.ws) {
      try {
        this.ws.close();
      } catch (e) {}
      this.ws = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach(function (t) {
        t.stop();
      });
      this.stream = null;
    }
    if (this.captureCtx) {
      this.captureCtx.close();
      this.captureCtx = null;
    }
    if (this.playbackCtx) {
      this.playbackCtx.close();
      this.playbackCtx = null;
      this.playbackNode = null;
      this.playbackRate = 0;
    }
    this.onstate("disconnected");
  };

  /* Push-to-talk. Also the gesture that satisfies autoplay policy: a browser
   * will not let us produce sound until the user has interacted with the page,
   * so the first click both starts the turn and unlocks playback. */
  LanaAudio.prototype.startTurn = function () {
    if (this.playbackCtx && this.playbackCtx.state === "suspended") {
      this.playbackCtx.resume();
    }
    if (this.captureCtx && this.captureCtx.state === "suspended") {
      this.captureCtx.resume();
    }
    this._send({ type: "start_turn" });
  };

  /* ── microphone ─────────────────────────────────────────────────────── */

  LanaAudio.prototype._openMic = function () {
    var self = this;
    return global.navigator.mediaDevices
      .getUserMedia({
        audio: {
          echoCancellation: true, // see the header comment — load-bearing
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      })
      .then(function (stream) {
        self.stream = stream;
        var Ctx = global.AudioContext || global.webkitAudioContext;
        self.captureCtx = new Ctx();
        return self.captureCtx.audioWorklet
          .addModule("/ui/worklets/capture.js")
          .then(function () {
            var source = self.captureCtx.createMediaStreamSource(stream);
            self.captureNode = new global.AudioWorkletNode(
              self.captureCtx,
              "capture-processor",
              {
                numberOfInputs: 1,
                numberOfOutputs: 0,
                processorOptions: {
                  targetRate: UPLINK_RATE,
                  frameSamples: FRAME_SAMPLES,
                },
              }
            );
            self.captureNode.port.onmessage = function (event) {
              self._onFrame(event.data);
            };
            source.connect(self.captureNode);
          });
      });
  };

  LanaAudio.prototype._setCapture = function (on) {
    this.listening = on;
    if (!on) {
      this.silentFrames = 0;
      this.speechFrames = 0;
      this.sawSpeech = false;
    }
    if (this.captureNode) {
      this.captureNode.port.postMessage({ type: "enable", value: on });
    }
  };

  /* The speech state machine. Deliberately here rather than in the worklet:
   * it needs to know whether Lana is currently speaking, which is connection
   * state, not audio state. */
  LanaAudio.prototype._onFrame = function (data) {
    if (!data || data.type !== "frame") return;
    var rms = data.rms;

    // Adapt the ambient estimate only while nobody is talking, otherwise speech
    // drags the floor up until it stops registering as speech.
    var threshold = Math.max(this.noiseFloor * SPEECH_FACTOR, MIN_SPEECH_RMS);
    var isSpeech = rms > threshold;
    if (!isSpeech) {
      this.noiseFloor =
        this.noiseFloor === 0
          ? rms
          : this.noiseFloor * (1 - NOISE_FLOOR_ALPHA) + rms * NOISE_FLOOR_ALPHA;
    }

    if (this.speaking) {
      // BARGE-IN. The microphone is echo-cancelled, so what we hear here is the
      // user, not Lana — that assumption is exactly what AEC buys us.
      if (isSpeech) {
        this.speechFrames++;
        if (this.speechFrames >= SPEECH_FRAMES_TO_BARGE_IN) {
          this._bargeIn();
        }
      } else {
        this.speechFrames = 0;
      }
      return;
    }

    if (!this.listening) return;

    this._sendBytes(data.pcm);

    if (isSpeech) {
      this.sawSpeech = true;
      this.silentFrames = 0;
    } else if (this.sawSpeech) {
      this.silentFrames++;
      if (this.silentFrames >= SILENCE_FRAMES_TO_END_TURN) {
        this._setCapture(false);
        this._send({ type: "end_turn" });
      }
    }
  };

  LanaAudio.prototype._bargeIn = function () {
    this.speaking = false;
    this.speechFrames = 0;
    this._setCaptureForBargeIn(false);
    if (this.playbackNode) {
      this.playbackNode.port.postMessage({ type: "flush" });
    }
    this._send({ type: "playback_aborted" });
    this.onstate("interrupted");
  };

  /* ── playback ───────────────────────────────────────────────────────── */

  LanaAudio.prototype._ensurePlayback = function (rate) {
    var self = this;
    if (this.playbackCtx && this.playbackRate === rate) {
      this.playbackNode.port.postMessage({ type: "reset" });
      return Promise.resolve();
    }
    if (this.playbackCtx) this.playbackCtx.close();

    var Ctx = global.AudioContext || global.webkitAudioContext;
    // Created AT the provider's rate, so the downlink never needs resampling.
    this.playbackCtx = new Ctx({ sampleRate: rate });
    this.playbackRate = rate;

    return this.playbackCtx.audioWorklet
      .addModule("/ui/worklets/playback.js")
      .then(function () {
        self.playbackNode = new global.AudioWorkletNode(
          self.playbackCtx,
          "playback-processor",
          {
            numberOfInputs: 0,
            numberOfOutputs: 1,
            outputChannelCount: [1],
            processorOptions: { capacity: rate * 30 },
          }
        );
        self.playbackNode.port.onmessage = function (event) {
          if (event.data && event.data.type === "drained") {
            self.speaking = false;
            // Capture was left running through playback for barge-in; stop it
            // now, or the worklet keeps analysing frames nobody consumes until
            // the next turn.
            self._setCaptureForBargeIn(false);
            self._send({ type: "playback_finished" });
            self.onstate("spoken");
          }
        };
        self.playbackNode.connect(self.playbackCtx.destination);
      });
  };

  /* ── socket ─────────────────────────────────────────────────────────── */

  LanaAudio.prototype._openSocket = function (token) {
    var self = this;
    return new Promise(function (resolve, reject) {
      var scheme = global.location.protocol === "https:" ? "wss" : "ws";
      var url =
        scheme +
        "://" +
        global.location.host +
        "/audio?token=" +
        encodeURIComponent(token);

      var ws = new global.WebSocket(url);
      ws.binaryType = "arraybuffer";
      self.ws = ws;

      ws.onopen = function () {
        self._send({
          type: "hello",
          sample_rate: UPLINK_RATE,
        });
        resolve();
      };

      ws.onmessage = function (event) {
        if (typeof event.data === "string") {
          self._onControl(JSON.parse(event.data));
        } else if (self.playbackNode) {
          self.playbackNode.port.postMessage(
            { type: "push", pcm: event.data },
            [event.data]
          );
        }
      };

      ws.onerror = function () {
        reject(new Error("audio socket failed"));
      };

      ws.onclose = function (event) {
        self._setCapture(false);
        self.speaking = false;
        // A rejected token closes the handshake BEFORE it completes, and the
        // browser reports 1006 — never the code the server passed. So 1006 here
        // means "refused", not "network blip": reconnecting would loop forever.
        // The /events socket shipped with exactly this bug.
        if (event.code === 1006) {
          self.onerror("Audio connection refused — check the token.");
        } else if (event.code === 4001) {
          self.onerror("Lana is already open in another tab.");
        }
        self.onstate("disconnected");
      };
    });
  };

  LanaAudio.prototype._onControl = function (msg) {
    var self = this;
    switch (msg.type) {
      case "listen_start":
        this.noiseFloor = 0; // recalibrate per turn; rooms change
        this._setCapture(true);
        this.onstate("listening");
        break;

      case "listen_stop":
        this._setCapture(false);
        this.onstate("thinking");
        break;

      case "speak_begin":
        this._ensurePlayback(msg.sample_rate).then(function () {
          self.speaking = true;
          self.speechFrames = 0;
          // Capture stays ON while Lana speaks — that is what makes barge-in
          // possible at all. The AEC keeps her own voice out of it.
          self._setCaptureForBargeIn(true);
          self.onstate("speaking");
        });
        break;

      case "speak_end":
        if (this.playbackNode) {
          this.playbackNode.port.postMessage({ type: "end" });
        }
        break;

      case "flush":
        this.speaking = false;
        if (this.playbackNode) {
          this.playbackNode.port.postMessage({ type: "flush" });
        }
        break;

      case "busy":
        this.onerror(msg.message || "Lana is already open in another tab.");
        break;

      case "error":
        this.onerror(msg.message || "Audio error.");
        break;
    }
  };

  /* Enables the worklet without setting `listening`, so frames are analysed for
   * barge-in but not streamed to the server. */
  LanaAudio.prototype._setCaptureForBargeIn = function (on) {
    if (this.captureNode) {
      this.captureNode.port.postMessage({ type: "enable", value: on });
    }
  };

  LanaAudio.prototype._send = function (obj) {
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify(obj));
    }
  };

  LanaAudio.prototype._sendBytes = function (buffer) {
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(buffer);
    }
  };

  global.LanaAudio = LanaAudio;
})(window);
