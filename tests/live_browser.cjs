// Browser lifecycle regression tests; no microphone, network, or paid API calls.
// Run with: node --test tests/live_browser.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../experimental/live/static/app.js"), "utf8");
const flush = async () => {
  for (let i = 0; i < 12; i++) await new Promise(setImmediate);
};

function createBrowser({ failCreate = false, waitMicrophone = false } = {}) {
  const nodes = new Map();
  const timers = new Map();
  const calls = [];
  const beacons = [];
  const windowEvents = {};
  const tracks = [];
  const peers = [];
  let timerId = 0;
  let resolveMicrophone;
  const snapshot = { state: "active", seconds: 3, tasks: [], transcripts: [] };

  class Element {
    constructor() {
      this.textContent = "";
      this.hidden = false;
      this.dataset = {};
      this.attributes = {};
      this.events = {};
      this.children = [];
    }
    addEventListener(name, callback) { this.events[name] = callback; }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name]; }
    replaceChildren(...children) { this.children = children; }
    append(...children) { this.children.push(...children); }
    play() { return Promise.resolve(); }
    pause() {}
  }

  function createStream() {
    const track = {
      enabled: true,
      stopped: false,
      stop() { this.stopped = true; },
    };
    tracks.push(track);
    return { getTracks: () => [track], getAudioTracks: () => [track] };
  }

  class Peer {
    constructor() {
      this.events = {};
      this.iceGatheringState = "complete";
      this.connectionState = "new";
      peers.push(this);
    }
    addEventListener(name, callback) { this.events[name] = callback; }
    removeEventListener() {}
    addTrack() {}
    createDataChannel(label) {
      this.channel = {
        label,
        readyState: "open",
        events: {},
        sent: [],
        addEventListener(name, callback) { this.events[name] = callback; },
        send(data) { this.sent.push(JSON.parse(data)); },
        close() { this.readyState = "closed"; this.events.close?.(); },
      };
      return this.channel;
    }
    createOffer() {
      assert.equal(this.channel.label, "oai-events", "event channel exists before the offer");
      return Promise.resolve({ type: "offer", sdp: "offer-sdp" });
    }
    setLocalDescription(offer) { this.localDescription = offer; return Promise.resolve(); }
    setRemoteDescription(answer) { this.answer = answer; return Promise.resolve(); }
    close() { this.closed = true; }
  }

  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, new Element());
        return nodes.get(id);
      },
      createElement: () => new Element(),
    },
    window: {
      isSecureContext: true,
      RTCPeerConnection: Peer,
      addEventListener(name, callback) { windowEvents[name] = callback; },
    },
    location: { hostname: "localhost" },
    navigator: {
      mediaDevices: {
        getUserMedia() {
          if (waitMicrophone) {
            return new Promise((resolve) => { resolveMicrophone = () => resolve(createStream()); });
          }
          return Promise.resolve(createStream());
        },
      },
      sendBeacon(...args) { beacons.push(args); return true; },
    },
    RTCPeerConnection: Peer,
    MediaStream: class {},
    AbortController,
    Blob,
    setTimeout(callback, delay) {
      const id = ++timerId;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    async fetch(url, options) {
      calls.push({ url, options });
      const ok = !(failCreate && url === "/api/session");
      let data = snapshot;
      if (url === "/api/config") {
        data = { backend: "demo", model: "gpt-live-1", idle_timeout: 90, max_duration: 600 };
      } else if (url === "/api/session") {
        data = ok ? {
          id: "local-" + peers.length,
          session: { id: "live-id" },
          transport: { type: "webrtc", sdp: "answer-sdp" },
        } : { error: "Set OPENAI_API_KEY in the bridge environment." };
      }
      return { ok, status: ok ? 200 : 503, text: async () => JSON.stringify(data) };
    },
  });
  vm.runInContext(source, context);
  return {
    nodes, timers, calls, beacons, tracks, peers, snapshot,
    click: (id) => nodes.get(id).events.click(),
    sendEvent: (event, peer = peers.at(-1)) => peer.channel.events.message({ data: JSON.stringify(event) }),
    resolveMicrophone: () => resolveMicrophone(),
    unload: () => windowEvents.pagehide(),
    refresh: () => vm.runInContext("poll(watched)", context),
  };
}

