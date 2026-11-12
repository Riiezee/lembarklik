"use strict";

const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="local-token"]').content;
const groups = {
  "All tools": ["grid", "#e9efff", "#2648df"],
  "Organize": ["layers", "#e9efff", "#2648df"],
  "Optimize": ["compress", "#e8f8f1", "#137253"],
  "Convert to PDF": ["in", "#fff2e6", "#a34a17"],
  "Convert from PDF": ["out", "#e9efff", "#2648df"],
  "Edit": ["edit", "#f2eaff", "#7041be"],
  "Security": ["lock", "#fff0f3", "#a42e5c"],
  "Review": ["review", "#e8f7fa", "#16718b"],
};
const icons = {
  grid: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
  layers: '<path d="m12 4 8 4-8 4-8-4 8-4Zm-8 8 8 4 8-4M4 16l8 4 8-4"/>',
  compress: '<path d="M4 4l6 6M4 10h6V4m10 16-6-6m0 6v-6h6"/>',
  in: '<path d="M10 4h6l4 4v12H8v-6m8-10v5h4M4 11h9m-3-3 3 3-3 3"/>',
  out: '<path d="M15 4H6v16h13v-5m-4-11 4 4h-4V4m-4 8h10m-3-3 3 3-3 3"/>',
  edit: '<path d="m5 16-1 4 4-1L20 7l-3-3L5 16Zm9-9 3 3M12 20h8"/>',
  lock: '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 1 1 8 0v3m-4 4v2"/>',
  review: '<path d="M6 4h9l3 3v13H6zM9 9h6m-6 4h6m-6 4h4"/>',
  search: '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4 4"/>',
  upload: '<path d="M5 15v4h14v-4m-7 0V4m-4 4 4-4 4 4"/>',
};
const priority = ["word_to_pdf", "pdf_to_word", "merge", "optimize", "split", "images_to_pdf",
                  "organize", "protect", "pdf_to_jpg"];
const notes = {
  pdf_to_word: "DOCX contains editable text and detected tables. Fonts, graphics, and the original page layout may change. Scanned pages require Tesseract.",
  word_to_pdf: "Uses the fonts installed on this device and the LibreOffice layout engine.",
  pdf_to_excel: "Table detection works best on PDFs with visible table borders. Scanned tables may require OCR.",
  pdf_to_ppt: "Snapshots keep the page appearance. Editable text mode rebuilds slides as text.",
  pdfa: "LibreOffice reimports each page before exporting. Verify the appearance and validate archival compliance if required.",
  redact: "Export creates an image-only copy of every page. The marked pixels are covered, and original text, links, form fields, and metadata are discarded.",
  edit: "Add new content on top of the page. For Image mode, choose a PDF and a PNG or JPEG.",
  sign: "This places a visible signature. It does not apply a digital certificate or send signing requests.",
  crop: "Cropping hides content outside the selected area. To permanently remove sensitive material, use Redact.",
  html_to_pdf: "Converts static HTML with basic inline formatting. Scripts, images and external resources are omitted.",
  ocr: "Pages with existing extractable text are kept. Image-only pages are rendered and rebuilt with a searchable text layer.",
  summarize: "Built-in mode selects key sentences. Local AI mode uses a configured GGUF model for up to 2,800 characters.",
  repair: "Recovery depends on how much of the original PDF remains readable.",
  translate: "Translates extracted text, up to 2,800 characters per export, with the local model you configure.",
};
let tools = [];
let availability = {};
let help = {};
let category = "All tools";
let state;
let translations = {};
let language = "id";
try {
  if (sessionStorage.getItem("lembarklik.language") === "en") language = "en";
} catch {
  // The workspace still works when browser storage is unavailable.
}

function t(key, values = {}) {
  const message = language === "id" ? translations[key] ?? key : key;
  return message.replace(/\{(\w+)\}/g, (match, name) => values[name] ?? match);
}

