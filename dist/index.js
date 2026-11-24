// Dictation — hand-written Decky frontend (no build step).
const manifest = {"name":"Dictation"};
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) throw new Error("[Dictation] Decky loader API not initialized");
let api;
try { api = internalAPIConnection.connect(API_VERSION, manifest.name); }
catch { api = internalAPIConnection.connect(1, manifest.name); }
const call = api.call;
const addEventListener = api.addEventListener;
const removeEventListener = api.removeEventListener;
const toaster = api.toaster;

const React = window.SP_REACT;
const h = React.createElement;
const { useState, useEffect } = React;

// ---------------------------------------------------------------- state store
const store = { state: "idle", source: null, error: null, lastText: "" };
const listeners = new Set();
function setStore(patch) { Object.assign(store, patch); listeners.forEach((l) => l()); }
function useStore() {
  const [, force] = useState(0);
  useEffect(() => { const l = () => force((n) => n + 1); listeners.add(l); return () => listeners.delete(l); }, []);
  return store;
}

// ---------------------------------------------------------------- mic key label
function MicIcon({ size = "1.25em", color = "currentColor" }) {
  return h("svg", { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: color,
    strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", style: { verticalAlign: "middle" } },
    h("rect", { x: 9, y: 2, width: 6, height: 12, rx: 3, fill: color === "currentColor" ? "none" : color }),
    h("path", { d: "M5 10a7 7 0 0 0 14 0" }),
    h("line", { x1: 12, y1: 17, x2: 12, y2: 22 }));
}
function MicLabel() {
  const s = useStore();
  if (s.state === "recording")
    return h("span", { style: { display: "inline-flex", alignItems: "center", gap: "4px", color: "#ff5a52" } },
      h(MicIcon, { color: "#ff5a52" }), h("span", { style: { fontSize: "0.8em" } }, "Stop"));
  if (s.state === "transcribing")
    return h("span", { style: { fontSize: "1.2em", letterSpacing: "2px", opacity: 0.8 } }, "…");
  return h(MicIcon, {});
}
const DICT_KEY = { key: "VKDictate", label: h(MicLabel, {}), type: 4, strLocDescription: "Dictate" };

// ---------------------------------------------------------------- inserting text
let lastVKM = null; // Steam's VirtualKeyboardManager, captured when its keys are used
function insertText(text) {
  if (!text) return;
  try {
    if (lastVKM) { lastVKM.DispatchKeypress(text); return; }
  } catch (e) { console.error("[Dictation] DispatchKeypress failed", e); }
  SteamClient.Input.ControllerKeyboardSendText(text);
}

async function onDictateKey(vkm) {
  lastVKM = vkm;
  if (store.state === "transcribing") return;
  try {
    const r = await call("toggle", "osk");
    if (r && r.ok === false && r.error) toaster.toast({ title: "Dictation", body: r.error });
    if (r && r.text) insertText(r.text);
  } catch (e) {
    toaster.toast({ title: "Dictation failed", body: String(e) });
  }
}

// ---------------------------------------------------------------- Steam keyboard patches
const unpatchers = [];
function webpackRequire() {
  const chunk = window.webpackChunksteamui;
  return chunk.push([[Symbol("dictation")], {}, (r) => r]);
}
function findModules(pred) {
  const wr = webpackRequire();
  const out = [];
  for (const id in wr.m) {
    let src;
    try { src = wr.m[id].toString(); } catch { continue; }
    if (pred(src)) { try { out.push(wr(id)); } catch {} }
  }
  return out;
}

let layoutGetters = [];
function patchLayout(l) {
  if (!l || typeof l.rgLayout !== "function" || l.__dictationOrig) return;
  const orig = l.rgLayout;
  l.__dictationOrig = orig;
  l.rgLayout = function (...args) {
    const rows = orig.apply(this, args);
    try {
      const last = rows && rows[rows.length - 1];
      if (Array.isArray(last) && !last.includes(DICT_KEY)) last.splice(last.length - 1, 0, DICT_KEY);
    } catch (e) { console.error("[Dictation] layout patch", e); }
    return rows;
  };
  // Keep the physical-keycode table (used for highlighting) aligned with the new column.
  const kc = Array.isArray(l.rgKeycodes) ? l.rgKeycodes[l.rgKeycodes.length - 1] : null;
  if (Array.isArray(kc)) { kc.splice(kc.length - 1, 0, 0); l.__dictationKc = kc; }
  unpatchers.push(() => {
    l.rgLayout = orig; delete l.__dictationOrig;
    if (l.__dictationKc) { l.__dictationKc.splice(l.__dictationKc.length - 2, 1); delete l.__dictationKc; }
  });
}
function patchLayouts() {
  for (const g of layoutGetters) {
    try {
      const r = g();
      if (Array.isArray(r)) r.forEach(patchLayout); else patchLayout(r);
    } catch {}
  }
}

function patchKeyboard() {
  const layoutMod = findModules((s) => s.includes("#KeyboardLayout_QWERTY") && s.includes("rgKeycodes"))[0];
  const vkmMod = findModules((s) => s.includes('"VKPaste"') && s.includes("DispatchKeypress(") && s.includes("HandleVirtualKeyDown("))[0];
  const VKM = vkmMod && Object.values(vkmMod).find((v) => typeof v === "function" && v.prototype && v.prototype.HandleVirtualKeyDown);
  if (!layoutMod || !VKM) {
    console.error("[Dictation] couldn't find Steam's keyboard modules", !!layoutMod, !!VKM);
    return false;
  }
  layoutGetters = Object.values(layoutMod).filter((v) => typeof v === "function" && v.length === 0);
  patchLayouts();
  const timer = setInterval(patchLayouts, 2000); // layouts switched or added later
  unpatchers.push(() => clearInterval(timer));

  const proto = VKM.prototype;
  const origKey = proto.HandleVirtualKeyDown;
  proto.HandleVirtualKeyDown = function (key, ...rest) {
    lastVKM = this;
    if (key === DICT_KEY.key) { onDictateKey(this); return; }
    return origKey.call(this, key, ...rest);
  };
  unpatchers.push(() => { proto.HandleVirtualKeyDown = origKey; });
  return true;
}

// ---------------------------------------------------------------- backend events
function onState(s) {
  const prev = store.state;
  setStore({ state: s.state === "error" ? "idle" : s.state, source: s.source || store.source,
             error: s.error || null, lastText: s.text !== undefined ? s.text : store.lastText });
  if (s.state === "error" || (s.error && s.state === "idle")) {
    toaster.toast({ title: "Dictation", body: s.error || "Something went wrong" });
    return;
  }
  // Keyboard-key dictation shows its state on the key; toast for the hotkey.
  if (s.source === "hotkey") {
    if (s.state === "recording") toaster.toast({ title: "Dictation", body: "Listening… press the key again to stop", duration: 2500 });
    else if (s.state === "idle" && prev === "transcribing" && !(s.text || "").trim())
      toaster.toast({ title: "Dictation", body: "Didn't catch anything", duration: 2000 });
  }
}
function onText(text) { insertText(text); }

// ---------------------------------------------------------------- QAM panel
function Toggle({ label, desc, k, s }) {
  return h(DFL.PanelSectionRow, null, h(DFL.ToggleField, {
    label, description: desc, checked: !!(s.settings && s.settings[k]),
    onChange: async (v) => { await call("set_setting", k, v); s.reload(); } }));
}
function Panel() {
  const st = useStore();
  const [info, setInfo] = useState(null);
  const reload = async () => setInfo(await call("get_status"));
  useEffect(() => { reload(); }, [st.state]);
  const s = { ...(info || {}), reload };
  const busy = st.state === "transcribing";
  const label = st.state === "recording" ? "Stop and transcribe" : busy ? "Transcribing…" : "Test dictation";
  return h("div", null,
    h(DFL.PanelSection, { title: "Dictate" },
      h(DFL.PanelSectionRow, null, h(DFL.ButtonItem, { layout: "below", disabled: busy,
        onClick: async () => { const r = await call("toggle", "panel"); if (r && r.ok === false && r.error) toaster.toast({ title: "Dictation", body: r.error }); } },
        h("span", { style: { display: "inline-flex", alignItems: "center", gap: "8px" } }, h(MicIcon, {}), label))),
      st.lastText ? h(DFL.PanelSectionRow, null, h("div", { style: { fontSize: "13px", opacity: 0.85, padding: "4px 0" } }, "Heard: “" + st.lastText.trim() + "”")) : null,
      info && info.error ? h(DFL.PanelSectionRow, null, h("div", { style: { fontSize: "12px", color: "#ff8a80" } }, info.error)) : null,
      h(DFL.PanelSectionRow, null, h("div", { style: { fontSize: "12px", opacity: 0.7, lineHeight: 1.4 } },
        "Tap the mic key on the on-screen keyboard, or press the Copilot key, to start. Press again to stop and type what you said."))),
    h(DFL.PanelSection, { title: "Settings" },
      h(Toggle, { label: "Copilot key hotkey", desc: "Start/stop dictation from the keyboard, in Game and Desktop Mode", k: "hotkey_enabled", s }),
      h(Toggle, { label: "Start/stop sounds", k: "sounds", s }),
      h(Toggle, { label: "Add a space after", desc: "So you can keep dictating", k: "trailing_space", s })));
}

// ---------------------------------------------------------------- plugin
var index = DFL.definePlugin(() => {
  let patched = false;
  try { patched = patchKeyboard(); } catch (e) { console.error("[Dictation] patch failed", e); }
  if (!patched) toaster.toast({ title: "Dictation", body: "Couldn't add the mic key to the keyboard (Steam changed?). The hotkey still works." });
  addEventListener("dictation_state", onState);
  addEventListener("dictation_text", onText);
  call("get_status").then((s) => s && setStore({ state: s.state }));
  return {
    name: "Dictation",
    title: h("div", { className: DFL.staticClasses.Title }, "Dictation"),
    content: h(Panel, null),
    icon: h(MicIcon, { size: "1em" }),
    onDismount() {
      removeEventListener("dictation_state", onState);
      removeEventListener("dictation_text", onText);
      while (unpatchers.length) { try { unpatchers.pop()(); } catch {} }
    },
  };
});

export { index as default };