test("start negotiates audio once, waits for readiness, and supports microphone mute", async () => {
  const browser = createBrowser();
  await flush();
  assert.equal(browser.nodes.get("start").disabled, false);
  browser.click("start");
  browser.click("start");
  await flush();
  const creations = browser.calls.filter((call) => call.url === "/api/session");
  assert.equal(creations.length, 1);
  assert.equal(JSON.parse(creations[0].options.body).sdp, "offer-sdp");
  assert.equal(browser.peers[0].answer.sdp, "answer-sdp");
  assert.equal(browser.peers[0].channel.sent.length, 0, "HTTP starts Live; browser sends no session.start");
  assert.equal(browser.nodes.get("status").textContent, "Connecting");
  browser.sendEvent({ type: "session.started" });
  assert.equal(browser.nodes.get("status").textContent, "You're live");
  browser.click("mute");
  assert.equal(browser.tracks[0].enabled, false);
  browser.click("mute");
  assert.equal(browser.tracks[0].enabled, true);
  browser.unload();
});

test("End keeps audio alive until finalization and ignores events from an old session", async () => {
  const browser = createBrowser();
  await flush();
  browser.click("start");
  await flush();
  browser.click("end");
  await flush();
  assert.equal(browser.tracks[0].stopped, false);
  assert.ok(browser.calls.some((call) => call.url.endsWith("/close")));
  browser.snapshot.state = "closed";
  browser.snapshot.finalized = true;
  browser.snapshot.usage = { seconds: 3 };
  browser.sendEvent({ type: "session.closed", usage: { seconds: 3 } });
  await flush();
  assert.equal(browser.tracks[0].stopped, true);
  assert.equal(browser.peers[0].closed, true);
  assert.equal(browser.nodes.get("start").disabled, false);
  assert.match(browser.nodes.get("usage").textContent, /seconds/);
  browser.snapshot.state = "active";
  browser.snapshot.finalized = false;
  browser.click("start");
  await flush();
  browser.sendEvent({ type: "session.closed" }, browser.peers[0]);
  assert.equal(browser.tracks[1].stopped, false);
  browser.unload();
});

test("close timeout stops local audio and reports unconfirmed final usage", async () => {
  const browser = createBrowser();
  await flush();
  browser.click("start");
  await flush();
  browser.click("end");
  await flush();
  const timer = [...browser.timers.values()].find((entry) => entry.delay === 15000);
  assert.ok(timer, "graceful close has a bounded timeout");
  timer.callback();
  assert.equal(browser.tracks[0].stopped, true);
  assert.equal(browser.peers[0].closed, true);
  assert.match(browser.nodes.get("message").textContent, /unconfirmed/);
  browser.unload();
});

test("background results continue updating after audio ends", async () => {
  const browser = createBrowser();
  await flush();
  browser.click("start");
  await flush();
  browser.snapshot.tasks = [{ id: "job", status: "running", request: "Home request" }];
  browser.snapshot.state = "closed";
  browser.snapshot.finalized = true;
  browser.sendEvent({ type: "session.closed" });
  await flush();
  assert.equal(browser.tracks[0].stopped, true);
  assert.equal(browser.nodes.get("start").disabled, true, "wait for the running HA job before another session");
  browser.snapshot.tasks[0].status = "completed";
  browser.snapshot.tasks[0].result = "Done";
  browser.refresh();
  await flush();
  assert.equal(browser.nodes.get("start").disabled, false);
  assert.equal(browser.nodes.get("tasks").children[0].children.at(-1).textContent, "Done");
  browser.unload();
});

test("a bridge 503 frees microphone and peer and explains the missing configuration", async () => {
  const browser = createBrowser({ failCreate: true });
  await flush();
  browser.click("start");
  await flush();
  assert.equal(browser.tracks[0].stopped, true);
  assert.equal(browser.peers[0].closed, true);
  assert.equal(browser.nodes.get("start").disabled, false);
  assert.match(browser.nodes.get("message").textContent, /OPENAI_API_KEY/);
  browser.unload();
});

test("microphone permission resolving after unload cannot leak a recording track", async () => {
  const browser = createBrowser({ waitMicrophone: true });
  await flush();
  browser.click("start");
  browser.unload();
  browser.resolveMicrophone();
  await flush();
  assert.equal(browser.tracks[0].stopped, true);
  assert.equal(browser.peers.length, 0);
});
