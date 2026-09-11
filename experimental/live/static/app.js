"use strict";

const $ = (id) => document.getElementById(id);
let config = null;
let active = null;
let watched = null;
let transcriptKey = "";
let taskKey = "";

const formatTime = (seconds) => {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
};
const printable = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2);

function setStatus(title, detail, state = "idle") {
  $("status").textContent = title;
  $("status-detail").textContent = detail;
  $("signal").dataset.state = state;
}

function showMessage(message = "") {
  $("message").textContent = message;
  $("message").hidden = !message;
}

function updateControls() {
  $("start").disabled = !config || !!active || !!watched;
  $("end").disabled = !active?.id || active.stopping;
  $("mute").disabled = !active?.stream || active.stopping;
}

async function request(path, body, timeout = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { error: text }; }
    if (!response.ok) throw new Error(printable(data.error || data.message || `Bridge error (${response.status})`));
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The local bridge did not respond in time.");
    throw error;
  } finally { clearTimeout(timer); }
}

function displayUsage(usage) {
  if (usage === undefined || usage === null) return;
  $("usage").textContent = printable(usage);
  $("usage-summary").textContent = "Final API usage";
}

function renderSnapshot(snapshot) {
  $("elapsed").textContent = formatTime(snapshot.seconds);
  const transcripts = (snapshot.transcripts || []).reduce((items, fragment) => {
    const previous = items.at(-1);
    if (previous?.role === fragment.role) previous.text += fragment.text;
    else items.push({ ...fragment });
    return items;
  }, []);
  const nextTranscriptKey = JSON.stringify(transcripts);
  if (nextTranscriptKey !== transcriptKey && transcripts.length) {
    transcriptKey = nextTranscriptKey;
    $("transcripts").replaceChildren(...transcripts.map((item) => {
      const entry = document.createElement("article");
      entry.className = "transcript";
      entry.dataset.role = item.role;
      const meta = document.createElement("div");
      meta.className = "entry-meta";
      const name = document.createElement("span");
      name.textContent = item.role === "user" ? "YOU" : "SPECIAL AGENT";
      const time = document.createElement("time");
      time.textContent = formatTime((item.start_ms || 0) / 1000);
      meta.append(name, time);
      const content = document.createElement("p");
      content.textContent = item.text;
      entry.append(meta, content);
      return entry;
    }));
    $("transcripts").scrollTop = $("transcripts").scrollHeight;
  }
  const tasks = snapshot.tasks || [];
  $("task-count").textContent = `${tasks.length} TASK${tasks.length === 1 ? "" : "S"}`;
  const nextTaskKey = JSON.stringify(tasks);
  if (nextTaskKey !== taskKey && tasks.length) {
    taskKey = nextTaskKey;
    $("tasks").replaceChildren(...tasks.map((item) => {
      const entry = document.createElement("article");
      entry.className = "task";
      const meta = document.createElement("div");
      meta.className = "entry-meta";
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.dataset.status = item.status;
      badge.textContent = item.status.replaceAll("_", " ");
      const duration = document.createElement("span");
      duration.textContent = item.duration_ms == null ? "" : `${(item.duration_ms / 1000).toFixed(1)}s`;
      meta.append(badge, duration);
      const content = document.createElement("p");
      content.textContent = printable(item.request || "Delegated request");
      entry.append(meta, content);
      if (item.result !== undefined && item.result !== null) {
        const result = document.createElement("p");
        result.className = "result";
        result.textContent = printable(item.result);
        entry.append(result);
      }
      return entry;
    }));
  }
  if (snapshot.finalized) displayUsage(snapshot.usage);
}

function closeBeacon(id) {
  if (id) navigator.sendBeacon(`/api/session/${encodeURIComponent(id)}/close`, new Blob(["{}"], { type: "application/json" }));
}

