/**
 * query_builder.js
 *
 * Progressively enhances a hidden <textarea class="query-builder-source">
 * that holds a PersonFilter criteria JSON object.
 *
 * Field definitions and their operators are read from the textarea's
 * data-fields and data-operators attributes (JSON strings).
 * Available nested filters are read from data-people-filters, and available
 * membership types from data-membership-types.
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

  // The operator of a membership condition which also needs a start time, as
  // MEMBERSHIP_SINCE_OPERATOR in underground_crm/models/filter.py.
  const MEMBERSHIP_SINCE_OPERATOR = "since";
  // The operators of a membership condition which also need a start date, as the
  // keys of MEMBERSHIP_START_DATE_LOOKUPS in underground_crm/models/filter.py.
  const MEMBERSHIP_START_DATE_OPERATORS = ["started_before", "started_by"];
  // The operator of an interaction condition which also needs an end date, as
  // INTERACTION_BETWEEN_OPERATOR in underground_crm/models/filter.py.
  const INTERACTION_BETWEEN_OPERATOR = "between";

  // ── Rule row ───────────────────────────────────────────────────────────────

  /**
   * Build a single rule row.  ``rule`` is either an existing leaf object or
   * a fresh default; it is NOT mutated — state is read entirely from the DOM
   * when serialising.
   */
  function buildRuleEl(fields, operators, peopleFilters, ruleOptions, rule) {
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

    // Value input (text/integer/boolean fields)
    const valueInput = el("input", "qb-value");
    valueInput.type = "text";
    if (rule.value !== undefined && rule.value !== null) {
      valueInput.value = String(rule.value);
    }

    // Filter select (for "People filter" field type)
    const filterSelect = el("select", "qb-filter-value");
    filterSelect.style.display = "none";
    peopleFilters.forEach(function (pf) {
      filterSelect.appendChild(opt(pf.id, pf.label, pf.id === rule.value));
    });

    // Membership type select. It starts on a blank placeholder rather than on the
    // first type, so that the admin has to make a choice, and so that a rule whose
    // type has been deleted does not silently switch to some other type.
    const membershipTypeSelect = el("select", "qb-value qb-membership-type-value");
    membershipTypeSelect.style.display = "none";
    membershipTypeSelect.appendChild(opt("", ruleOptions.membershipTypePlaceholder, true));
    ruleOptions.membershipTypes.forEach(function (type) {
      membershipTypeSelect.appendChild(opt(type.id, type.label, type.id === rule.value));
    });

    // Start time for "has active membership since"
    const sinceInput = el("input", "qb-value qb-since-value");
    sinceInput.type = "datetime-local";
    sinceInput.style.display = "none";
    if (rule.since) sinceInput.value = rule.since;

    // Start date for "has active membership that started before" or "... started by"
    const startDateInput = el("input", "qb-value qb-start-date-value");
    startDateInput.type = "date";
    startDateInput.style.display = "none";
    if (rule.start_date) startDateInput.value = rule.start_date;

    // End date for "had an interaction between", preceded by the word "and"
    const untilConjunction = el("span", "qb-conjunction");
    untilConjunction.textContent = ruleOptions.periodConjunction;
    untilConjunction.style.display = "none";
    const untilInput = el("input", "qb-value qb-until-value");
    untilInput.type = "date";
    untilInput.style.display = "none";
    if (rule.until) untilInput.value = rule.until;

    function getFieldType() {
      const fieldDef = fields.find(function (f) { return f.id === fieldSel.value; });
      return fieldDef ? fieldDef.type : "text";
    }

    function refreshOperators() {
      const fieldType = getFieldType();
      const ops = operators[fieldType] || operators["text"] || [];

      opSel.innerHTML = "";
      ops.forEach(function (op) {
        opSel.appendChild(opt(op.id, op.label, op.id === rule.operator));
      });
      if (opSel.selectedIndex === -1 && ops.length) opSel.selectedIndex = 0;
      refreshValueVisibility();
    }

    function refreshValueVisibility() {
      const fieldType = getFieldType();
      const isMembership = fieldType === "membership";
      membershipTypeSelect.style.display = isMembership ? "" : "none";
      sinceInput.style.display =
        (isMembership && opSel.value === MEMBERSHIP_SINCE_OPERATOR) ? "" : "none";
      startDateInput.style.display =
        (isMembership && MEMBERSHIP_START_DATE_OPERATORS.includes(opSel.value)) ? "" : "none";
      const needsUntil =
        fieldType === "interaction" && opSel.value === INTERACTION_BETWEEN_OPERATOR;
      untilConjunction.style.display = needsUntil ? "" : "none";
      untilInput.style.display = needsUntil ? "" : "none";
      if (isMembership) {
        valueInput.style.display = "none";
        filterSelect.style.display = "none";
      } else if (fieldType === "filter") {
        valueInput.style.display = "none";
        filterSelect.style.display = "";
      } else {
        const ops = operators[fieldType] || operators["text"] || [];
        const selOp = ops.find(function (op) { return op.id === opSel.value; });
        // Date fields get the browser's date picker, which submits YYYY-MM-DD.
        valueInput.type =
          (fieldType === "date" || fieldType === "interaction") ? "date" : "text";
        valueInput.style.display = (selOp && !selOp.has_value) ? "none" : "";
        filterSelect.style.display = "none";
      }
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
    row.appendChild(filterSelect);
    row.appendChild(membershipTypeSelect);
    row.appendChild(sinceInput);
    row.appendChild(startDateInput);
    row.appendChild(untilConjunction);
    row.appendChild(untilInput);
    row.appendChild(removeBtn);
    return row;
  }

  // ── Group ──────────────────────────────────────────────────────────────────

  function buildGroupEl(fields, operators, peopleFilters, ruleOptions, node, isRoot) {
    node = node || {};
    const group = el("div", isRoot ? "qb-group qb-root" : "qb-group qb-nested");

    // Header: "Match [all|any|none] of the following:"
    const header = el("div", "qb-group-header");
    header.appendChild(document.createTextNode("Match "));

    const logicSel = el("select", "qb-logic");
    const logic = node.logic || "AND";
    logicSel.appendChild(opt("AND", "all", logic === "AND"));
    logicSel.appendChild(opt("OR", "any", logic === "OR"));
    logicSel.appendChild(opt("NONE", "none", logic === "NONE"));
    header.appendChild(logicSel);
    header.appendChild(document.createTextNode(" of the following:"));
    group.appendChild(header);

    // Rules container
    const rulesContainer = el("div", "qb-rules");
    (node.rules || []).forEach(function (rule) {
      if ("field" in rule) {
        rulesContainer.appendChild(buildRuleEl(fields, operators, peopleFilters, ruleOptions, rule));
      } else {
        rulesContainer.appendChild(buildGroupEl(fields, operators, peopleFilters, ruleOptions, rule, false));
      }
    });
    group.appendChild(rulesContainer);

    // Footer actions
    const footer = el("div", "qb-footer");

    const addRuleBtn = el("button", "qb-btn qb-add-rule");
    addRuleBtn.type = "button";
    addRuleBtn.textContent = "+ Add condition";
    addRuleBtn.addEventListener("click", function () {
      rulesContainer.appendChild(buildRuleEl(fields, operators, peopleFilters, ruleOptions, {}));
    });
    footer.appendChild(addRuleBtn);

    const addGroupBtn = el("button", "qb-btn qb-add-group");
    addGroupBtn.type = "button";
    addGroupBtn.textContent = "+ Add group";
    addGroupBtn.addEventListener("click", function () {
      rulesContainer.appendChild(buildGroupEl(fields, operators, peopleFilters, ruleOptions, {logic: "AND", rules: []}, false));
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
        // The start time, start date and end date inputs also carry .qb-value for
        // their styling, so this has to exclude them to find the row's ordinary value
        // input.
        const valueInput = child.querySelector(
          "input.qb-value:not(.qb-since-value):not(.qb-start-date-value):not(.qb-until-value)"
        );
        const untilInput = child.querySelector(".qb-until-value");
        const membershipTypeSelect = child.querySelector(".qb-membership-type-value");
        const sinceInput = child.querySelector(".qb-since-value");
        const startDateInput = child.querySelector(".qb-start-date-value");
        const filterSelect = child.querySelector(".qb-filter-value");
        if (!fieldSel || !opSel) return;
        const rule = {field: fieldSel.value, operator: opSel.value};
        if (membershipTypeSelect && membershipTypeSelect.style.display !== "none") {
          rule.value = membershipTypeSelect.value;
          if (sinceInput && sinceInput.style.display !== "none") {
            rule.since = sinceInput.value;
          }
          if (startDateInput && startDateInput.style.display !== "none") {
            rule.start_date = startDateInput.value;
          }
        } else if (filterSelect && filterSelect.style.display !== "none") {
          rule.value = filterSelect.value;
        } else if (valueInput && valueInput.style.display !== "none") {
          rule.value = valueInput.value;
          if (untilInput && untilInput.style.display !== "none") {
            rule.until = untilInput.value;
          }
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

      let fields, operators, peopleFilters, membershipTypes;
      try {
        fields = JSON.parse(textarea.dataset.fields || "[]");
        operators = JSON.parse(textarea.dataset.operators || "{}");
        peopleFilters = JSON.parse(textarea.dataset.peopleFilters || "[]");
        membershipTypes = JSON.parse(textarea.dataset.membershipTypes || "[]");
      } catch (_) {
        fields = [];
        operators = {};
        peopleFilters = [];
        membershipTypes = [];
      }
      // What the rows need beyond the fields and operators: the membership types to
      // choose from, and the text shown in the controls of the special conditions.
      const ruleOptions = {
        membershipTypes: membershipTypes,
        membershipTypePlaceholder: textarea.dataset.membershipTypePlaceholder || "",
        periodConjunction: textarea.dataset.periodConjunction || "and",
      };

      const rootEl = buildGroupEl(fields, operators, peopleFilters, ruleOptions, criteria, true);
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