function applyLanguage() {
  document.documentElement.lang = language;
  $("language").value = language;
  for (const [attribute, output] of [["data-i18n", null], ["data-i18n-label", "aria-label"],
                                     ["data-i18n-placeholder", "placeholder"]]) {
    for (const element of document.querySelectorAll("[" + attribute + "]")) {
      const value = t(element.getAttribute(attribute));
      if (output) element.setAttribute(output, value);
      else element.textContent = value;
    }
  }
}

function changeLanguage() {
  if (state?.busy || state?.inspecting) {
    $("language").value = language;
    return;
  }
  language = $("language").value === "en" ? "en" : "id";
  try { sessionStorage.setItem("lembarklik.language", language); } catch {}
  const previous = state;
  const scroll = window.scrollY;
  applyLanguage();
  dashboard();
  if (previous) renderWorkspace(previous.tool, previous);
  window.scrollTo(0, scroll);
  $("language").focus({preventScroll: true});
}

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function button(text, className, action) {
  const element = node("button", className, text);
  element.type = "button";
  element.addEventListener("click", action);
  return element;
}

function icon(key, className = "") {
  const element = node("span", "icon " + className);
  element.setAttribute("aria-hidden", "true");
  element.innerHTML = '<svg viewBox="0 0 24 24">' + icons[key] + '</svg>';
  return element;
}

function toolIcon(tool) {
  const [key, tint, tone] = groups[tool.category];
  const element = icon(key, "tool-icon");
  element.style.setProperty("--tint", tint);
  element.style.setProperty("--tone", tone);
  return element;
}

function selectCategory(name) {
  if (state?.busy) return;
  category = name;
  $("search").value = "";
  if (location.hash) location.hash = "";
  else dashboard();
}

function dashboard() {
  stopCamera(state);
  state = null;
  $("language").disabled = false;
  $("dashboard").hidden = false;
  $("workspace").hidden = true;
  $("workspace").replaceChildren();
  $("crumb").textContent = t("Workspace") + "  /  " + t(category);
  $("navigation").replaceChildren();
  $("filters").replaceChildren();
  for (const [name, [glyph]] of Object.entries(groups)) {
    const nav = button("", "nav-button", () => selectCategory(name));
    nav.append(icon(glyph), node("span", "", t(name)));
    if (category === name) nav.setAttribute("aria-current", "true");
    $("navigation").append(nav);

    const filter = button(t(name), "filter", () => {
      selectCategory(name);
      $("tool-library").scrollIntoView({block: "start"});
      $("filters").children[Object.keys(groups).indexOf(name)]?.focus({preventScroll: true});
    });
    filter.setAttribute("aria-pressed", String(name === category));
    $("filters").append(filter);
  }
  renderCards();
}

function renderCards() {
  const query = $("search").value.toLowerCase().trim();
  const found = tools.filter(tool => {
    const searchable = [t(tool.title), t(tool.description), t(tool.category), tool.title, tool.ext, tool.output].join(" ").toLowerCase();
    return (category === "All tools" || tool.category === category)
      && query.split(/\s+/).every(word => searchable.includes(word));
  });
  $("tool-count").textContent = t(found.length === 1 ? "{count} tool · Local processing" : "{count} tools · Local processing", {count: found.length});
  const cards = $("tool-grid");
  cards.replaceChildren();
  for (const tool of found) {
    const card = button("", "tool-card", () => { location.hash = tool.key; });
    const text = node("span");
    text.append(node("strong", "", t(tool.title)), node("small", "", t(tool.description)));
    card.append(toolIcon(tool), text, node("span", "arrow", "↗"));
    cards.append(card);
  }
  if (!found.length) cards.append(node("p", "empty", t("No matching tools. Try a different search.")));
}