function cleanup(session) {
  if (active !== session) return;
  active = null; // Invalidate pending callbacks before closing the transports.
  clearTimeout(session.pollTimer);
  clearTimeout(session.closeTimer);
  clearTimeout(session.connectTimer);
  session.stream?.getTracks().forEach((track) => track.stop());
  session.channel?.close();
  session.peer?.close();
  $("audio").pause();
  $("audio").srcObject = null;
  $("mute").textContent = "Mute mic";
  $("mute").setAttribute("aria-pressed", "false");
  updateControls();
}

function finish(session, error = "") {
  if (active !== session) return;
  session.audioEnded = true;
  setStatus(error ? (session.id ? "Session interrupted" : "Couldn't start") : "Conversation ended", session.id ? "Checking the final session and any background work." : "Check setup and try again.", error ? "error" : "idle");
  if (error) showMessage(error);
  cleanup(session);
  if (session.id) {
    if (!session.polling) poll(session);
  } else { watched = null; updateControls(); }
}

async function poll(session) {
  if (watched !== session || session.polling) return;
  session.polling = true;
  try {
    const snapshot = await request(`/api/session/${encodeURIComponent(session.id)}`, undefined, 5000);
    if (watched !== session) return;
    session.pollFailures = 0;
    renderSnapshot(snapshot);
    const pending = (snapshot.tasks || []).some((job) => ["running", "waiting_for_context"].includes(job.status));
    if (snapshot.finalized || snapshot.error || ["closed", "error"].includes(snapshot.state)) {
      finish(session, snapshot.error ? printable(snapshot.error) : "");
      $("status-detail").textContent = pending ? "Audio stopped. Background requests are still running." : "Ready whenever you are.";
      if (!pending) { watched = null; updateControls(); return; }
    }
  } catch (error) {
    if (watched !== session) return;
    if (++session.pollFailures >= 3) {
      showMessage(`Lost contact with the bridge. ${error.message}`);
      if (active === session) endConversation();
      else { watched = null; updateControls(); return; }
    }
  } finally {
    session.polling = false;
    if (watched === session) session.pollTimer = setTimeout(() => poll(session), 1000);
  }
}

function waitForIce(peer) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => done(new Error("Audio connection timed out while gathering network candidates.")), 10000);
    function done(error) {
      clearTimeout(timer);
      peer.removeEventListener("icegatheringstatechange", check);
      error ? reject(error) : resolve();
    }
    function check() { if (peer.iceGatheringState === "complete") done(); }
    peer.addEventListener("icegatheringstatechange", check);
    check();
  });
}

function listenToPeer(session) {
  session.peer.addEventListener("track", ({ track, streams }) => {
    if (active !== session) return;
    $("audio").srcObject = streams[0] || new MediaStream([track]);
    $("audio").play().catch(() => {
      if (active === session) showMessage("Select play in Assistant audio to hear the conversation.");
    });
  });
  session.peer.addEventListener("connectionstatechange", () => {
    if (active === session && session.peer.connectionState === "failed") {
      showMessage("The audio connection failed. Closing this session.");
      endConversation();
    }
  });
  session.channel = session.peer.createDataChannel("oai-events");
  session.channel.addEventListener("message", ({ data }) => {
    if (active !== session) return;
    let event;
    try { event = JSON.parse(data); } catch { return; }
    if (event.type === "session.started" && !session.stopping) {
      clearTimeout(session.connectTimer);
      session.started = true;
      setStatus("You're live", "Talk naturally. You can interrupt at any time.", "live");
    } else if (event.type === "session.closed") {
      displayUsage(event.usage);
      finish(session);
    } else if (event.type === "error") {
      showMessage(printable(event.error?.message || event.error || "The voice service reported an error."));
      endConversation();
    }
  });
  session.channel.addEventListener("close", () => {
    if (active === session && !session.stopping) endConversation();
  });
}

