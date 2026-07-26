(function exposeFeedback(globalScope) {
  "use strict";

  const SCHEMA_VERSION = "1.0";
  const STORAGE_KEY = "personal-paper-daily:reader-feedback:v1";
  const MAX_COMMANDS = 500;
  const MAX_BACKUP_BYTES = 1000000;
  const MAX_FEEDBACK_SEQUENCE = Number.MAX_SAFE_INTEGER;
  const ACTIONS = new Set(["read", "favorite", "irrelevant"]);
  const COMMAND_ACTIONS = new Set(["set_read", "set_favorite", "set_irrelevant"]);
  const PAPER_ID_PATTERN = /^(?:[a-z-]+(?:\.[A-Z]{2})?\/\d{7}|\d{4}\.\d{4,5})(?:v[1-9]\d*)?$/i;
  const DEVICE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  const UUID4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const DIGEST_PATTERN = /^[0-9a-f]{64}$/;
  const AWARE_TIMESTAMP_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/;

  function fail() {
    throw new Error("feedback data rejected");
  }

  function assertExactKeys(value, expected) {
    if (!value || typeof value !== "object" || Array.isArray(value)) fail();
    const actual = Object.keys(value).sort();
    const wanted = [...expected].sort();
    if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) fail();
  }

  function normalizePaperId(value) {
    if (typeof value !== "string" || [...value].some((character) => character.charCodeAt(0) < 32)) fail();
    let normalized = value.trim();
    if (normalized.toLowerCase().startsWith("arxiv:")) normalized = normalized.slice(6);
    if (!PAPER_ID_PATTERN.test(normalized)) fail();
    return normalized.replace(/v[1-9]\d*$/i, "").toLowerCase();
  }

  function canonicalUtc(value) {
    if (value instanceof Date) {
      if (Number.isNaN(value.getTime())) fail();
      return canonicalUtc(value.toISOString());
    }
    if (typeof value !== "string") fail();
    const match = AWARE_TIMESTAMP_PATTERN.exec(value);
    if (!match) fail();
    const [, yearText, monthText, dayText, hourText, minuteText, secondText, fraction = "", zone] = match;
    const year = Number(yearText);
    const month = Number(monthText);
    const day = Number(dayText);
    const hour = Number(hourText);
    const minute = Number(minuteText);
    const second = Number(secondText);
    if (year < 1 || month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59) fail();

    const date = new Date(0);
    date.setUTCHours(hour, minute, second, 0);
    date.setUTCFullYear(year, month - 1, day);
    if (
      date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day
    ) fail();

    let offsetMinutes = 0;
    if (zone !== "Z") {
      const offsetHours = Number(zone.slice(1, 3));
      const offsetRemainder = Number(zone.slice(4, 6));
      if (offsetHours > 23 || offsetRemainder > 59) fail();
      offsetMinutes = offsetHours * 60 + offsetRemainder;
      if (zone[0] === "-") offsetMinutes = -offsetMinutes;
    }
    date.setUTCMinutes(date.getUTCMinutes() - offsetMinutes);
    if (date.getUTCFullYear() < 1 || date.getUTCFullYear() > 9999) fail();

    const utc = [
      String(date.getUTCFullYear()).padStart(4, "0"),
      String(date.getUTCMonth() + 1).padStart(2, "0"),
      String(date.getUTCDate()).padStart(2, "0"),
    ];
    const time = [
      String(date.getUTCHours()).padStart(2, "0"),
      String(date.getUTCMinutes()).padStart(2, "0"),
      String(date.getUTCSeconds()).padStart(2, "0"),
    ];
    const canonicalFraction = Number(fraction) === 0 ? "" : `.${fraction.padEnd(6, "0")}`;
    return `${utc.join("-")}T${time.join(":")}${canonicalFraction}Z`;
  }

  function validateUuid(value) {
    if (typeof value !== "string" || !UUID4_PATTERN.test(value)) fail();
    return value.toLowerCase();
  }

  function validateDeviceId(value) {
    if (typeof value !== "string" || !DEVICE_ID_PATTERN.test(value)) fail();
    return value;
  }

  function canonicalStringify(value) {
    if (Array.isArray(value)) return `[${value.map(canonicalStringify).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalStringify(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  }

  function timestampSortKey(timestamp) {
    return timestamp.includes(".") ? timestamp : `${timestamp.slice(0, -1)}.000000Z`;
  }

  function commandSortKey(command) {
    return [timestampSortKey(command.occurred_at), command.device_id, command.sequence, command.command_id];
  }

  function compareCommands(left, right) {
    const leftKey = commandSortKey(left);
    const rightKey = commandSortKey(right);
    for (let index = 0; index < leftKey.length; index += 1) {
      if (leftKey[index] < rightKey[index]) return -1;
      if (leftKey[index] > rightKey[index]) return 1;
    }
    return 0;
  }

  function normalizeCommand(value) {
    assertExactKeys(value, [
      "schema_version", "command_id", "paper_id", "action", "value",
      "occurred_at", "device_id", "sequence",
    ]);
    if (value.schema_version !== SCHEMA_VERSION || !COMMAND_ACTIONS.has(value.action)) fail();
    if (
      typeof value.value !== "boolean" || !Number.isSafeInteger(value.sequence)
      || value.sequence < 1 || value.sequence > MAX_FEEDBACK_SEQUENCE
    ) fail();
    return {
      schema_version: SCHEMA_VERSION,
      command_id: validateUuid(value.command_id),
      paper_id: normalizePaperId(value.paper_id),
      action: value.action,
      value: value.value,
      occurred_at: canonicalUtc(value.occurred_at),
      device_id: validateDeviceId(value.device_id),
      sequence: value.sequence,
    };
  }

  function normalizeCommands(values, requireCanonicalOrder) {
    if (!Array.isArray(values) || values.length > MAX_COMMANDS) fail();
    const commands = values.map(normalizeCommand);
    if (new Set(commands.map((command) => command.command_id)).size !== commands.length) fail();
    const sorted = [...commands].sort(compareCommands);
    if (requireCanonicalOrder && commands.some((command, index) => compareCommands(command, sorted[index]) !== 0)) fail();
    return sorted;
  }

  function createEmptyState(deviceId) {
    return {
      schema_version: SCHEMA_VERSION,
      device_id: validateDeviceId(deviceId),
      sequence: 0,
      commands: [],
    };
  }

  function validateState(value) {
    assertExactKeys(value, ["schema_version", "device_id", "sequence", "commands"]);
    if (
      value.schema_version !== SCHEMA_VERSION || !Number.isSafeInteger(value.sequence)
      || value.sequence < 0 || value.sequence > MAX_FEEDBACK_SEQUENCE
    ) fail();
    return {
      schema_version: SCHEMA_VERSION,
      device_id: validateDeviceId(value.device_id),
      sequence: value.sequence,
      commands: normalizeCommands(value.commands, true),
    };
  }

  function parseStoredState(serialized) {
    if (typeof serialized !== "string" || new TextEncoder().encode(serialized).byteLength > MAX_BACKUP_BYTES) fail();
    let value;
    try {
      value = JSON.parse(serialized);
    } catch (error) {
      fail();
    }
    return validateState(value);
  }

  function serializeState(state) {
    return JSON.stringify(validateState(state));
  }

  function appendFeedback(stateValue, paperId, action, value, options) {
    const state = validateState(stateValue);
    if (!ACTIONS.has(action) || typeof value !== "boolean" || !options || typeof options !== "object") fail();
    if (state.sequence >= MAX_FEEDBACK_SEQUENCE) fail();
    const sequence = state.sequence + 1;
    const command = normalizeCommand({
      schema_version: SCHEMA_VERSION,
      command_id: options.commandId,
      paper_id: paperId,
      action: `set_${action}`,
      value,
      occurred_at: options.occurredAt,
      device_id: state.device_id,
      sequence,
    });
    if (state.commands.some((item) => item.command_id === command.command_id)) fail();
    if (state.commands.length >= MAX_COMMANDS) fail();
    return {
      ...state,
      sequence,
      commands: [...state.commands, command].sort(compareCommands),
    };
  }

  function deriveRecords(stateValue) {
    const state = validateState(stateValue);
    const records = new Map();
    for (const command of state.commands) {
      const current = records.get(command.paper_id) || { read: false, favorite: false, irrelevant: false };
      if (command.action === "set_read") {
        current.read = command.value;
      } else if (command.action === "set_favorite") {
        current.favorite = command.value;
        if (command.value) current.irrelevant = false;
      } else {
        current.irrelevant = command.value;
        if (command.value) current.favorite = false;
      }
      records.set(command.paper_id, current);
    }
    return records;
  }

  function getPaperRecord(state, paperId) {
    const value = deriveRecords(state).get(normalizePaperId(paperId));
    return value ? { ...value } : { read: false, favorite: false, irrelevant: false };
  }

  async function sha256(value, subtle) {
    if (!subtle || typeof subtle.digest !== "function") fail();
    const bytes = new TextEncoder().encode(value);
    const digest = await subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  async function createBackupBundle(stateValue, options) {
    const state = validateState(stateValue);
    if (!options || typeof options !== "object") fail();
    const payload = {
      schema_version: SCHEMA_VERSION,
      bundle_id: validateUuid(options.bundleId),
      generated_at: canonicalUtc(options.generatedAt),
      commands: state.commands,
    };
    return { ...payload, digest: await sha256(canonicalStringify(payload), options.subtle) };
  }

  async function validateBackup(serialized, subtle) {
    if (typeof serialized !== "string" || new TextEncoder().encode(serialized).byteLength > MAX_BACKUP_BYTES) fail();
    let value;
    try {
      value = JSON.parse(serialized);
    } catch (error) {
      fail();
    }
    assertExactKeys(value, ["schema_version", "bundle_id", "generated_at", "commands", "digest"]);
    if (value.schema_version !== SCHEMA_VERSION || typeof value.digest !== "string" || !DIGEST_PATTERN.test(value.digest)) fail();
    const payload = {
      schema_version: SCHEMA_VERSION,
      bundle_id: validateUuid(value.bundle_id),
      generated_at: canonicalUtc(value.generated_at),
      commands: normalizeCommands(value.commands, true),
    };
    const digest = await sha256(canonicalStringify(payload), subtle);
    if (digest !== value.digest) fail();
    return { ...payload, digest };
  }

  function mergeCommands(existing, imported) {
    const commands = new Map(existing.map((command) => [command.command_id, command]));
    for (const command of imported) {
      const previous = commands.get(command.command_id);
      if (previous && canonicalStringify(previous) !== canonicalStringify(command)) fail();
      commands.set(command.command_id, command);
    }
    if (commands.size > MAX_COMMANDS) fail();
    return [...commands.values()].sort(compareCommands);
  }

  async function importBackup(serialized, currentState, storage, subtle) {
    const state = validateState(currentState);
    const bundle = await validateBackup(serialized, subtle);
    const commands = mergeCommands(state.commands, bundle.commands);
    const candidate = {
      ...state,
      sequence: commands
        .filter((command) => command.device_id === state.device_id)
        .reduce((maximum, command) => Math.max(maximum, command.sequence), state.sequence),
      commands,
    };
    const candidateSerialized = serializeState(candidate);
    storage.setItem(STORAGE_KEY, candidateSerialized);
    return candidate;
  }

  function closestPaper(element) {
    let current = element;
    while (current) {
      if (typeof current.hasAttribute === "function" && current.hasAttribute("data-paper-id")) return current;
      current = current.parentNode;
    }
    return null;
  }

  function setPressed(button, pressed, action) {
    button.setAttribute("aria-pressed", pressed ? "true" : "false");
    const label = button.querySelector("[data-feedback-label]");
    if (!label) return;
    const labels = {
      read: pressed ? "标记未读" : "标记已读",
      favorite: pressed ? "取消收藏" : "收藏",
      irrelevant: pressed ? "恢复相关" : "标记不相关",
    };
    label.textContent = labels[action];
  }

  function createBrowserAdapter(dependencies) {
    const document = dependencies.document;
    const storage = dependencies.storage;
    const cryptoAdapter = dependencies.crypto;
    const now = dependencies.now;
    const BlobAdapter = dependencies.Blob || globalScope.Blob;
    const URLAdapter = dependencies.URL || globalScope.URL;
    let state;

    const status = document.querySelector('[data-testid="feedback-status"]');
    const filters = Object.fromEntries(
      Array.from(document.querySelectorAll("[data-feedback-filter]")).map(
        (input) => [input.getAttribute("data-feedback-filter"), input]
      )
    );

    function newState() {
      return createEmptyState(`browser-${cryptoAdapter.randomUUID()}`);
    }

    function setStatus(message) {
      if (status) status.textContent = message;
    }

    function render() {
      for (const root of document.querySelectorAll("[data-paper-id]")) {
        const paperId = normalizePaperId(root.getAttribute("data-paper-id"));
        const record = getPaperRecord(state, paperId);
        for (const button of root.querySelectorAll("[data-feedback-action]")) {
          const action = button.getAttribute("data-feedback-action");
          setPressed(button, record[action], action);
        }
      }
      for (const card of document.querySelectorAll(".paper-card[data-paper-id]")) {
        const record = getPaperRecord(state, card.getAttribute("data-paper-id"));
        const hiddenByIrrelevant = record.irrelevant && !(filters["show-irrelevant"] && filters["show-irrelevant"].checked);
        const hiddenByRead = Boolean(filters.unread && filters.unread.checked && record.read);
        const hiddenByFavorite = Boolean(filters.favorite && filters.favorite.checked && !record.favorite);
        card.hidden = hiddenByIrrelevant || hiddenByRead || hiddenByFavorite;
        if (card.hidden) card.setAttribute("hidden", "");
        else card.removeAttribute("hidden");
      }
    }

    function persist(nextState) {
      state = nextState;
      try {
        storage.setItem(STORAGE_KEY, serializeState(state));
        setStatus("反馈已保存在当前浏览器。");
      } catch (error) {
        setStatus("无法保存浏览器反馈。请导出备份后重试。");
      }
      render();
    }

    function changeFeedback(root, action) {
      const paperId = root.getAttribute("data-paper-id");
      const current = getPaperRecord(state, paperId);
      if (state.commands.length >= MAX_COMMANDS || state.sequence >= MAX_FEEDBACK_SEQUENCE) {
        setStatus("反馈容量已满，请先导出备份并清理。");
        return;
      }
      try {
        const next = appendFeedback(state, paperId, action, !current[action], {
          commandId: cryptoAdapter.randomUUID(),
          occurredAt: now().toISOString(),
        });
        persist(next);
      } catch (error) {
        setStatus("无法更新浏览器反馈。请导出备份后重试。");
      }
    }

    function setBusy(control, busy, message) {
      control.disabled = busy;
      control.setAttribute("aria-busy", busy ? "true" : "false");
      if (message) setStatus(message);
    }

    function bindActions() {
      for (const button of document.querySelectorAll("[data-feedback-action]")) {
        button.addEventListener("click", () => {
          const root = closestPaper(button);
          if (root) changeFeedback(root, button.getAttribute("data-feedback-action"));
        });
      }
      for (const card of document.querySelectorAll(".paper-card[data-paper-id]")) {
        card.addEventListener("keydown", (event) => {
          if (event.target !== card) return;
          const action = { r: "read", f: "favorite", i: "irrelevant" }[String(event.key).toLowerCase()];
          if (!action) return;
          event.preventDefault();
          changeFeedback(card, action);
        });
      }
      for (const input of document.querySelectorAll("[data-feedback-filter]")) {
        input.addEventListener("input", render);
        input.addEventListener("change", render);
      }
    }

    function bindExport() {
      const control = document.querySelector('[data-ai-action="export-feedback"]');
      if (!control) return;
      control.addEventListener("click", async () => {
        setBusy(control, true, "正在准备反馈备份…");
        try {
          const bundle = await createBackupBundle(state, {
            bundleId: cryptoAdapter.randomUUID(),
            generatedAt: now().toISOString(),
            subtle: cryptoAdapter.subtle,
          });
          const blob = new BlobAdapter([`${JSON.stringify(bundle, null, 2)}\n`], { type: "application/json" });
          const url = URLAdapter.createObjectURL(blob);
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = "feedback-v1.json";
          document.body.appendChild(anchor);
          anchor.click();
          document.body.removeChild(anchor);
          URLAdapter.revokeObjectURL(url);
          setStatus("反馈备份已导出。");
        } catch (error) {
          setStatus("无法导出反馈备份。");
        } finally {
          setBusy(control, false, "");
        }
      });
    }

    function bindImport() {
      const control = document.querySelector('[data-ai-action="import-feedback"]');
      if (!control) return;
      control.addEventListener("change", async () => {
        const file = control.files && control.files[0];
        if (!file) return;
        setBusy(control, true, "正在校验反馈备份…");
        try {
          const candidate = await importBackup(await file.text(), state, storage, cryptoAdapter.subtle);
          state = candidate;
          render();
          setStatus("反馈备份已导入当前浏览器。");
        } catch (error) {
          setStatus("无法导入反馈备份。现有反馈保持不变。");
        } finally {
          control.value = "";
          setBusy(control, false, "");
        }
      });
    }

    function bindClear() {
      const control = document.querySelector('[data-ai-action="clear-feedback"]');
      if (!control) return;
      control.addEventListener("click", () => {
        try {
          storage.removeItem(STORAGE_KEY);
          state = newState();
          render();
          setStatus("本站浏览器反馈已清除。");
        } catch (error) {
          setStatus("无法清除浏览器反馈。");
        }
      });
    }

    function init() {
      try {
        const serialized = storage.getItem(STORAGE_KEY);
        state = serialized === null ? newState() : parseStoredState(serialized);
        setStatus("反馈已就绪，仅保存在当前浏览器。");
      } catch (error) {
        state = newState();
        setStatus("无法读取浏览器反馈。可清除本站反馈后重试。");
      }
      bindActions();
      bindExport();
      bindImport();
      bindClear();
      render();
    }

    return Object.freeze({ init, getState: () => state });
  }

  const api = Object.freeze({
    STORAGE_KEY,
    normalizePaperId,
    canonicalStringify,
    createEmptyState,
    parseStoredState,
    serializeState,
    appendFeedback,
    getPaperRecord,
    createBackupBundle,
    importBackup,
    createBrowserAdapter,
  });

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    globalScope.PaperDailyFeedback = api;
    const mount = () => {
      try {
        api.createBrowserAdapter({
          document: globalScope.document,
          storage: globalScope.localStorage,
          crypto: globalScope.crypto,
          now: () => new Date(),
          Blob: globalScope.Blob,
          URL: globalScope.URL,
        }).init();
      } catch (error) {
        const status = globalScope.document.querySelector('[data-testid="feedback-status"]');
        if (status) status.textContent = "无法启动浏览器反馈。";
      }
    };
    if (globalScope.document.readyState === "loading") {
      globalScope.document.addEventListener("DOMContentLoaded", mount);
    } else {
      mount();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : window);
