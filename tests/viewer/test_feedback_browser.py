from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from zotero_arxiv_daily.viewer.feedback import (
    FeedbackBundle,
    FeedbackCommand,
    FeedbackStoreState,
    apply_feedback_commands,
    canonical_feedback_bundle_digest,
)


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "src" / "zotero_arxiv_daily" / "viewer" / "static" / "feedback.js"
PINNED_NODE = Path(r"C:\Users\Berton\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")
MAX_SAFE_SEQUENCE = 9_007_199_254_740_991


NODE_HARNESS = r"""
"use strict";

const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const api = require(process.argv[2]);
const scenario = process.argv[3];
const bundleJson = process.argv[4];

class FakeElement {
  constructor(tagName, ownerDocument = null) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.attributes = new Map();
    this.children = [];
    this.parentNode = null;
    this.listeners = new Map();
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.files = [];
    this.value = "";
    this.textContent = "";
    this.isContentEditable = false;
    this.clicked = false;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "hidden") this.hidden = true;
  }

  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === "hidden") this.hidden = false;
  }

  appendChild(child) {
    child.parentNode = this;
    child.ownerDocument = this.ownerDocument || this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    this.children = this.children.filter((candidate) => candidate !== child);
    child.parentNode = null;
    return child;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  async dispatchEvent(event) {
    event.target = event.target || this;
    event.currentTarget = this;
    event.preventDefault = event.preventDefault || (() => { event.defaultPrevented = true; });
    for (const listener of this.listeners.get(event.type) || []) {
      await listener(event);
    }
    return !event.defaultPrevented;
  }

  async click() {
    this.clicked = true;
    return this.dispatchEvent({ type: "click", target: this });
  }

  querySelectorAll(selector) {
    const found = [];
    const visit = (node) => {
      for (const child of node.children) {
        if (matchesSelector(child, selector)) found.push(child);
        visit(child);
      }
    };
    visit(this);
    return new FakeNodeList(found);
  }

  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class FakeNodeList {
  constructor(items) {
    this.length = items.length;
    items.forEach((item, index) => { this[index] = item; });
  }
  item(index) { return this[index] || null; }
  forEach(callback) {
    for (let index = 0; index < this.length; index += 1) callback(this[index], index, this);
  }
  *[Symbol.iterator]() {
    for (let index = 0; index < this.length; index += 1) yield this[index];
  }
}

function matchesSelector(element, selector) {
  const tag = selector.match(/^[a-z]+/i);
  if (tag && element.tagName !== tag[0].toUpperCase()) return false;
  const className = selector.match(/\.([A-Za-z0-9_-]+)/);
  if (className) {
    const classes = (element.getAttribute("class") || "").split(/\s+/);
    if (!classes.includes(className[1])) return false;
  }
  for (const match of selector.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)) {
    if (!element.hasAttribute(match[1])) return false;
    if (match[2] !== undefined && element.getAttribute(match[1]) !== match[2]) return false;
  }
  return true;
}

class FakeDocument extends FakeElement {
  constructor() {
    super("document");
    this.ownerDocument = this;
    this.readyState = "complete";
    this.body = new FakeElement("body", this);
    this.appendChild(this.body);
    this.downloads = [];
  }

  createElement(tagName) {
    const element = new FakeElement(tagName, this);
    if (tagName.toLowerCase() === "a") {
      const originalClick = element.click.bind(element);
      element.click = async () => {
        this.downloads.push({ download: element.download, href: element.href });
        return originalClick();
      };
    }
    return element;
  }
}

class MemoryStorage {
  constructor(initial = {}, options = {}) {
    this.values = new Map(Object.entries(initial));
    this.options = options;
    this.setCalls = 0;
  }
  getItem(key) {
    if (this.options.throwGet) throw new Error("private get failure");
    return this.values.has(key) ? this.values.get(key) : null;
  }
  setItem(key, value) {
    this.setCalls += 1;
    if (this.options.throwSet) throw new Error("private quota details");
    this.values.set(key, String(value));
  }
  removeItem(key) {
    if (this.options.throwRemove) throw new Error("private remove failure");
    this.values.delete(key);
  }
}

function element(tag, attributes = {}) {
  const value = new FakeElement(tag);
  for (const [name, content] of Object.entries(attributes)) value.setAttribute(name, content);
  return value;
}

function createDom(paperIds = ["2401.00001"]) {
  const document = new FakeDocument();
  const status = element("p", { "data-testid": "feedback-status", "aria-live": "polite" });
  document.body.appendChild(status);
  const exportButton = element("button", { "data-ai-action": "export-feedback" });
  const importInput = element("input", { "data-ai-action": "import-feedback" });
  const clearButton = element("button", { "data-ai-action": "clear-feedback" });
  document.body.appendChild(exportButton);
  document.body.appendChild(importInput);
  document.body.appendChild(clearButton);

  const filters = {};
  for (const name of ["unread", "favorite", "show-irrelevant"]) {
    filters[name] = element("input", { "data-feedback-filter": name });
    document.body.appendChild(filters[name]);
  }

  const cards = paperIds.map((paperId) => {
    const card = element("article", { class: "paper-card", "data-paper-id": paperId, tabindex: "0" });
    const buttons = {};
    for (const action of ["read", "favorite", "irrelevant"]) {
      const button = element("button", { "data-feedback-action": action, "aria-pressed": "false" });
      const label = element("span", { "data-feedback-label": action });
      button.appendChild(label);
      card.appendChild(button);
      buttons[action] = button;
    }
    document.body.appendChild(card);
    return { card, buttons };
  });
  return { document, status, exportButton, importInput, clearButton, filters, cards };
}

function deterministicDeps(initialSeconds = 0) {
  let uuidSequence = 1;
  let seconds = initialSeconds;
  return {
    crypto: {
      subtle: webcrypto.subtle,
      randomUUID: () => `00000000-0000-4000-8000-${String(uuidSequence++).padStart(12, "0")}`,
    },
    now: () => new Date(Date.UTC(2026, 6, 22, 4, 0, seconds++)),
  };
}

function record(state, paperId) { return api.getPaperRecord(state, paperId); }

async function transitionsAndRefresh() {
  const storage = new MemoryStorage();
  const first = createDom();
  const adapter = api.createBrowserAdapter({ document: first.document, storage, ...deterministicDeps() });
  adapter.init();

  await first.cards[0].buttons.read.click();
  await first.cards[0].buttons.favorite.click();
  assert.deepEqual(record(adapter.getState(), "2401.00001"), { read: true, favorite: true, irrelevant: false });
  await first.cards[0].buttons.irrelevant.click();
  assert.deepEqual(record(adapter.getState(), "2401.00001"), { read: true, favorite: false, irrelevant: true });
  assert.equal(first.cards[0].card.hidden, true);
  assert.equal(storage.values.size, 1);
  assert.equal([...storage.values.keys()][0], api.STORAGE_KEY);

  const refreshed = createDom();
  const refreshedAdapter = api.createBrowserAdapter({ document: refreshed.document, storage, ...deterministicDeps(10) });
  refreshedAdapter.init();
  assert.equal(refreshed.cards[0].buttons.read.getAttribute("aria-pressed"), "true");
  assert.equal(refreshed.cards[0].buttons.irrelevant.getAttribute("aria-pressed"), "true");
  assert.equal(refreshed.cards[0].card.hidden, true);
  await refreshed.cards[0].buttons.favorite.click();
  assert.deepEqual(record(refreshedAdapter.getState(), "2401.00001"), { read: true, favorite: true, irrelevant: false });
  assert.equal(refreshed.cards[0].card.hidden, false);
}

async function filtersUseInputAndChange() {
  const dom = createDom(["2401.00001", "2401.00002"]);
  const adapter = api.createBrowserAdapter({ document: dom.document, storage: new MemoryStorage(), ...deterministicDeps() });
  adapter.init();
  await dom.cards[0].buttons.read.click();
  await dom.cards[0].buttons.favorite.click();
  await dom.cards[1].buttons.irrelevant.click();
  assert.equal(dom.cards[1].card.hidden, true);

  dom.filters["show-irrelevant"].checked = true;
  await dom.filters["show-irrelevant"].dispatchEvent({ type: "change" });
  assert.equal(dom.cards[1].card.hidden, false);
  dom.filters.favorite.checked = true;
  await dom.filters.favorite.dispatchEvent({ type: "input" });
  assert.equal(dom.cards[0].card.hidden, false);
  assert.equal(dom.cards[1].card.hidden, true);
  dom.filters.unread.checked = true;
  await dom.filters.unread.dispatchEvent({ type: "change" });
  assert.equal(dom.cards[0].card.hidden, true);
  await dom.cards[0].buttons.read.click();
  assert.equal(dom.cards[0].card.hidden, false);
}

async function keyboardScopeAndInputExclusion() {
  const dom = createDom();
  const adapter = api.createBrowserAdapter({ document: dom.document, storage: new MemoryStorage(), ...deterministicDeps() });
  adapter.init();
  await dom.cards[0].card.dispatchEvent({ type: "keydown", key: "r", target: dom.cards[0].card });
  assert.equal(record(adapter.getState(), "2401.00001").read, true);
  await dom.cards[0].card.dispatchEvent({ type: "keydown", key: "f", target: dom.cards[0].card });
  assert.equal(record(adapter.getState(), "2401.00001").favorite, true);
  await dom.cards[0].card.dispatchEvent({ type: "keydown", key: "i", target: dom.cards[0].card });
  assert.deepEqual(record(adapter.getState(), "2401.00001"), { read: true, favorite: false, irrelevant: true });

  const input = element("input");
  await dom.cards[0].card.dispatchEvent({ type: "keydown", key: "r", target: input });
  assert.equal(record(adapter.getState(), "2401.00001").read, true);
  await dom.cards[0].card.dispatchEvent({ type: "keydown", key: "x", target: dom.cards[0].card });
  assert.equal(record(adapter.getState(), "2401.00001").irrelevant, true);
}

async function storageFailuresAreSafe() {
  const corrupted = new MemoryStorage({ [api.STORAGE_KEY]: "{not-json" });
  const corruptDom = createDom();
  const corruptAdapter = api.createBrowserAdapter({ document: corruptDom.document, storage: corrupted, ...deterministicDeps() });
  corruptAdapter.init();
  assert.equal(corruptDom.status.textContent, "无法读取浏览器反馈。可清除本站反馈后重试。");
  assert.deepEqual(record(corruptAdapter.getState(), "2401.00001"), { read: false, favorite: false, irrelevant: false });

  const quota = new MemoryStorage({}, { throwSet: true });
  const quotaDom = createDom();
  const quotaAdapter = api.createBrowserAdapter({ document: quotaDom.document, storage: quota, ...deterministicDeps() });
  quotaAdapter.init();
  await quotaDom.cards[0].buttons.read.click();
  assert.equal(quotaDom.status.textContent, "无法保存浏览器反馈。请导出备份后重试。");
  assert.equal(quotaDom.cards[0].buttons.read.getAttribute("aria-pressed"), "true");
  assert.equal(quota.values.size, 0);
}

async function deterministicBundle() {
  const deps = deterministicDeps();
  let state = api.createEmptyState("browser-test");
  state = api.appendFeedback(state, "hep-th/9901001v2", "favorite", true, {
    commandId: "00000000-0000-4000-8000-000000000012",
    occurredAt: "2026-07-22T04:00:02.000Z",
  });
  state = api.appendFeedback(state, "2401.01234v3", "read", true, {
    commandId: "00000000-0000-4000-8000-000000000011",
    occurredAt: "2026-07-22T04:00:01.000Z",
  });
  const options = {
    bundleId: "00000000-0000-4000-8000-000000000099",
    generatedAt: "2026-07-22T05:00:00.000Z",
    subtle: deps.crypto.subtle,
  };
  const first = await api.createBackupBundle(state, options);
  const second = await api.createBackupBundle(state, options);
  assert.deepEqual(first, second);
  assert.equal(first.commands[0].paper_id, "2401.01234");
  assert.match(first.digest, /^[0-9a-f]{64}$/);
  assert.throws(() => api.appendFeedback(state, "2401.01235", "read", true, {
    commandId: "00000000-0000-4000-8000-000000000013",
    occurredAt: "2026-07-22T04:00:03",
  }));
  await assert.rejects(() => api.createBackupBundle(state, {
    ...options,
    generatedAt: "2026-07-22T05:00:00",
  }));
  process.stdout.write(JSON.stringify(first));
}

async function transactionalImport() {
  const deps = deterministicDeps();
  let source = api.createEmptyState("browser-source");
  source = api.appendFeedback(source, "2401.00001", "favorite", true, {
    commandId: "00000000-0000-4000-8000-000000000021",
    occurredAt: "2026-07-22T04:00:02.000Z",
  });
  const bundle = await api.createBackupBundle(source, {
    bundleId: "00000000-0000-4000-8000-000000000022",
    generatedAt: "2026-07-22T05:00:00.000Z",
    subtle: deps.crypto.subtle,
  });
  let existing = api.createEmptyState("browser-existing");
  existing = api.appendFeedback(existing, "2401.00001", "read", true, {
    commandId: "00000000-0000-4000-8000-000000000023",
    occurredAt: "2026-07-22T04:00:01.000Z",
  });
  const storage = new MemoryStorage({ [api.STORAGE_KEY]: api.serializeState(existing) });
  const merged = await api.importBackup(JSON.stringify(bundle), existing, storage, deps.crypto.subtle);
  assert.deepEqual(record(merged, "2401.00001"), { read: true, favorite: true, irrelevant: false });
  assert.equal(storage.setCalls, 1);

  const before = storage.getItem(api.STORAGE_KEY);
  const tampered = structuredClone(bundle);
  tampered.commands[0].value = false;
  await assert.rejects(() => api.importBackup(JSON.stringify(tampered), merged, storage, deps.crypto.subtle));
  assert.equal(storage.getItem(api.STORAGE_KEY), before);
  const unknown = structuredClone(bundle);
  unknown.private_note = "must reject";
  await assert.rejects(() => api.importBackup(JSON.stringify(unknown), merged, storage, deps.crypto.subtle));
  assert.equal(storage.getItem(api.STORAGE_KEY), before);

  const quota = new MemoryStorage({ [api.STORAGE_KEY]: before }, { throwSet: true });
  await assert.rejects(() => api.importBackup(JSON.stringify(bundle), existing, quota, deps.crypto.subtle));
  assert.equal(quota.getItem(api.STORAGE_KEY), before);

  let newerSameDevice = api.createEmptyState("browser-existing");
  newerSameDevice = api.appendFeedback(newerSameDevice, "2401.00002", "read", true, {
    commandId: "00000000-0000-4000-8000-000000000024",
    occurredAt: "2026-07-22T04:00:04.000Z",
  });
  newerSameDevice = api.appendFeedback(newerSameDevice, "2401.00003", "read", true, {
    commandId: "00000000-0000-4000-8000-000000000025",
    occurredAt: "2026-07-22T04:00:05.000Z",
  });
  const sameDeviceBundle = await api.createBackupBundle(newerSameDevice, {
    bundleId: "00000000-0000-4000-8000-000000000026",
    generatedAt: "2026-07-22T05:00:01.000Z",
    subtle: deps.crypto.subtle,
  });
  const monotonicStorage = new MemoryStorage();
  const monotonic = await api.importBackup(JSON.stringify(sameDeviceBundle), existing, monotonicStorage, deps.crypto.subtle);
  assert.equal(monotonic.sequence, 2);
  const next = api.appendFeedback(monotonic, "2401.00004", "read", true, {
    commandId: "00000000-0000-4000-8000-000000000027",
    occurredAt: "2026-07-22T04:00:06.000Z",
  });
  assert.equal(next.sequence, 3);
}

async function blobExportAndFileImport() {
  const dom = createDom();
  const storage = new MemoryStorage();
  const urls = [];
  const URLAdapter = {
    createObjectURL: (blob) => { urls.push(blob); return "blob:feedback"; },
    revokeObjectURL: (url) => { assert.equal(url, "blob:feedback"); },
  };
  const deps = deterministicDeps();
  const adapter = api.createBrowserAdapter({
    document: dom.document,
    storage,
    URL: URLAdapter,
    Blob,
    ...deps,
  });
  adapter.init();
  await dom.cards[0].buttons.read.click();
  await dom.exportButton.click();
  assert.equal(urls.length, 1);
  assert.equal(dom.document.downloads[0].download, "feedback-v1.json");
  assert.equal(dom.exportButton.disabled, false);
  assert.equal(dom.exportButton.getAttribute("aria-busy"), "false");

  let source = api.createEmptyState("browser-import");
  source = api.appendFeedback(source, "2401.00001", "favorite", true, {
    commandId: "00000000-0000-4000-8000-000000000031",
    occurredAt: "2026-07-22T04:00:03.000Z",
  });
  const bundle = await api.createBackupBundle(source, {
    bundleId: "00000000-0000-4000-8000-000000000032",
    generatedAt: "2026-07-22T05:00:00.000Z",
    subtle: deps.crypto.subtle,
  });
  dom.importInput.files = [{ text: async () => JSON.stringify(bundle) }];
  await dom.importInput.dispatchEvent({ type: "change", target: dom.importInput });
  assert.deepEqual(record(adapter.getState(), "2401.00001"), { read: true, favorite: true, irrelevant: false });
  assert.equal(dom.importInput.disabled, false);
  assert.equal(dom.importInput.getAttribute("aria-busy"), "false");
}

async function importPythonPrecisionBundleAndReexport() {
  assert.equal(typeof bundleJson, "string");
  const expected = JSON.parse(bundleJson);
  const dom = createDom([...new Set(expected.commands.map((command) => command.paper_id))]);
  const storage = new MemoryStorage();
  const urls = [];
  const URLAdapter = {
    createObjectURL: (blob) => { urls.push(blob); return "blob:feedback"; },
    revokeObjectURL: (url) => { assert.equal(url, "blob:feedback"); },
  };
  const adapter = api.createBrowserAdapter({
    document: dom.document,
    storage,
    URL: URLAdapter,
    Blob,
    ...deterministicDeps(),
  });
  adapter.init();
  dom.importInput.files = [{ text: async () => bundleJson }];
  await dom.importInput.dispatchEvent({ type: "change", target: dom.importInput });
  assert.equal(dom.status.textContent, "反馈备份已导入当前浏览器。");
  assert.deepEqual(adapter.getState().commands, expected.commands);

  await dom.exportButton.click();
  assert.equal(urls.length, 1);
  const exported = JSON.parse(await urls[0].text());
  assert.deepEqual(exported.commands, expected.commands);
  process.stdout.write(JSON.stringify(exported));
}

async function browserAdapterSameSecondBundle() {
  const dom = createDom();
  const storage = new MemoryStorage();
  const urls = [];
  const URLAdapter = {
    createObjectURL: (blob) => { urls.push(blob); return "blob:feedback"; },
    revokeObjectURL: (url) => { assert.equal(url, "blob:feedback"); },
  };
  const moments = [
    new Date("2026-07-22T18:30:03.100Z"),
    new Date("2026-07-22T18:30:03.000Z"),
    new Date("2026-07-22T19:00:00.000Z"),
  ];
  const adapter = api.createBrowserAdapter({
    document: dom.document,
    storage,
    URL: URLAdapter,
    Blob,
    crypto: deterministicDeps().crypto,
    now: () => moments.shift(),
  });
  adapter.init();
  await dom.cards[0].buttons.read.click();
  await dom.cards[0].buttons.read.click();
  await dom.exportButton.click();
  process.stdout.write(JSON.stringify(JSON.parse(await urls[0].text())));
}

async function sequenceBoundsAndPythonMaximumBundle() {
  const unsafe = Number.MAX_SAFE_INTEGER + 1;
  assert.throws(() => api.parseStoredState(JSON.stringify({
    schema_version: "1.0", device_id: "browser-test", sequence: unsafe, commands: [],
  })));
  assert.throws(() => api.parseStoredState(JSON.stringify({
    schema_version: "1.0", device_id: "browser-test", sequence: 0,
    commands: [{
      schema_version: "1.0", command_id: "00000000-0000-4000-8000-000000000401",
      paper_id: "2401.00001", action: "set_read", value: true,
      occurred_at: "2026-07-22T18:30:00Z", device_id: "browser-test", sequence: unsafe,
    }],
  })));
  await importPythonPrecisionBundleAndReexport();
}

const scenarios = {
  transitions: transitionsAndRefresh,
  filters: filtersUseInputAndChange,
  keyboard: keyboardScopeAndInputExclusion,
  storage: storageFailuresAreSafe,
  bundle: deterministicBundle,
  import: transactionalImport,
  files: blobExportAndFileImport,
  "cross-language": importPythonPrecisionBundleAndReexport,
  "adapter-same-second": browserAdapterSameSecondBundle,
  "sequence-bounds": sequenceBoundsAndPythonMaximumBundle,
};

scenarios[scenario]().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""


@pytest.fixture(scope="session")
def node_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("feedback-browser") / "harness.cjs"
    path.write_text(NODE_HARNESS, encoding="utf-8")
    return path


def _node_executable() -> str:
    executable = shutil.which("node")
    if executable is not None:
        return executable
    assert PINNED_NODE.is_file(), f"Node 20 runtime is required at {PINNED_NODE}"
    return str(PINNED_NODE)


def _run_node(
    node_harness: Path, scenario: str, bundle_json: str | None = None
) -> subprocess.CompletedProcess[str]:
    command = [_node_executable(), str(node_harness), str(SCRIPT), scenario]
    if bundle_json is not None:
        command.append(bundle_json)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    return completed


@pytest.mark.parametrize("scenario", ["transitions", "filters", "keyboard", "storage"])
def test_browser_adapter_state_filters_keyboard_and_storage_failures(
    node_harness: Path, scenario: str
) -> None:
    _run_node(node_harness, scenario)


def test_browser_export_uses_web_crypto_and_matches_python_bundle_contract(
    node_harness: Path,
) -> None:
    completed = _run_node(node_harness, "bundle")
    bundle = FeedbackBundle.model_validate(json.loads(completed.stdout))

    assert bundle.commands[0].paper_id == "2401.01234"
    assert bundle.commands[1].paper_id == "hep-th/9901001"


def test_browser_adapter_imports_and_reexports_python_microsecond_backup(
    node_harness: Path,
) -> None:
    commands = (
        FeedbackCommand(
            command_id=UUID("00000000-0000-4000-8000-000000000101"),
            paper_id="arxiv:2401.00001v2",
            action="set_read",
            value=True,
            occurred_at="2026-07-22T23:59:59.123456+05:30",
            device_id="python-device",
            sequence=1,
        ),
        FeedbackCommand(
            command_id=UUID("00000000-0000-4000-8000-000000000102"),
            paper_id="hep-th/9901001v2",
            action="set_favorite",
            value=True,
            occurred_at="2026-07-22T18:30:00.000001Z",
            device_id="python-device",
            sequence=2,
        ),
    )
    bundle_id = UUID("00000000-0000-4000-8000-000000000199")
    generated_at = datetime.fromisoformat("2026-07-22T23:59:59.654321-04:00")
    bundle = FeedbackBundle(
        bundle_id=bundle_id,
        generated_at=generated_at,
        commands=commands,
        digest=canonical_feedback_bundle_digest(
            commands, bundle_id=bundle_id, generated_at=generated_at
        ),
    )

    completed = _run_node(node_harness, "cross-language", bundle.model_dump_json())
    reexported = FeedbackBundle.model_validate_json(completed.stdout)

    assert tuple(command.command_id for command in reexported.commands) == tuple(
        command.command_id for command in commands
    )
    assert tuple(command.occurred_at for command in reexported.commands) == tuple(
        command.occurred_at for command in commands
    )


def test_browser_adapter_imports_python_same_second_commands_in_datetime_order(
    node_harness: Path,
) -> None:
    commands = (
        FeedbackCommand(
            command_id=UUID("00000000-0000-4000-8000-000000000202"),
            paper_id="2401.00001",
            action="set_read",
            value=False,
            occurred_at="2026-07-22T18:30:03Z",
            device_id="python-device",
            sequence=2,
        ),
        FeedbackCommand(
            command_id=UUID("00000000-0000-4000-8000-000000000201"),
            paper_id="2401.00001",
            action="set_read",
            value=True,
            occurred_at="2026-07-22T18:30:03.100000Z",
            device_id="python-device",
            sequence=1,
        ),
    )
    bundle_id = UUID("00000000-0000-4000-8000-000000000299")
    generated_at = datetime.fromisoformat("2026-07-22T19:00:00Z")
    bundle = FeedbackBundle(
        bundle_id=bundle_id,
        generated_at=generated_at,
        commands=commands,
        digest=canonical_feedback_bundle_digest(
            commands, bundle_id=bundle_id, generated_at=generated_at
        ),
    )

    completed = _run_node(node_harness, "cross-language", bundle.model_dump_json())
    reexported = FeedbackBundle.model_validate_json(completed.stdout)
    python_projection = apply_feedback_commands(FeedbackStoreState(), reexported.commands)

    assert tuple(command.sequence for command in reexported.commands) == (2, 1)
    assert python_projection.state.records[0].read is True


def test_browser_adapter_exports_same_second_commands_in_python_datetime_order(
    node_harness: Path,
) -> None:
    completed = _run_node(node_harness, "adapter-same-second")
    bundle = FeedbackBundle.model_validate_json(completed.stdout)
    python_projection = apply_feedback_commands(FeedbackStoreState(), bundle.commands)

    assert tuple(command.sequence for command in bundle.commands) == (2, 1)
    assert python_projection.state.records[0].read is True


@pytest.mark.parametrize(
    ("source_timestamp", "expected_timestamp"),
    [
        ("2026-07-22T18:30:00Z", "2026-07-22T18:30:00Z"),
        ("2026-07-22T18:30:00.0Z", "2026-07-22T18:30:00Z"),
        ("2026-07-22T18:30:00.1Z", "2026-07-22T18:30:00.100000Z"),
        ("2026-07-22T18:30:00.12Z", "2026-07-22T18:30:00.120000Z"),
        ("2026-07-22T18:30:00.123Z", "2026-07-22T18:30:00.123000Z"),
        ("2026-07-22T18:30:00.1234Z", "2026-07-22T18:30:00.123400Z"),
        ("2026-07-22T18:30:00.12345Z", "2026-07-22T18:30:00.123450Z"),
        ("2026-07-22T18:30:00.123456Z", "2026-07-22T18:30:00.123456Z"),
        ("2026-07-23T00:00:00.1+05:30", "2026-07-22T18:30:00.100000Z"),
        ("2026-07-22T23:59:59.12345-04:00", "2026-07-23T03:59:59.123450Z"),
    ],
)
def test_browser_adapter_preserves_python_origin_timestamp_serialization(
    node_harness: Path,
    source_timestamp: str,
    expected_timestamp: str,
) -> None:
    command = FeedbackCommand(
        command_id=UUID("00000000-0000-4000-8000-000000000301"),
        paper_id="2401.00001",
        action="set_read",
        value=True,
        occurred_at=source_timestamp,
        device_id="python-device",
        sequence=1,
    )
    bundle_id = UUID("00000000-0000-4000-8000-000000000399")
    generated_at = datetime.fromisoformat("2026-07-22T19:00:00Z")
    bundle = FeedbackBundle(
        bundle_id=bundle_id,
        generated_at=generated_at,
        commands=(command,),
        digest=canonical_feedback_bundle_digest(
            (command,), bundle_id=bundle_id, generated_at=generated_at
        ),
    )

    completed = _run_node(node_harness, "cross-language", bundle.model_dump_json())
    reexported = FeedbackBundle.model_validate_json(completed.stdout)

    assert command.model_dump(mode="json")["occurred_at"] == expected_timestamp
    assert reexported.commands[0].occurred_at == command.occurred_at


def test_browser_adapter_accepts_python_maximum_sequence_and_rejects_unsafe_json_numbers(
    node_harness: Path,
) -> None:
    command = FeedbackCommand(
        command_id=UUID("00000000-0000-4000-8000-000000000402"),
        paper_id="2401.00001",
        action="set_read",
        value=True,
        occurred_at="2026-07-22T18:30:00Z",
        device_id="python-device",
        sequence=MAX_SAFE_SEQUENCE,
    )
    bundle_id = UUID("00000000-0000-4000-8000-000000000499")
    generated_at = datetime.fromisoformat("2026-07-22T19:00:00Z")
    bundle = FeedbackBundle(
        bundle_id=bundle_id,
        generated_at=generated_at,
        commands=(command,),
        digest=canonical_feedback_bundle_digest(
            (command,), bundle_id=bundle_id, generated_at=generated_at
        ),
    )

    completed = _run_node(node_harness, "sequence-bounds", bundle.model_dump_json())
    reexported = FeedbackBundle.model_validate_json(completed.stdout)

    assert reexported.commands[0].sequence == MAX_SAFE_SEQUENCE


def test_browser_and_python_order_equal_timestamps_by_device_then_sequence_then_uuid(
    node_harness: Path,
) -> None:
    occurred_at = "2026-07-22T18:30:00Z"
    commands = (
        FeedbackCommand(
            command_id=UUID("00000000-0000-4000-8000-000000000403"),
            paper_id="2401.00001",
            action="set_read",
            value=True,
            occurred_at=occurred_at,
            device_id="device-a",
            sequence=9,
        ),
        FeedbackCommand(
            command_id=UUID("00000000-0000-4000-8000-000000000404"),
            paper_id="2401.00001",
            action="set_read",
            value=False,
            occurred_at=occurred_at,
            device_id="device-b",
            sequence=1,
        ),
        FeedbackCommand(
            command_id=UUID("00000000-0000-4000-8000-000000000405"),
            paper_id="2401.00001",
            action="set_read",
            value=True,
            occurred_at=occurred_at,
            device_id="device-c",
            sequence=2,
        ),
        FeedbackCommand(
            command_id=UUID("00000000-0000-4000-8000-000000000406"),
            paper_id="2401.00001",
            action="set_read",
            value=False,
            occurred_at=occurred_at,
            device_id="device-c",
            sequence=2,
        ),
    )
    bundle_id = UUID("00000000-0000-4000-8000-000000000498")
    generated_at = datetime.fromisoformat("2026-07-22T19:00:00Z")
    bundle = FeedbackBundle(
        bundle_id=bundle_id,
        generated_at=generated_at,
        commands=commands,
        digest=canonical_feedback_bundle_digest(
            commands, bundle_id=bundle_id, generated_at=generated_at
        ),
    )

    completed = _run_node(node_harness, "cross-language", bundle.model_dump_json())
    reexported = FeedbackBundle.model_validate_json(completed.stdout)

    assert tuple(command.command_id for command in reexported.commands) == tuple(
        command.command_id for command in commands
    )


@pytest.mark.parametrize("scenario", ["import", "files"])
def test_browser_backup_import_is_transactional_and_uses_file_blob_adapters(
    node_harness: Path, scenario: str
) -> None:
    _run_node(node_harness, scenario)