function renderWorkspace(tool, previous) {
  stopCamera(state);
  const multi = ["merge", "compare", "images_to_pdf", "scan", "sign", "edit"].includes(tool.key);
  state = {tool, files: [], controls: {}, regions: [], formInputs: new Map(), busy: false, multi, page: 1, pages: 0};
  const current = state;
  if (previous) {
    current.files = previous.files;
    current.regions = previous.regions;
    current.page = previous.page;
    current.pages = previous.pages;
  }
  $("dashboard").hidden = true;
  const work = $("workspace");
  work.hidden = false;
  work.replaceChildren();
  $("crumb").textContent = t("Workspace") + "  /  " + t(tool.title);
  const back = button(t("← All tools"), "secondary back", () => { location.hash = ""; });
  const heading = node("div", "tool-heading");
  const copy = node("div");
  copy.append(node("h1", "", t(tool.title)), node("p", "", t(tool.description)));
  heading.append(toolIcon(tool), copy);
  work.append(back, heading);
  if (notes[tool.key]) work.append(node("div", "notice", t(notes[tool.key])));
  current.dependencyNotice = node("div", "notice");
  current.dependencyNotice.hidden = true;
  work.append(current.dependencyNotice);

  const inputPanel = node("section", "workspace-panel");
  inputPanel.append(node("h2", "", t("1. Select files")));
  const drop = node("div", "drop");
  const picker = node("input");
  picker.type = "file";
  picker.hidden = true;
  picker.multiple = multi;
  picker.accept = tool.ext.split(",").map(ext => "." + ext).join(",");
  const choose = button(t(multi ? "Browse files" : "Browse file"), "secondary", () => picker.click());
  drop.append(icon("upload"), node("strong", "", t("Drop files here")), choose,
              node("p", "", tool.ext.toUpperCase().replaceAll(",", ", ") + " · " + t("80 MB total")), picker);
  inputPanel.append(drop);
  if (tool.key === "scan") inputPanel.append(cameraControls(current));
  current.list = node("ol", "file-list");
  inputPanel.append(current.list);
  work.append(inputPanel);
  picker.addEventListener("change", () => { addFiles(current, Array.from(picker.files)); picker.value = ""; });
  for (const event of ["dragenter", "dragover"]) drop.addEventListener(event, e => {
    e.preventDefault();
    if (!current.busy) drop.classList.add("dragging");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragging"));
  drop.addEventListener("drop", e => {
    e.preventDefault();
    drop.classList.remove("dragging");
    addFiles(current, Array.from(e.dataTransfer.files));
  });

  if (tool.fields.length) {
    const optionsPanel = node("section", "workspace-panel");
    optionsPanel.append(node("h2", "", t("2. Set options")));
    const fields = node("div", "fields");
    for (const field of tool.fields) fields.append(renderField(current, field));
    optionsPanel.append(fields);
    work.append(optionsPanel);
  }
  const exportPanel = node("section", "workspace-panel submit-row");
  current.message = node("p", "message", t("Choose your input files to begin."));
  current.message.setAttribute("role", "status");
  current.run = button(t("Export {format}", {format: tool.output.toUpperCase()}), "primary", () => runTool(current));
  exportPanel.append(current.message, current.run);
  work.append(exportPanel);
  if (previous) {
    for (const [key, input] of Object.entries(current.controls)) input.value = previous.controls[key].value;
    renderFiles(current);
    const image = previous.sheet?.querySelector("img");
    if (image) showPreview(current, image.src, current.page, current.pages);
    if (current.formBox && previous.files.length) {
      const fields = Array.from(previous.formInputs, ([name, input]) => [name, input.value]);
      showFields(current, fields);
    }
  } else {
    updateReady(current);
    window.scrollTo(0, 0);
    back.focus({preventScroll: true});
  }
}

function stopCamera(current) {
  if (!current?.camera) return;
  current.camera.getTracks().forEach(track => track.stop());
  current.camera = null;
  current.video.srcObject = null;
  current.cameraView.hidden = true;
  current.cameraButton.disabled = false;
}

function cameraControls(current) {
  const panel = node("div", "camera-panel");
  current.cameraView = node("div", "camera-view");
  current.cameraView.hidden = true;
  current.video = node("video");
  current.video.autoplay = true;
  current.video.muted = true;
  current.video.playsInline = true;
  current.video.setAttribute("aria-label", t("Local camera preview"));
  const controls = node("div", "preview-controls");
  const capture = button(t("Capture page"), "primary", () => {
    if (current.busy || !current.video.videoWidth) return;
    const canvas = node("canvas");
    canvas.width = current.video.videoWidth;
    canvas.height = current.video.videoHeight;
    canvas.getContext("2d").drawImage(current.video, 0, 0);
    canvas.toBlob(blob => {
      if (blob && state === current) {
        addFiles(current, [new File([blob], "scan-" + Date.now() + ".jpg", {type: "image/jpeg"})]);
      }
    }, "image/jpeg", .92);
  });
  controls.append(capture, button(t("Close camera"), "secondary", () => stopCamera(current)));
  current.cameraView.append(current.video, controls);
  current.cameraButton = button(t("Open camera"), "secondary", async () => {
    if (current.busy) return;
    current.cameraButton.disabled = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false, video: {facingMode: {ideal: "environment"}, width: {ideal: 1920}},
      });
      if (state !== current || current.busy) {
        stream.getTracks().forEach(track => track.stop());
        current.cameraButton.disabled = false;
        return;
      }
      current.camera = stream;
      current.video.srcObject = stream;
      current.cameraView.hidden = false;
    } catch {
      setMessage(current, t("Camera unavailable. Check browser permissions or choose photos from your device."), "error");
      current.cameraButton.disabled = false;
    }
  });
  panel.append(current.cameraButton, current.cameraView);
  return panel;
}

