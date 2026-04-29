/**
 * query_builder.js
 *
 * Progressively enhances a hidden <textarea class="query-builder-source">
 * that holds a PersonFilter criteria JSON object.
 *
 * Field definitions and their operators are read from the textarea's
 * data-fields and data-operators attributes (JSON strings).
 *
 * On form submit the DOM tree is serialised back to JSON and written
 * into the textarea so Django's normal form handling saves it.
 */
(function () {
  "use strict";

  // ── Helpers ────────────────────────────────────────────────────────────────

  function el(tag, cls) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    return node;
  }

  function opt(value, text, selected) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    if (selected) o.selected = true;
    return o;
  }

  // ── Rule row ───────────────────────────────────────────────────────────────

  /**
   * Build a single rule row.  ``rule`` is either an existing leaf object or
   * a fresh default; it is NOT mutated — state is read entirely from the DOM
   * when serialising.
   */
  function buildRuleEl(fields, operators, rule) {
    rule = rule || {};
    const row = el("div", "qb-rule");

    // Field selector
    const fieldSel = el("select", "qb-field");
    fields.forEach(function (f) {
      fieldSel.appendChild(opt(f.id, f.label, f.id === rule.field));
    });
    if (!rule.field) fieldSel.selectedIndex = 0;

    // Operator selector — rebuilt whenever the field changes
    const opSel = el("select", "qb-operator");

    // Value input
    const valueInput = el("input", "qb-value");
    valueInput.type = "text";
    if (rule.value !== undefined && rule.value !== null) {
      valueInput.value = String(rule.value);
    }

    function refreshOperators() {
      const fieldId = fieldSel.value;
      const fieldDef = fields.find(function (f) { return f.id === fieldId; });
      const fieldType = fieldDef ? fieldDef.type : "text";
      const ops = operators[fieldType] || operators["text"] || [];

      opSel.innerHTML = "";
      ops.forEach(function (op) {
        opSel.appendChild(opt(op.id, op.label, op.id === rule.operator));
      });
      if (opSel.selectedIndex === -1 && ops.length) opSel.selectedIndex = 0;
      refreshValueVisibility();
    }

    function refreshValueVisibility() {
      const fieldId = fieldSel.value;
      const fieldDef = fields.find(function (f) { return f.id === fieldId; });
      const fieldType = fieldDef ? fieldDef.type : "text";
      const ops = operators[fieldType] || operators["text"] || [];
      const selOp = ops.find(function (op) { return op.id === opSel.value; });
      valueInput.style.display = (selOp && !selOp.has_value) ? "none" : "";
    }

    refreshOperators();

    fieldSel.addEventListener("change", refreshOperators);
    opSel.addEventListener("change", refreshValueVisibility);

    // Remove button
    const removeBtn = el("button", "qb-btn qb-remove");
    removeBtn.type = "button";
    removeBtn.title = "Remove condition";
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", function () { row.remove(); });

    row.appendChild(fieldSel);
    row.appendChild(opSel);
    row.appendChild(valueInput);
    row.appendChild(removeBtn);
    return row;
  }

  // ── Group ──────────────────────────────────────────────────────────────────

  function buildGroupEl(fields, operators, node, isRoot) {
    node = node || {};
    const group = el("div", isRoot ? "qb-group qb-root" : "qb-group qb-nested");

    // Header: "Match [all|any] of the following:"
    const header = el("div", "qb-group-header");
    header.appendChild(document.createTextNode("Match "));

    const logicSel = el("select", "qb-logic");
    logicSel.appendChild(opt("AND", "all", node.logic !== "OR"));
    logicSel.appendChild(opt("OR", "any", node.logic === "OR"));
    header.appendChild(logicSel);
    header.appendChild(document.createTextNode(" of the following:"));
    group.appendChild(header);

    // Rules container
    const rulesContainer = el("div", "qb-rules");
    (node.rules || []).forEach(function (rule) {
      if ("field" in rule) {
        rulesContainer.appendChild(buildRuleEl(fields, operators, rule));
      } else {
        rulesContainer.appendChild(buildGroupEl(fields, operators, rule, false));
      }
    });
    group.appendChild(rulesContainer);

    // Footer actions
    const footer = el("div", "qb-footer");

    const addRuleBtn = el("button", "qb-btn qb-add-rule");
    addRuleBtn.type = "button";
    addRuleBtn.textContent = "+ Add condition";
    addRuleBtn.addEventListener("click", function () {
      rulesContainer.appendChild(buildRuleEl(fields, operators, {}));
    });
    footer.appendChild(addRuleBtn);

    const addGroupBtn = el("button", "qb-btn qb-add-group");
    addGroupBtn.type = "button";
    addGroupBtn.textContent = "+ Add group";
    addGroupBtn.addEventListener("click", function () {
      rulesContainer.appendChild(buildGroupEl(fields, operators, {logic: "AND", rules: []}, false));
    });
    footer.appendChild(addGroupBtn);

    if (!isRoot) {
      const removeGroupBtn = el("button", "qb-btn qb-remove-group");
      removeGroupBtn.type = "button";
      removeGroupBtn.textContent = "× Remove group";
      removeGroupBtn.addEventListener("click", function () { group.remove(); });
      footer.appendChild(removeGroupBtn);
    }

    group.appendChild(footer);
    return group;
  }

  // ── Serialisation ──────────────────────────────────────────────────────────

  function serializeGroup(groupEl) {
    const logicSel = groupEl.querySelector(":scope > .qb-group-header .qb-logic");
    const node = {
      logic: logicSel ? logicSel.value : "AND",
      rules: [],
    };
    const rulesContainer = groupEl.querySelector(":scope > .qb-rules");
    if (!rulesContainer) return node;

    Array.from(rulesContainer.children).forEach(function (child) {
      if (child.classList.contains("qb-rule")) {
        const fieldSel = child.querySelector(".qb-field");
        const opSel = child.querySelector(".qb-operator");
        const valueInput = child.querySelector(".qb-value");
        if (!fieldSel || !opSel) return;
        const rule = {field: fieldSel.value, operator: opSel.value};
        if (valueInput && valueInput.style.display !== "none") {
          rule.value = valueInput.value;
        }
        node.rules.push(rule);
      } else if (child.classList.contains("qb-group")) {
        node.rules.push(serializeGroup(child));
      }
    });
    return node;
  }

  // ── Bootstrap ──────────────────────────────────────────────────────────────

  function init() {
    document.querySelectorAll("textarea.query-builder-source").forEach(function (textarea) {
      let criteria;
      try {
        criteria = JSON.parse(textarea.value || "{}");
      } catch (_) {
        criteria = {};
      }
      if (!criteria.logic) criteria.logic = "AND";
      if (!criteria.rules) criteria.rules = [];

      let fields, operators;
      try {
        fields = JSON.parse(textarea.dataset.fields || "[]");
        operators = JSON.parse(textarea.dataset.operators || "{}");
      } catch (_) {
        fields = [];
        operators = {};
      }

      const rootEl = buildGroupEl(fields, operators, criteria, true);
      textarea.parentNode.insertBefore(rootEl, textarea);

      const form = textarea.closest("form");
      if (form) {
        form.addEventListener("submit", function () {
          textarea.value = JSON.stringify(serializeGroup(rootEl));
        });
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
