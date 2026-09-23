import { PipecatClient } from "@pipecat-ai/client-js";
import {
  WebSocketTransport,
  ProtobufFrameSerializer,
} from "@pipecat-ai/websocket-transport";

const logEl = document.getElementById("log");
const statusEl = document.getElementById("statusText");
const btnConnect = document.getElementById("btnConnect");
const btnDisconnect = document.getElementById("btnDisconnect");
const serverUrlInput = document.getElementById("serverUrl");

let pcClient = null;
// Keep a reference to the raw transport so we can call
// _mediaManager.userStartedSpeaking() for barge-in.
let transport = null;

function log(msg, cls) {
  const div = document.createElement("div");
  div.textContent = `${new Date().toISOString().slice(11, 23)} ${msg}`;
  if (cls) div.className = cls;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  console.log(msg);
}

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}

function setButtons(connected) {
  btnConnect.disabled = connected;
  btnDisconnect.disabled = !connected;
}

async function connect() {
  const serverUrl = serverUrlInput.value.trim();
  if (!serverUrl) {
    log("Please enter a server URL", "log-error");
    return;
  }

  setStatus("Connecting...", "connecting");
  setButtons(true);
  log("Connecting via " + serverUrl + "...", "log-system");

  try {
    transport = new WebSocketTransport({
      serializer: new ProtobufFrameSerializer(),
      recorderSampleRate: 16000,
      playerSampleRate: 24000,
    });

    pcClient = new PipecatClient({
      transport: transport,
      enableMic: true,
      enableCam: false,
      callbacks: {
        onConnected: () => {
          setStatus("Connected", "connected");
          log("Connected", "log-system");
        },
        onDisconnected: () => {
          setStatus("Disconnected", "disconnected");
          setButtons(false);
          log("Disconnected", "log-system");
        },
        onBotReady: (data) => {
          log("Bot ready", "log-system");
        },
        onBotStartedSpeaking: () => {
          log("Bot speaking...", "log-system");
        },
        onBotStoppedSpeaking: () => {
          log("Bot stopped speaking", "log-system");
        },
        onUserStartedSpeaking: () => {
          log("User speaking...", "log-system");
          // Interrupt bot audio playback for barge-in.
          // The transport doesn't wire this up automatically —
          // we call userStartedSpeaking() on its internal media manager.
          if (transport._mediaManager) {
            transport._mediaManager.userStartedSpeaking();
          }
        },
        onUserStoppedSpeaking: () => {
          log("User stopped speaking", "log-system");
        },
        onUserTranscript: (data) => {
          if (data.final) {
            log("User: " + data.text, "log-user");
          }
        },
        onBotTranscript: (data) => {
          log("Bot: " + data.text, "log-bot");
        },
        onMessageError: (error) => {
          log("Error: " + JSON.stringify(error), "log-error");
        },
        onError: (error) => {
          log("Error: " + JSON.stringify(error), "log-error");
        },
      },
    });

    window.pcClient = pcClient;

    await pcClient.initDevices();
    log("Devices initialized", "log-system");

    // Manually fetch the /start endpoint to get the WebSocket URL,
    // then connect directly. Using startBotAndConnect() causes a
    // "body stream already read" error because the SDK reads the
    // response body twice internally.
    const resp = await fetch(serverUrl, { method: "POST" });
    const data = await resp.json();
    if (!data.ws_url) {
      throw new Error("Server did not return ws_url");
    }
    log("WebSocket URL: " + data.ws_url, "log-system");

    await pcClient.connect({ wsUrl: data.ws_url });
    log("Connection complete", "log-system");
  } catch (error) {
    log("Connection error: " + error.message, "log-error");
    setStatus("Error", "disconnected");
    setButtons(false);
    if (pcClient) {
      try { await pcClient.disconnect(); } catch (e) {}
      pcClient = null;
    }
  }
}

async function disconnect() {
  if (pcClient) {
    try { await pcClient.disconnect(); } catch (e) {}
    pcClient = null;
  }
  transport = null;
  setStatus("Disconnected", "disconnected");
  setButtons(false);
}

btnConnect.addEventListener("click", connect);
btnDisconnect.addEventListener("click", disconnect);