function setMessage(current, text, type = "") {
  current.message.className = "message" + (type ? " " + type : "");
  current.message.textContent = text;
}

function updateReady(current) {
  $("language").disabled = current.busy || Boolean(current.inspecting);
  const {key, dep} = current.tool;
  let required = dep;
  if (key === "pdf_to_word" && current.controls.ocr.value !== "Off") required = "ocr";
  if (key === "summarize" && current.controls.mode.value === "Local AI") required = "model";
  const missing = Boolean(required && !availability[required]);
  current.dependencyNotice.hidden = !missing;
  current.dependencyNotice.textContent = missing ? t(help[required] || "A local program is required.") : "";
  current.run.disabled = current.busy || current.files.length < current.tool.min || missing;
}

function addFiles(current, incoming) {
  if (current.busy || !incoming.length) return;
  const tool = current.tool;
  if (!current.multi && incoming.length > 1) {
    setMessage(current, t("Choose one file for this tool."), "error");
    return;
  }
  if (incoming.some(file => !tool.ext.split(",").includes(file.name.split(".").pop().toLowerCase()))) {
    setMessage(current, t("Choose files in one of the formats shown above."), "error");
    return;
  }
  const previous = current.files[0];
  let files = current.multi ? [...current.files, ...incoming] : incoming;
  if (["sign", "edit"].includes(tool.key)) {
    files = [...files.filter(file => /\.pdf$/i.test(file.name)), ...files.filter(file => !/\.pdf$/i.test(file.name))];
    if (files.length > 2 || !/\.pdf$/i.test(files[0].name) || files.slice(1).some(file => /\.pdf$/i.test(file.name))) {
      setMessage(current, t("Choose one PDF and optionally one image."), "error");
      return;
    }
  }
  if (files.length > (tool.key === "compare" ? 2 : 40)
      || files.reduce((total, file) => total + file.size, 0) > 80 * 1024 * 1024) {
    setMessage(current, t(tool.key === "compare" ? "Choose exactly two PDFs, up to 80 MB total."
               : "Choose up to 40 files totaling no more than 80 MB."), "error");
    return;
  }
  current.files = files;
  renderFiles(current);
  if (previous !== files[0]) resetPreview(current);
}

