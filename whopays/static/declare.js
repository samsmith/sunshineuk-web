/* =========================================================
   Sunshine UK: declaration form (templates/declare.html)

   Section 1 posts with the Doctor form's own field names.
   Section 2 is built here, one declaration at a time, and
   goes up on submit in two new fields:

     declaration          the declarations as markdown
     declaration_records  the same declarations as JSON; the
                          page also uses this to put them back
                          if the server returns the form
   ========================================================= */
(function () {
  "use strict";

  const BANDS = [
    "under £100",
    "£100 – £1,000",
    "£1,000 – £2,000",
    "£2,000 – £5,000",
    "£5,000 – £10,000",
    "£10,000 – £50,000",
    "£50,000 – £100,000",
    "£100,000+"
  ];

  // Categories and sub-options from the DetailedDeclaration model;
  // "Main employment" is new.
  const CATEGORIES = {
    // Listed first. One post per record, so the type is a single
    // choice, and there's no income band.
    main_employment: {
      label: "Main employment",
      note: "NHS, academic or private post",
      kind: "employment",
      placeholder: "Job title / institution",
      subs: [
        { id: "nhs",      label: "NHS" },
        { id: "academic", label: "Academic" },
        { id: "private",  label: "Private work" }
      ]
    },
    consultancy: {
      label: "Consultancy",
      note: "Advisory work for companies",
      subs: [
        { id: "pharmaceutical_companies", label: "Pharmaceutical companies" },
        { id: "technology_companies",     label: "Technology companies" },
        { id: "consultancy_other",        label: "Other" }
      ]
    },
    academic: {
      label: "Academic",
      note: "Grants and academic relationships",
      subs: [
        { id: "research_grants", label: "Research grants" },
        { id: "academic_other",  label: "Other" }
      ]
    },
    other_work: {
      label: "Other work",
      note: "PR, commercial, media",
      subs: [
        { id: "public_relations",         label: "Public relations" },
        { id: "commercial_relationships", label: "Commercial relationships" },
        { id: "media",                    label: "Media" }
      ]
    },
    financial: {
      label: "Financial",
      note: "Patents, shares, ownership",
      subs: [
        { id: "patents_owned", label: "Patents owned / part-owned" },
        { id: "shares",        label: "Shares, stocks or company ownership" }
      ]
    },
    spousal: {
      label: "Spousal / family",
      note: "Interests held by close family",
      subs: []
    },
    sponsored: {
      label: "Sponsored / educational events",
      note: "Conferences, hospitality, travel",
      subs: [
        { id: "conferences", label: "Conferences" },
        { id: "meals",       label: "Meals / hospitality / travel" }
      ]
    },
    political: {
      label: "Political / membership organisations",
      note: "Affiliations of note",
      subs: []
    }
  };

  const state = {
    identity: null,          // {name, job_title, primary_employer, gmc_number}
    records: [],             // interest:   {category, category_label, subs:{}, band, details}
                             // employment: {category, category_label, employment_type, details}
    saidNothing: false,      // the "I have nothing to declare" button was used
    currentCategory: null
  };

  /* ---------- helpers ---------- */
  const byId = (id) => document.getElementById(id);
  const show = (el) => el.classList.remove("hidden");
  const hide = (el) => el.classList.add("hidden");
  const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
  const isEmployment = (rec) => CATEGORIES[rec.category].kind === "employment";

  function esc(s){
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  /* Rolling 12 months: from the start of this month a year ago, to today. */
  function declarationPeriod(){
    const end = new Date();
    const start = new Date(end.getFullYear() - 1, end.getMonth(), 1);
    return { start, end };
  }

  /* ---------- category buttons and band options ---------- */
  Object.entries(CATEGORIES).forEach(([key, cat]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-default";
    btn.innerHTML = `${esc(cat.label)}<span class="cat-note">${esc(cat.note)}</span>`;
    btn.addEventListener("click", () => openCategoryForm(key));
    byId("cat-grid").appendChild(btn);
  });

  [["", "— select a band —"]].concat(BANDS.map(b => [b, b])).forEach(([value, text]) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = text;
    byId("cat-band").appendChild(opt);
  });

  /* =========================================================
     SECTION 1 → SECTION 2
     ========================================================= */
  function identityDone(){
    const value = (id) => byId(id).value.trim();
    const identity = {
      name: value("id_name"),
      job_title: value("id_job_title"),
      primary_employer: value("id_primary_employer"),
      gmc_number: value("id_gmc_number")
    };
    if (Object.values(identity).some(v => !v)) {
      show(byId("identity-error"));
      return;
    }
    hide(byId("identity-error"));
    state.identity = identity;

    const p = declarationPeriod();
    const month = (d) => d.toLocaleDateString("en-GB", { month: "long", year: "numeric" });
    byId("identity-summary").innerHTML =
      `Declaring as <strong>${esc(identity.name)}</strong>, ${esc(identity.job_title)} at ${esc(identity.primary_employer)} &middot; identifier <strong>${esc(identity.gmc_number)}</strong><br>
       Covering the period <strong>${month(p.start)}</strong> to <strong>${month(p.end)}</strong>`;

    hide(byId("screen-identity"));
    show(byId("screen-main"));
    window.scrollTo({ top: 0 });
  }

  byId("btn-identity-done").addEventListener("click", identityDone);

  /* =========================================================
     ONE DECLARATION AT A TIME
     ========================================================= */
  function openCategoryForm(key){
    state.currentCategory = key;
    const cat = CATEGORIES[key];
    const isEmp = cat.kind === "employment";

    byId("cat-form-title").textContent = cat.label;
    byId("cat-subs-legend").textContent = isEmp ? "Type of employment" : "This relates to";

    const subs = byId("cat-subs");
    subs.innerHTML = "";
    if (cat.subs.length === 0) {
      hide(byId("cat-subs-wrap"));
    } else {
      show(byId("cat-subs-wrap"));
      cat.subs.forEach(s => {
        const div = document.createElement("div");
        div.className = isEmp ? "radio" : "checkbox";
        div.innerHTML = isEmp
          ? `<label><input type="radio" name="emp-type" value="${s.id}"> ${esc(s.label)}</label>`
          : `<label><input type="checkbox" data-sub="${s.id}"> ${esc(s.label)}</label>`;
        subs.appendChild(div);
      });
    }

    // Main employment has no income band, and its Details box carries
    // suggested text instead of the usual hint.
    if (isEmp) { hide(byId("band-field")); hide(byId("cat-details-hint")); }
    else       { show(byId("band-field")); show(byId("cat-details-hint")); }
    byId("cat-details").placeholder = cat.placeholder || "";

    byId("cat-band").value = "";
    byId("cat-details").value = "";
    hide(byId("cat-error"));

    hide(byId("picker"));
    hide(byId("after-add"));
    show(byId("cat-form"));
    byId("cat-form").scrollIntoView({ block: "start" });
  }

  byId("btn-cancel-cat").addEventListener("click", () => {
    hide(byId("cat-form"));
    show(byId("picker"));
  });

  byId("btn-add-record").addEventListener("click", () => {
    const key = state.currentCategory;
    const cat = CATEGORIES[key];
    const err = byId("cat-error");
    const details = byId("cat-details").value.trim();

    if (cat.kind === "employment") {
      const picked = document.querySelector("#cat-subs input[name=emp-type]:checked");
      if (!picked || !details) {
        err.textContent = "Choose NHS, Academic or Private work, and add your job title and institution.";
        show(err);
        return;
      }
      // Main employment isn't an interest: with no interests listed,
      // the declaration still says "Nothing to declare".
      state.records.push({
        category: key,
        category_label: cat.label,
        employment_type: picked.value,
        details: details
      });
    } else {
      const subs = {};
      document.querySelectorAll("#cat-subs input[type=checkbox]").forEach(cb => {
        subs[cb.dataset.sub] = cb.checked;
      });
      const band = byId("cat-band").value;
      if (!band && !details) {
        err.textContent = "Add an income band or some details before saving this declaration.";
        show(err);
        return;
      }
      // Declaring an interest takes "Nothing to declare" away.
      state.saidNothing = false;
      state.records.push({
        category: key,
        category_label: cat.label,
        subs: subs,
        band: band,
        details: details
      });
    }
    hide(err);

    renderLedger();
    hide(byId("cat-form"));
    show(byId("after-add"));
    byId("after-add").scrollIntoView({ block: "start" });
  });

  byId("btn-add-another").addEventListener("click", () => {
    hide(byId("after-add"));
    show(byId("picker"));
    byId("picker").scrollIntoView({ block: "start" });
  });

  byId("btn-all-finished").addEventListener("click", () => {
    hide(byId("after-add"));
    show(byId("picker"));
    byId("submit-panel").scrollIntoView({ block: "start" });
  });

  byId("btn-back-to-picker").addEventListener("click", () => {
    show(byId("picker"));
    byId("picker").scrollIntoView({ block: "start" });
  });

  // Only offered while the list is empty (see renderLedger).
  byId("btn-nothing").addEventListener("click", () => {
    state.saidNothing = true;
    renderLedger();
    byId("submit-panel").scrollIntoView({ block: "start" });
  });

  /* =========================================================
     LEDGER: each record is a card with readable text, plus a
     hidden copy of its variables as JSON.
     ========================================================= */
  function renderLedger(){
    const ledger = byId("ledger");
    ledger.innerHTML = "";

    // A declaration with no interests in it says "Nothing to declare":
    // after the button, or as soon as main employment is listed. Declaring
    // an interest takes it away.
    const hasInterests = state.records.some(r => !isEmployment(r));
    const nothingToDeclare = !hasInterests && (state.saidNothing || state.records.length > 0);

    // So the button is only needed while the list is empty.
    if (state.records.length === 0 && !state.saidNothing) show(byId("btn-nothing"));
    else hide(byId("btn-nothing"));

    if (state.records.length === 0 && !nothingToDeclare) {
      ledger.innerHTML = `<p class="ledger-empty">Nothing added yet.</p>`;
      hide(byId("submit-panel"));
      return;
    }
    show(byId("submit-panel"));

    // Main employment first, then interests, each in the order added.
    state.records
      .map((rec, i) => ({ rec, i }))
      .sort((a, b) => isEmployment(b.rec) - isEmployment(a.rec))
      .forEach(({ rec, i }) => ledger.appendChild(recordDiv(rec, i)));

    if (nothingToDeclare) {
      const div = document.createElement("div");
      div.className = "record record--nothing";
      div.innerHTML =
        `<div class="record-body">${esc(nothingLine(state.records))}</div>
         <span class="record-json" hidden>${esc(JSON.stringify({ nothing_to_declare: true }))}</span>`;
      ledger.appendChild(div);
    }
  }

  /* A declaration with no interests ends with this line, on its card
     and in the markdown. */
  function nothingLine(records){
    return records.some(r => r.category && isEmployment(r))
      ? "Nothing else to declare."
      : "Nothing to declare.";
  }

  function recordDiv(rec, i){
    const { title, body } = summarise(rec);
    const div = document.createElement("div");
    div.className = isEmployment(rec) ? "record record--employment" : "record";
    div.innerHTML =
      `<span class="record-title">${esc(title)}</span>
       <div class="record-body">${esc(body)}</div>
       <span class="record-json" hidden>${esc(JSON.stringify(rec))}</span>
       <button type="button" class="close record-remove" aria-label="Remove this declaration"><span aria-hidden="true">&times;</span></button>`;
    div.querySelector(".record-remove").addEventListener("click", () => {
      state.records.splice(i, 1);
      renderLedger();
    });
    return div;
  }

  /* The records as they stand in the ledger: the page is the
     source of truth for what gets submitted. */
  function ledgerRecords(){
    return [...document.querySelectorAll("#ledger .record-json")]
      .map(span => JSON.parse(span.textContent));
  }

  /* What a record says: a title plus labelled fields. The ledger cards
     and the submitted markdown are both built from this, so they always
     say the same thing. */
  function describe(rec){
    const cat = CATEGORIES[rec.category];
    const details = rec.details.replace(/\s+/g, " ").trim();

    if (isEmployment(rec)) {
      const type = cat.subs.find(s => s.id === rec.employment_type);
      return { title: rec.category_label, fields: [["Type", type.label], ["Details", details]] };
    }

    const chosenSubs = cat.subs.filter(s => rec.subs[s.id]).map(s => s.label);
    const fields = [];
    if (chosenSubs.length) fields.push(["Relates to", chosenSubs.join(", ")]);
    if (rec.band)          fields.push(["Income band", rec.band]);
    if (details)           fields.push(["Details", details]);
    return { title: rec.category_label, fields };
  }

  /* The one-line version shown on a ledger card. */
  function summarise(rec){
    const d = describe(rec);
    const quoteDetails = !isEmployment(rec);
    const body = d.fields
      .map(([label, value]) => label === "Details" && quoteDetails ? "“" + value + "”" : value)
      .join(" · ");
    return { title: d.title, body: body || "Declared." };
  }

  /* =========================================================
     MARKDOWN

       ## Declaration on 1 November 2026

       ### Consultancy

       - **Relates to:** Pharmaceutical companies
       - **Income band:** £5,000 – £10,000
       - **Details:** Advisory board member

     With no interests, it ends with a plain line instead:
     "Nothing to declare." ("Nothing else to declare." after
     main employment).

     "##" rather than "#" so it sits under the doctor's name on
     their register page.
     ========================================================= */

  /* Escape what doctors type, so it shows exactly as typed and can't
     add formatting, links or HTML. Works with the site's renderer
     (django-markdown-deux) and with CommonMark ones (markdown-it,
     marked). */
  function mdEscape(s){
    return s
      .replace(/[\\`*_\[\]<]/g, "\\$&")        // emphasis, code, links, HTML
      .replace(/&(?=#?[a-z0-9]+;)/gi, "\\&")   // "&copy;" stays literal; "R&D" is untouched
      .replace(/~/g, "&#126;");                // strikethrough; markdown-deux has no "\~"
  }

  function buildDeclarationMarkdown(date = new Date()){
    const heading = "Declaration on " +
      date.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });

    const records = ledgerRecords();
    const blocks = ["## " + heading];
    records.forEach(rec => {
      if (rec.nothing_to_declare) {
        blocks.push(nothingLine(records));
        return;
      }
      const s = describe(rec);
      blocks.push("### " + mdEscape(s.title));
      blocks.push(s.fields.map(([label, value]) => `- **${label}:** ${mdEscape(value)}`).join("\n"));
    });
    return blocks.join("\n\n");
  }

  /* =========================================================
     SUBMIT: a normal form post. Section 1 goes up in its own
     inputs; section 2 is written into the two hidden fields.
     ========================================================= */
  const submitButton = byId("btn-submit");

  byId("declare-form").addEventListener("submit", (event) => {
    // Pressing Enter in a section 1 box submits the form; treat it as "Done".
    if (!state.identity || byId("screen-main").classList.contains("hidden")) {
      event.preventDefault();
      identityDone();
      return;
    }
    if (state.records.length === 0 && !state.saidNothing) {
      event.preventDefault();
      return;
    }

    byId("id_declaration").value = buildDeclarationMarkdown();
    byId("id_declaration_records").value = JSON.stringify(ledgerRecords());

    // Stop a double click sending it twice (after the post has started).
    setTimeout(() => {
      submitButton.disabled = true;
      submitButton.textContent = "Submitting…";
    }, 0);
  });

  // Coming back with the browser's Back button: make the button usable again.
  window.addEventListener("pageshow", () => {
    submitButton.disabled = false;
    submitButton.textContent = "Make my declaration";
  });

  /* =========================================================
     If the server sends the form back (say a section 1 error),
     put the posted declarations back in the ledger. They're
     checked against CATEGORIES because they came from the request.
     ========================================================= */
  function cleanRecord(item){
    if (!item || typeof item !== "object" || typeof item.details !== "string") return null;
    if (typeof item.category !== "string" || !has(CATEGORIES, item.category)) return null;
    const cat = CATEGORIES[item.category];

    if (cat.kind === "employment") {
      if (!cat.subs.some(s => s.id === item.employment_type) || !item.details.trim()) return null;
      return {
        category: item.category,
        category_label: cat.label,
        employment_type: item.employment_type,
        details: item.details
      };
    }

    const band = BANDS.includes(item.band) ? item.band : "";
    if (!band && !item.details.trim()) return null;
    const subs = {};
    cat.subs.forEach(s => { subs[s.id] = !!(item.subs && item.subs[s.id] === true); });
    return { category: item.category, category_label: cat.label, subs, band, details: item.details };
  }

  (function restorePostedRecords(){
    const el = byId("posted-declaration-records");
    if (!el) return;
    let items;
    try {
      const posted = JSON.parse(el.textContent);   // json_script wraps the posted string
      items = posted ? JSON.parse(posted) : [];
    } catch (e) {
      return;
    }
    if (!Array.isArray(items)) return;

    let saidNothing = false;
    items.forEach(item => {
      if (item && item.nothing_to_declare === true) {
        saidNothing = true;
        return;
      }
      const rec = cleanRecord(item);
      if (rec) state.records.push(rec);
    });
    // With main employment listed, "Nothing to declare" follows by itself.
    state.saidNothing = saidNothing && state.records.length === 0;
    renderLedger();
  })();
})();