async function startConversation() {
  if (active || watched || !config) return;
  const session = { stopping: false, pollFailures: 0 };
  active = watched = session;
  updateControls();
  showMessage();
  setStatus("Connecting", "Allow microphone access to begin.", "starting");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (active !== session) { stream.getTracks().forEach((track) => track.stop()); return; }
    session.stream = stream;
    session.peer = new RTCPeerConnection();
    stream.getAudioTracks().forEach((track) => session.peer.addTrack(track, stream));
    listenToPeer(session);
    await session.peer.setLocalDescription(await session.peer.createOffer());
    await waitForIce(session.peer);
    if (active !== session) return;
    setStatus("Connecting", "Opening the voice session and backend connection.", "starting");
    const result = await request("/api/session", { sdp: session.peer.localDescription.sdp }, 60000);
    if (active !== session) { closeBeacon(result.id); return; }
    session.id = result.id;
    if (!session.id || !result.transport?.sdp) throw new Error("The bridge returned an incomplete session response.");
    $("transcripts").replaceChildren();
    $("tasks").replaceChildren();
    transcriptKey = taskKey = "";
    $("usage").textContent = "No finalized usage yet.";
    $("usage-summary").textContent = "Final API usage appears after the session ends";
    $("elapsed").textContent = "00:00";
    updateControls();
    session.connectTimer = setTimeout(() => {
      if (active === session && !session.started) { showMessage("The audio session did not become ready in time."); endConversation(); }
    }, 20000);
    await session.peer.setRemoteDescription({ type: "answer", sdp: result.transport.sdp });
    if (active === session) poll(session);
  } catch (error) {
    if (active !== session) return;
    closeBeacon(session.id);
    const messages = { NotAllowedError: "Microphone access was denied. Allow the microphone for this localhost page and try again.", NotFoundError: "No microphone was found. Connect one and try again.", NotReadableError: "The microphone is unavailable. Check whether another app is using it." };
    finish(session, messages[error.name] || error.message);
  }
}

function endConversation() {
  const session = active;
  if (!session?.id || session.stopping) return;
  session.stopping = true;
  clearTimeout(session.connectTimer);
  updateControls();
  setStatus("Finishing up", "Waiting for the final session event.", "stopping");
  // Keep microphone, audio, events, and polling alive while the service drains.
  session.closeTimer = setTimeout(() => {
    closeBeacon(session.id);
    finish(session, "Close timed out. Local audio is stopped; final API usage is unconfirmed.");
  }, 15000);
  request(`/api/session/${encodeURIComponent(session.id)}/close`, {}).catch((error) => {
    if (active === session) {
      showMessage(`The bridge could not confirm close. ${error.message}`);
      if (session.channel?.readyState === "open") session.channel.send(JSON.stringify({ type: "session.close" }));
    }
  });
}

$("start").addEventListener("click", startConversation);
$("end").addEventListener("click", endConversation);
$("mute").addEventListener("click", () => {
  if (!active?.stream || active.stopping) return;
  const muted = $("mute").getAttribute("aria-pressed") !== "true";
  active.stream.getAudioTracks().forEach((track) => { track.enabled = !muted; });
  $("mute").setAttribute("aria-pressed", String(muted));
  $("mute").textContent = muted ? "Unmute mic" : "Mute mic";
});
window.addEventListener("pagehide", () => {
  if (watched) clearTimeout(watched.pollTimer);
  watched = null;
  if (!active) return;
  if (active.channel?.readyState === "open") active.channel.send(JSON.stringify({ type: "session.close" }));
  closeBeacon(active.id);
  cleanup(active);
});

(async () => {
  try {
    if (!window.isSecureContext || !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) throw new Error("Open this test on http://localhost:8099 so the browser can safely access your microphone.");
    if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) throw new Error("This browser does not support microphone capture and WebRTC. Open the test in a current desktop browser.");
    config = await request("/api/config");
    $("model").textContent = config.model;
    $("backend").textContent = config.backend === "home-assistant" ? "Home Assistant backend" : "Demo backend";
    $("demo-note").hidden = config.backend !== "demo";
    $("limits").textContent = `Idle timeout ${formatTime(config.idle_timeout)} · Session limit ${formatTime(config.max_duration)}`;
    setStatus("Ready to talk", "Start a conversation to connect your microphone.");
    updateControls();
  } catch (error) { setStatus("Setup needed", "Check the local bridge and browser.", "error"); showMessage(error.message); }
})();