function renderFiles(current) {
  current.list.replaceChildren();
  current.files.forEach((file, index) => {
    const item = node("li");
    item.append(node("span", "name", file.name),
                node("span", "size", (file.size / 1024 / 1024).toFixed(2) + " MB"));
    if (current.multi && !["sign", "edit"].includes(current.tool.key)) {
      for (const [direction, symbol] of [[-1, "↑"], [1, "↓"]]) {
        const target = index + direction;
        const move = button(symbol, "", () => {
          if (current.busy) return;
          [current.files[index], current.files[target]] = [current.files[target], current.files[index]];
          renderFiles(current);
        });
        move.setAttribute("aria-label", t(direction < 0 ? "Move file up" : "Move file down"));
        move.disabled = target < 0 || target >= current.files.length;
        item.append(move);
      }
    }
    const remove = button(t("Remove"), "", () => {
      if (current.busy) return;
      current.files.splice(index, 1);
      if (["sign", "edit"].includes(current.tool.key) && index === 0) current.files = [];
      renderFiles(current);
      if (index === 0) resetPreview(current);
    });
    remove.setAttribute("aria-label", t("Remove {file}", {file: file.name}));
    item.append(remove);
    current.list.append(item);
  });
  setMessage(current, current.files.length < current.tool.min
    ? t(current.tool.min === 1 ? "Choose {count} input file." : "Choose {count} input files.", {count: current.tool.min})
    : t("Ready to export on this device."));
  updateReady(current);
}

function renderField(current, [key, label, kind, value, extra]) {
  if (kind === "region") return previewField(current, t(label));
  if (kind === "forms") {
    current.formBox = node("div", "full fields");
    current.formBox.append(node("p", "", t("Select a PDF to load its fields.")));
    return current.formBox;
  }
  const wrapper = node("label", "field", t(label));
  const input = node(kind === "select" ? "select" : "input");
  if (kind === "select") {
    for (const option of extra) {
      const item = node("option", "", t(option));
      item.value = option;
      input.append(item);
    }
  } else {
    input.type = kind;
    input.placeholder = extra ? t(extra) : "";
    if (kind === "password") input.autocomplete = "off";
  }
  input.value = value;
  wrapper.append(input);
  if (extra && kind === "text") wrapper.append(node("small", "", t(extra)));
  if (key === "mode" && current.tool.key === "optimize") {
    const hint = node("small", "", t("Rasterized modes flatten text, links, and forms into images."));
    wrapper.append(hint);
  }
  current.controls[key] = input;
  if ((key === "ocr" && current.tool.key === "pdf_to_word") ||
      (key === "mode" && current.tool.key === "summarize")) {
    input.addEventListener("change", () => updateReady(current));
  }
  return wrapper;
}

function previewField(current, title) {
  const field = node("div", "field full");
  field.append(node("strong", "", title), node("small", "", t("Drag a rectangle over the page. Use Undo to remove the last mark.")));
  const controls = node("div", "preview-controls");
  current.pageInput = node("input");
  current.pageInput.type = "number";
  current.pageInput.min = 1;
  current.pageInput.value = 1;
  current.pageInput.setAttribute("aria-label", t("Page number"));
  const view = button(t("View page"), "secondary", () => loadPreview(current, Number(current.pageInput.value)));
  const undo = button(t("Undo"), "secondary", () => {
    if (current.busy) return;
    current.regions.pop();
    drawAreas(current);
  });
  current.pageText = node("small", "", t("Select a PDF"));
  current.pageInput.addEventListener("keydown", event => {
    if (event.key === "Enter") loadPreview(current, Number(current.pageInput.value));
  });
  controls.append(current.pageInput, view, undo, current.pageText);
  current.board = node("div", "preview-board");
  current.board.append(node("small", "", t("The selected PDF will appear here.")));
  field.append(controls, current.board);
  return field;
}

function resetPreview(current) {
  current.regions = [];
  current.page = 1;
  current.pages = 0;
  if (current.pageInput) current.pageInput.value = 1;
  if (current.board) {
    current.sheet = null;
    current.board.replaceChildren(node("small", "", t("The selected PDF will appear here.")));
    current.pageText.textContent = t("Select a PDF");
    if (current.files.length) loadPreview(current, 1);
  }
  if (current.formBox) {
    current.formInputs.clear();
    current.formBox.replaceChildren(node("p", "", t("Select a PDF to load its fields.")));
    if (current.files.length) loadForms(current);
  }
}

function request(endpoint, files, metadata) {
  const form = new FormData();
  form.append("metadata", JSON.stringify(metadata));
  for (const file of files) form.append("file", file);
  return fetch("/api/" + endpoint, {method: "POST", body: form, headers: {"X-LembarKlik-Token": token, "Accept-Language": language}});
}

