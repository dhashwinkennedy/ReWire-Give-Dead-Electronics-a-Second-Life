(() => {
  "use strict";

  const itemList = document.getElementById("item-list");
  const vocabByKey = new Map();
  const vocabById = new Map();
  const appState = { vocab: [], focusedAutocomplete: null, activeSuggestion: -1, latestPayload: null };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const normalize = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().replace(/\s+/g, " ");
  const slugLabel = (id) => id.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());

  async function loadVocabulary() {
    try {
      const response = await fetch("/static/vocab.json", { cache: "force-cache" });
      if (!response.ok) throw new Error("Autocomplete vocabulary could not be loaded.");
      const data = await response.json();
      appState.vocab = data.items || [];
      for (const item of appState.vocab) {
        vocabById.set(`${item.kind}:${item.id}`, item);
        for (const phrase of [item.name, item.id, ...(item.aliases || [])]) {
          const key = normalize(phrase);
          if (!key) continue;
          if (!vocabByKey.has(key)) vocabByKey.set(key, []);
          vocabByKey.get(key).push(item);
        }
      }
    } catch (error) {
      setFormMessage(error.message || "Vocabulary could not be loaded.");
    }
  }

  function setFormMessage(message) {
    document.getElementById("form-message").textContent = message || "";
  }

  function addItemRow(initial = {}) {
    const rowId = `row-${Math.random().toString(36).slice(2, 10)}`;
    const row = document.createElement("div");
    row.className = "item-row";
    row.dataset.rowId = rowId;
    row.innerHTML = `
      <div class="item-name">
        <label class="field-label" for="${rowId}-name">Name or alias</label>
        <div class="input-wrap">
          <input id="${rowId}-name" class="text-input item-name-input" type="text" autocomplete="off" maxlength="80" placeholder="e.g. old Nokia, buzzer, LDR" aria-autocomplete="list" aria-expanded="false" aria-controls="${rowId}-suggestions">
          <div id="${rowId}-suggestions" class="autocomplete-list" role="listbox" hidden></div>
        </div>
      </div>
      <div class="item-kind">
        <label class="field-label" for="${rowId}-kind">Item type</label>
        <select id="${rowId}-kind" class="select-input item-kind-select">
          <option value="auto">Auto detect</option><option value="component">Component</option><option value="device">Device</option>
        </select>
      </div>
      <div class="item-quantity">
        <label class="field-label" for="${rowId}-quantity">Quantity</label>
        <input id="${rowId}-quantity" class="text-input item-quantity-input" type="number" min="1" max="999" value="1" inputmode="numeric">
      </div>
      <div class="item-condition">
        <label class="field-label" for="${rowId}-condition">Condition</label>
        <select id="${rowId}-condition" class="select-input item-condition-select">
          <option value="working">Working</option><option value="unknown">Unknown — needs testing</option><option value="defective">Defective</option>
        </select>
      </div>
      <button class="remove-button" type="button" aria-label="Remove item row" title="Remove item">×</button>`;
    if (initial.name) row.querySelector(".item-name-input").value = initial.name;
    if (initial.kind) row.querySelector(".item-kind-select").value = initial.kind;
    row.querySelector(".remove-button").addEventListener("click", () => { row.remove(); updateEmptyHint(); });
    row.querySelector(".item-name-input").addEventListener("input", () => {
      delete row.dataset.selectedId;
      delete row.dataset.selectedKind;
      hideDefects(row);
      renderAutocomplete(row);
    });
    row.querySelector(".item-name-input").addEventListener("focus", () => renderAutocomplete(row));
    row.querySelector(".item-name-input").addEventListener("keydown", (event) => autocompleteKeydown(event, row));
    row.querySelector(".item-kind-select").addEventListener("change", () => {
      const selectedKind = row.querySelector(".item-kind-select").value;
      if (row.dataset.selectedKind && selectedKind !== "auto" && row.dataset.selectedKind !== selectedKind) {
        delete row.dataset.selectedId;
        delete row.dataset.selectedKind;
      }
      renderAutocomplete(row);
      if (selectedKind !== "device") hideDefects(row);
    });
    row.querySelector(".item-condition-select").addEventListener("change", () => {
      if (row.querySelector(".item-condition-select").value !== "defective") hideDefects(row);
      else refreshDefects(row);
    });
    itemList.appendChild(row);
    if (initial.selected) selectVocabularyItem(row, initial.selected, false);
    updateEmptyHint();
    return row;
  }

  function updateEmptyHint() {
    const empty = itemList.querySelector(".empty-inventory");
    if (itemList.querySelectorAll(".item-row").length === 0 && !empty) {
      const hint = document.createElement("div");
      hint.className = "empty-inventory";
      hint.textContent = "No parts added yet. Find projects to browse the complete collection, or add an item to get a match.";
      itemList.appendChild(hint);
    } else if (itemList.querySelectorAll(".item-row").length > 0 && empty) empty.remove();
  }

  function autocompleteCandidates(query, kind) {
    const q = normalize(query);
    if (!q) return [];
    const entries = appState.vocab.filter((item) => kind === "auto" || item.kind === kind);
    const scores = entries.map((item) => {
      const variants = [item.name, item.id, ...(item.aliases || [])].map(normalize);
      let score = 0;
      for (const variant of variants) {
        if (variant === q) score = Math.max(score, 100);
        else if (variant.startsWith(q)) score = Math.max(score, 80 - Math.min(25, variant.length - q.length));
        else if (variant.includes(q)) score = Math.max(score, 55 - Math.min(20, variant.indexOf(q)));
        else {
          const qTokens = new Set(q.split(" "));
          const vTokens = new Set(variant.split(" "));
          const common = [...qTokens].filter((token) => vTokens.has(token)).length;
          if (common) score = Math.max(score, 20 + 12 * common / Math.max(qTokens.size, vTokens.size));
        }
      }
      return { item, score };
    }).filter((entry) => entry.score > 0);
    scores.sort((a, b) => b.score - a.score || a.item.name.localeCompare(b.item.name));
    return scores.slice(0, 8).map((entry) => entry.item);
  }

  function renderAutocomplete(row) {
    const input = row.querySelector(".item-name-input");
    const list = row.querySelector(".autocomplete-list");
    const kind = row.querySelector(".item-kind-select").value;
    const candidates = autocompleteCandidates(input.value, kind);
    appState.activeSuggestion = -1;
    if (!input.value.trim() || !candidates.length) {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      list.innerHTML = "";
      return;
    }
    list.innerHTML = candidates.map((item, index) => `<button class="autocomplete-option" type="button" role="option" id="${row.dataset.rowId}-option-${index}" aria-selected="false" data-vocab-id="${esc(item.id)}" data-vocab-kind="${item.kind}"><span>${esc(item.name)}</span><span class="type-hint">${item.kind}</span></button>`).join("");
    list.querySelectorAll("[data-vocab-id]").forEach((button) => {
      button.addEventListener("mousedown", (event) => event.preventDefault());
      button.addEventListener("click", () => {
        const item = vocabById.get(`${button.dataset.vocabKind}:${button.dataset.vocabId}`);
        if (item) selectVocabularyItem(row, item, true);
      });
    });
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function autocompleteKeydown(event, row) {
    const list = row.querySelector(".autocomplete-list");
    if (list.hidden) return;
    const options = [...list.querySelectorAll("[role=option]")];
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      appState.activeSuggestion = (appState.activeSuggestion + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      options.forEach((option, index) => option.setAttribute("aria-selected", String(index === appState.activeSuggestion)));
      options[appState.activeSuggestion]?.scrollIntoView({ block: "nearest" });
    } else if (event.key === "Enter" && appState.activeSuggestion >= 0) {
      event.preventDefault();
      options[appState.activeSuggestion]?.click();
    } else if (event.key === "Escape") {
      list.hidden = true;
      row.querySelector(".item-name-input").setAttribute("aria-expanded", "false");
    }
  }

  function selectVocabularyItem(row, item, setInput = true) {
    const input = row.querySelector(".item-name-input");
    if (setInput) input.value = item.name;
    row.dataset.selectedId = item.id;
    row.dataset.selectedKind = item.kind;
    const kindSelect = row.querySelector(".item-kind-select");
    if (kindSelect.value === "auto" || kindSelect.value !== item.kind) kindSelect.value = item.kind;
    row.querySelector(".autocomplete-list").hidden = true;
    input.setAttribute("aria-expanded", "false");
    refreshDefects(row);
  }

  function hideDefects(row) {
    const old = row.querySelector(".defect-box");
    if (old) old.remove();
  }

  function refreshDefects(row) {
    hideDefects(row);
    if (row.querySelector(".item-kind-select").value !== "device" || row.querySelector(".item-condition-select").value !== "defective") return;
    const device = row.dataset.selectedId ? vocabById.get(`device:${row.dataset.selectedId}`) : null;
    if (!device) return;
    const common = new Set(device.common_failure_components || []);
    const parts = [...(device.components || [])].sort((a, b) => Number(b.do_not_salvage) - Number(a.do_not_salvage) || Number(!common.has(a.id)) - Number(!common.has(b.id)) || a.name.localeCompare(b.name));
    const fieldset = document.createElement("fieldset");
    fieldset.className = "defect-box";
    fieldset.innerHTML = `<legend>Mark defective parts (optional)</legend><div class="defect-options">${parts.map((part) => {
      const isCommon = common.has(part.id);
      const disabled = part.do_not_salvage ? "disabled" : "";
      const help = part.do_not_salvage ? " · safety exclusion" : "";
      return `<label class="defect-option ${isCommon ? "common" : ""}"><input type="checkbox" value="${esc(part.id)}" ${disabled}><span>${esc(part.name)}${help}</span>${isCommon ? '<span class="common-tag">common failure</span>' : ""}</label>`;
    }).join("")}</div>`;
    row.appendChild(fieldset);
  }

  function getPayload() {
    const rows = [...itemList.querySelectorAll(".item-row")];
    const items = [];
    for (const row of rows) {
      const typedName = row.querySelector(".item-name-input").value.trim();
      if (!typedName) return { error: "Add a name to each row or remove the blank row." };
      const quantity = Number(row.querySelector(".item-quantity-input").value);
      if (!Number.isInteger(quantity) || quantity < 1 || quantity > 999) return { error: "Quantity must be a whole number from 1 to 999." };
      const item = { name: row.dataset.selectedId || typedName, quantity,
        condition: row.querySelector(".item-condition-select").value };
      const kind = row.querySelector(".item-kind-select").value;
      const selectedKind = row.dataset.selectedKind;
      if (selectedKind) item.kind = selectedKind;
      else if (kind !== "auto") item.kind = kind;
      const defects = [...row.querySelectorAll(".defect-box input:checked")].map((input) => input.value);
      if (defects.length) { item.kind = "device"; item.defective_components = defects; }
      items.push(item);
    }
    return { payload: { items, options: { max_results: 10, min_score: 40, max_difficulty: "high", detail: "summary", include_best_combination: true } } };
  }

  async function findProjects() {
    setFormMessage("");
    const form = getPayload();
    if (form.error) { setFormMessage(form.error); return; }
    const button = document.getElementById("find-projects");
    button.disabled = true;
    button.textContent = "Finding projects…";
    try {
      const response = await fetch("/api/v1/repurpose", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form.payload) });
      const data = await response.json();
      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.map((entry) => entry.msg).join(" ") : data.detail;
        throw new Error(detail || "The request could not be processed.");
      }
      appState.latestPayload = form.payload;
      renderResults(data);
      document.getElementById("results").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      setFormMessage(error.message || "Could not reach the project finder.");
    } finally {
      button.disabled = false;
      button.innerHTML = 'Find projects <span aria-hidden="true">→</span>';
    }
  }

  function displayName(id) { return vocabById.get(`component:${id}`)?.name || vocabById.get(`device:${id}`)?.name || slugLabel(id); }
  function tierLabel(tier) { return ({ ready: "Ready", almost: "Almost ready", partial: "Partial", not_viable: "Not viable", unscored: "Browse" })[tier] || tier; }
  function requirementIcon(status) { return ({ have: "✓", substitute: "↗", missing: "○", defective: "!", unscored: "—" })[status] || "•"; }
  function requirementWord(status) { return ({ have: "Have", substitute: "Substitute", missing: "Missing", defective: "Defective", unscored: "Unscored" })[status] || status; }

  function renderResults(data) {
    const results = document.getElementById("results");
    results.hidden = false;
    document.getElementById("catalog-version").textContent = `Catalog ${data.meta.catalog_version}`;
    const safety = (data.safety_banners || []).map((text) => `<div class="safety-banner" role="alert"><strong>Safety notice:</strong> ${esc(text)}</div>`);
    const defectPrompts = (data.resolved_inventory || []).filter((item) => item.prompt_user_to_specify_defects).map((item) => {
      const suggestions = (item.defect_suggestions || []).map(displayName).map(esc).join(", ");
      return `<div class="safety-banner"><strong>Parts need testing:</strong> ${esc(item.input)} was marked defective without listing failed parts.${suggestions ? ` Common failure points to check: ${suggestions}.` : ""}</div>`;
    });
    document.getElementById("safety-banners").innerHTML = [...safety, ...defectPrompts].join("");
    renderSummary(data);
    renderUnknown(data);
    const container = document.getElementById("project-list");
    if (!data.recommendations?.length) {
      container.innerHTML = `<div class="no-projects"><h3>No projects meet this score threshold yet</h3><p>Try adding more parts, marking uncertain components as “Unknown”, or lowering the minimum score in an API request.</p></div>`;
      return;
    }
    container.innerHTML = data.recommendations.map(renderProject).join("");
  }

  function renderSummary(data) {
    const summary = data.summary || {};
    document.getElementById("summary-bar").innerHTML = `
      <div class="summary-item"><span class="summary-value">${summary.items_recognized ?? 0}</span><span class="summary-label">items recognised</span></div>
      <div class="summary-item"><span class="summary-value">${summary.projects_ready ?? 0} ready · ${summary.projects_almost ?? 0} almost</span><span class="summary-label">strong matches</span></div>
      <div class="summary-item"><span class="summary-value">≈ ${Number(summary.top_waste_reduction_grams || 0).toLocaleString()} g</span><span class="summary-label">estimated e-waste kept in use</span></div>`;
  }

  function renderUnknown(data) {
    const unknownSection = document.getElementById("unrecognized-section");
    const resolved = data.resolved_inventory || [];
    const entries = [
      ...(data.unrecognized || []).map((item) => ({ ...item, status: "unrecognized" })),
      ...resolved.filter((item) => item.status === "ambiguous").map((item) => ({ input: item.input, status: "ambiguous", suggestions: item.alternatives || [] })),
    ];
    if (!entries.length) { unknownSection.hidden = true; return; }
    unknownSection.hidden = false;
    document.getElementById("unrecognized-list").innerHTML = entries.map((entry, index) => `
      <div class="unrecognized-item">
        <div><p>${esc(entry.input)} <span class="type-hint">${entry.status === "ambiguous" ? "could mean more than one item" : "unrecognised or needs confirmation"}</span></p>
          <div class="alternative-chips">${(entry.suggestions || []).map((suggestion) => `<button class="button button-secondary button-small use-suggestion" type="button" data-index="${index}" data-kind="${suggestion.kind}" data-id="${esc(suggestion.matched_id)}">Use ${esc(suggestion.display_name)}</button>`).join("")}</div>
        </div>
        <div class="retry-actions"><button class="button button-secondary button-small retry-kind" type="button" data-index="${index}" data-kind="component">Try as component</button><button class="button button-secondary button-small retry-kind" type="button" data-index="${index}" data-kind="device">Try as device</button></div>
      </div>`).join("");
    const buttons = document.getElementById("unrecognized-list");
    buttons.querySelectorAll(".retry-kind").forEach((button) => button.addEventListener("click", () => retryEntry(entries[Number(button.dataset.index)], button.dataset.kind)));
    buttons.querySelectorAll(".use-suggestion").forEach((button) => button.addEventListener("click", () => {
      const item = vocabById.get(`${button.dataset.kind}:${button.dataset.id}`);
      if (item) retryEntry(entries[Number(button.dataset.index)], button.dataset.kind, item);
    }));
  }

  function retryEntry(entry, kind, selected = null) {
    const row = [...itemList.querySelectorAll(".item-row")].find((candidate) => candidate.querySelector(".item-name-input").value.trim() === entry.input);
    if (!row) return;
    if (selected) selectVocabularyItem(row, selected, true);
    else {
      row.querySelector(".item-kind-select").value = kind;
      delete row.dataset.selectedId;
      delete row.dataset.selectedKind;
      renderAutocomplete(row);
    }
    findProjects();
  }

  function renderProject(project) {
    const score = project.feasibility_score;
    const progress = Number.isFinite(score) ? Math.max(0, Math.min(100, score)) : 28;
    const ringClass = score == null ? "score-ring browse" : "score-ring";
    const scoreText = score == null ? "BROWSE" : `${Math.round(score)}%`;
    const ring = `<div class="${ringClass}" style="--progress:${progress}%" aria-label="${score == null ? "Browse project" : `${Math.round(score)} percent feasible`}"><div class="score-center"><span class="score-number">${scoreText}</span><span class="score-caption">${score == null ? "project" : "feasible"}</span></div></div>`;
    const statusIcons = (project.requirements || []).map((requirement) => {
      const supplied = (requirement.satisfied_by || []).map((part) => `${displayName(part.component_id)} ×${part.qty}${part.from_source ? ` · ${part.from_source}` : ""}${part.note ? ` — ${part.note}` : ""}`).join("<br>");
      const fill = requirement.fill == null ? "" : ` · ${Math.round(requirement.fill * 100)}%`;
      return `<div class="check-row check-${esc(requirement.status)}"><span class="check-icon" aria-hidden="true">${requirementIcon(requirement.status)}</span><div><div class="requirement-role">${esc(requirement.role)} <span class="type-hint">×${requirement.needed}</span></div><div class="requirement-sub">${requirementWord(requirement.status)}${fill}${supplied ? `<br>${supplied}` : ""}${(requirement.possible_substitutes || []).map((hint) => `<br>Possible: ${esc(hint.display_name)} — ${esc(hint.note)}`).join("")}</div></div><span class="requirement-state">${requirementWord(requirement.status)}</span></div>`;
    }).join("");
    const shopping = project.shopping_list?.length ? project.shopping_list.map((item) => `<li><span>${esc(displayName(item.component_id))} ×${item.qty}${item.alternatives?.length ? `<small class="requirement-sub">Alternatives: ${item.alternatives.map(displayName).map(esc).join(", ")}</small>` : ""}</span><span class="shopping-tier">${esc(item.tier)}</span></li>`).join("") : `<li><span>${project.tier === "unscored" ? "Add your parts to see what's needed." : "No purchase listed for filled slots."}</span></li>`;
    const build = project.build || {};
    const steps = (build.steps || []).length ? `<ol>${build.steps.map((step) => `<li><strong>${esc(step.title)}</strong>${step.instruction ? `<br>${esc(step.instruction)}` : ""}</li>`).join("")}</ol>` : "<p>No reviewed build steps are available yet.</p>";
    const prerequisites = build.prerequisites?.length ? `<ul>${build.prerequisites.map((text) => `<li>${esc(text)}</li>`).join("")}</ul>` : "";
    const sources = (build.sources || []).map((source) => `<li><a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title || source.url)}</a></li>`).join("");
    const videoRows = (build.youtube_videos || []).map((video) => typeof video === "string" ? `<li><a href="${esc(video)}" target="_blank" rel="noopener noreferrer">Video tutorial</a></li>` : (video.url ? `<li><a href="${esc(video.url)}" target="_blank" rel="noopener noreferrer">${esc(video.title || "Video tutorial")}</a></li>` : "")).join("");
    const warnings = project.warnings?.length ? `<div class="build-warnings"><strong>Safety and compatibility notes</strong><ul>${project.warnings.map((text) => `<li>${esc(text)}</li>`).join("")}</ul></div>` : "";
    const qualityReasons = project.data_quality?.review_reasons?.length ? `<ul class="quality-reasons">${project.data_quality.review_reasons.map((text) => `<li>${esc(text)}</li>`).join("")}</ul>` : "";
    const waste = Number(project.waste_reduction?.grams || 0);
    return `<article class="project-card">
      <div class="project-main">${ring}<div class="project-copy"><div class="project-title-row"><h3 class="project-title">${esc(project.name)}</h3><span class="tier-badge tier-${esc(project.tier)}">${tierLabel(project.tier)}</span>${project.data_quality?.needs_review ? `<span class="quality-chip" title="Catalog editors flagged this project for review">Needs review</span>` : ""}</div>
        <p class="project-description">${esc(project.description || "")}</p><div class="project-meta"><span>${esc(project.difficulty)} difficulty</span><span>${Number(project.estimated_build_time_hours || 0)} h estimated</span><span>${esc(project.category)}</span>${project.score_if_buying_cheap_parts != null ? `<span>${Math.round(project.score_if_buying_cheap_parts)}% if buying cheap parts</span>` : ""}</div>${qualityReasons}</div>
        <div class="waste-badge"><strong>≈ ${waste.toLocaleString()} g</strong><span>e-waste kept in use · estimate</span></div>
      </div>
      <div class="project-details"><div class="detail-grid"><section aria-label="Requirement checklist"><h4 class="detail-heading">Parts checklist</h4><div class="checklist">${statusIcons}</div></section><section aria-label="Shopping list"><h4 class="detail-heading">Shopping list</h4><ul class="shopping-list">${shopping}</ul></section></div>
        <details class="build-disclosure"><summary>Build guidance, sources and videos</summary><div class="build-content">${prerequisites}${steps}${sources || videoRows ? `<h4>Sources and videos</h4><ul>${sources}${videoRows}</ul>` : ""}${warnings}</div></details>
      </div>
    </article>`;
  }

  document.getElementById("add-item").addEventListener("click", () => addItemRow());
  document.getElementById("find-projects").addEventListener("click", findProjects);
  updateEmptyHint();
  loadVocabulary();
})();