class LocalTaskError extends Error {}

function taskErrorMessage(error) {
  return error instanceof LocalTaskError ? error.message : t("The local task could not finish.");
}

async function resultJson(response) {
  const json = await response.json();
  if (!response.ok) throw new LocalTaskError(json.error || t("The local task could not finish."));
  return json;
}

async function loadPreview(current, number) {
  if (current.busy || !current.files.length || current.inspecting) return;
  if (!Number.isInteger(number) || number < 1 || (current.pages && number > current.pages)) {
    setMessage(current, t("Enter a page number within this PDF."), "error");
    return;
  }
  const source = current.files[0];
  current.inspecting = true;
  updateReady(current);
  current.pageText.textContent = t("Loading preview…");
  try {
    const json = await resultJson(await request("inspect", [source], {page: number}));
    if (state !== current || current.files[0] !== source) return;
    showPreview(current, json.image, number, json.pages);
  } catch (error) {
    if (state === current && current.files[0] === source) {
      setMessage(current, taskErrorMessage(error), "error");
      current.pageText.textContent = t("Preview unavailable");
    }
  } finally {
    current.inspecting = false;
    if (state === current) updateReady(current);
    if (state === current && current.files.length && current.files[0] !== source) loadPreview(current, 1);
  }
}

function showPreview(current, source, number, pages) {
  current.page = number;
  current.pages = pages;
  current.pageInput.max = pages;
  current.pageInput.value = number;
  current.sheet = node("div", "preview-sheet");
  const image = node("img");
  image.alt = t("Preview of page {page}", {page: number});
  image.draggable = false;
  image.src = source;
  current.sheet.append(image);
  current.board.replaceChildren(current.sheet);
  current.sheet.addEventListener("pointerdown", event => markArea(current, event));
  drawAreas(current);
}

function drawAreas(current, temporary) {
  if (!current.sheet) return;
  current.sheet.querySelectorAll(".area").forEach(area => area.remove());
  for (const [page, x1, y1, x2, y2] of temporary ? [...current.regions, temporary] : current.regions) {
    if (page !== current.page) continue;
    const mark = node("span", current.tool.key === "redact" ? "area redaction" : "area");
    Object.assign(mark.style, {left: x1 * 100 + "%", top: y1 * 100 + "%",
                             width: (x2 - x1) * 100 + "%", height: (y2 - y1) * 100 + "%"});
    current.sheet.append(mark);
  }
  const count = current.regions.length;
  current.pageText.textContent = t(count === 1 ? "Page {page} of {total} · {count} area"
    : "Page {page} of {total} · {count} areas", {page: current.page, total: current.pages, count});
}

function markArea(current, event) {
  if (event.button !== 0 || current.busy || current.inspecting) return;
  const sheet = current.sheet;
  const page = current.page;
  const bounds = sheet.getBoundingClientRect();
  const clamp = value => Math.max(0, Math.min(1, value));
  const point = e => [clamp((e.clientX - bounds.left) / bounds.width), clamp((e.clientY - bounds.top) / bounds.height)];
  const [x1, y1] = point(event);
  const rectangle = e => {
    const [x2, y2] = point(e);
    return [page, Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)];
  };
  const move = e => drawAreas(current, rectangle(e));
  const finish = e => {
    sheet.removeEventListener("pointermove", move);
    sheet.removeEventListener("pointerup", finish);
    sheet.removeEventListener("pointercancel", finish);
    if (e.type === "pointerup" && current.sheet === sheet) {
      const area = rectangle(e);
      if (area[3] - area[1] >= .003 && area[4] - area[2] >= .003) {
        if (current.tool.key === "redact" && current.regions.length < 60) current.regions.push(area);
        else if (current.tool.key !== "redact") current.regions = [area];
      }
    }
    drawAreas(current);
  };
  sheet.setPointerCapture(event.pointerId);
  sheet.addEventListener("pointermove", move);
  sheet.addEventListener("pointerup", finish);
  sheet.addEventListener("pointercancel", finish);
}

async function loadForms(current) {
  if (current.inspecting) return;
  const source = current.files[0];
  current.inspecting = true;
  updateReady(current);
  current.formBox.replaceChildren(node("p", "", t("Loading fields…")));
  try {
    const json = await resultJson(await request("inspect", [source], {kind: "forms"}));
    if (state !== current || current.files[0] !== source) return;
    showFields(current, json.fields);
  } catch (error) {
    if (state === current && current.files[0] === source) setMessage(current, taskErrorMessage(error), "error");
  } finally {
    current.inspecting = false;
    if (state === current) updateReady(current);
    if (state === current && current.files.length && current.files[0] !== source) loadForms(current);
  }
}

function showFields(current, fields) {
  current.formBox.replaceChildren();
  if (!fields.length) current.formBox.append(node("p", "", t("No supported form fields were found.")));
  for (const [name, value] of fields) {
    const label = node("label", "field", name);
    const field = node("input");
    field.type = "text";
    field.value = value;
    field.maxLength = 4000;
    current.formInputs.set(name, field);
    label.append(field);
    current.formBox.append(label);
  }
}

async function runTool(current) {
  if (current.busy) return;
  if (current.inspecting) {
    setMessage(current, t("Wait for the preview to finish loading."), "error");
    return;
  }
  const options = Object.fromEntries(Object.entries(current.controls).map(([key, input]) => [key, input.value]));
  if (current.board) {
    if (!current.regions.length) {
      setMessage(current, t("Draw an area on the page preview first."), "error");
      return;
    }
    options.region = current.regions;
  }
  if (current.formBox) options.fields = Object.fromEntries(Array.from(current.formInputs, ([name, input]) => [name, input.value]));
  current.busy = true;
  stopCamera(current);
  updateReady(current);
  setMessage(current, t("Processing locally…"));
  try {
    const response = await request("run", current.files, {tool: current.tool.key, options});
    if (!response.ok) await resultJson(response);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const download = node("a");
    download.href = url;
    download.download = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1]
      || "lembarklik." + current.tool.output;
    document.body.append(download);
    download.click();
    download.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    if (current.controls.password) current.controls.password.value = "";
    setMessage(current, t("Export ready · {file}", {file: download.download}), "done");
  } catch (error) {
    setMessage(current, taskErrorMessage(error), "error");
  } finally {
    current.busy = false;
    updateReady(current);
  }
}

function route() {
  if (state?.busy) {
    if (location.hash.slice(1) !== state.tool.key) history.replaceState(null, "", "#" + state.tool.key);
    return;
  }
  const selected = tools.find(tool => "#" + tool.key === location.hash);
  if (selected) renderWorkspace(selected);
  else dashboard();
}

async function start() {
  try {
    const [catalog, status, indonesian] = await Promise.all([
      fetch("/tools.json").then(resultJson), fetch("/api/status").then(resultJson),
      fetch("/id.json").then(resultJson),
    ]);
    translations = indonesian;
    applyLanguage();
    $("language").addEventListener("change", changeLanguage);
    tools = catalog.map(([key, title, category, description, ext, output, min, fields, dep]) =>
      ({key, title, category, description, ext, output, min, fields, dep}));
    tools.sort((a, b) => (priority.indexOf(a.key) + 1 || 999) - (priority.indexOf(b.key) + 1 || 999));
    ({programs: availability, help} = status);
    $("search-icon").append(icon("search"));
    $("search").addEventListener("input", renderCards);
    $("home").addEventListener("click", () => selectCategory("All tools"));
    $("mobile-home").addEventListener("click", () => selectCategory("All tools"));
    $("explore").addEventListener("click", () => $("tool-library").scrollIntoView({behavior: "smooth"}));
    window.addEventListener("hashchange", route);
    window.addEventListener("pagehide", () => stopCamera(state));
    dashboard();
    route();
  } catch {
    $("tool-grid").append(node("p", "empty", t("Could not start the workspace. Reopen LembarKlik from its launcher.")));
  }
}

start();
