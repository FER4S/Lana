/* ─────────────────────────────────────────────────────────────────────────
   Lana dashboard.

   A pure observer. The only requests it makes that change anything are
   /start, /stop and /email/pending-contact — all of which already existed for
   the frontend this replaces. It cannot start a plan, edit one, or send
   anything: the treatment-plan flow is a self-contained voice sub-dialogue
   with exactly one entry point, and this is not it.

   Nothing derived from a model — a transcript line, a rule quotation, a
   referral reason — is ever assigned with innerHTML. Cards are built with
   textContent; the saved document goes through LanaMarkdown, which escapes
   before it marks up.
   ───────────────────────────────────────────────────────────────────────── */

(function () {
  "use strict";

  /* ── Token ────────────────────────────────────────────────────────────
     From ?token= on first load, then sessionStorage. The query string is
     stripped from the address bar immediately afterwards — the token would
     otherwise sit in shot for the whole recording. */

  const STORE_KEY = "lana.token";
  let token = sessionStorage.getItem(STORE_KEY) || "";

  (function adoptTokenFromUrl() {
    const fromUrl = new URLSearchParams(location.search).get("token");
    if (!fromUrl) return;
    token = fromUrl;
    sessionStorage.setItem(STORE_KEY, token);
    history.replaceState(null, "", location.pathname);
  })();

  /* ── Tiny DOM helpers ─────────────────────────────────────────────────── */

  const $ = function (id) { return document.getElementById(id); };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function plural(n, one, many) { return n === 1 ? one : many; }

  function timeOf(date) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function whenLabel(iso) {
    if (!iso) return "";
    const then = new Date(iso);
    if (isNaN(then)) return "";
    const now = new Date();
    const sameDay = then.toDateString() === now.toDateString();
    if (sameDay) return timeOf(then);
    return then.toLocaleDateString([], { day: "numeric", month: "short" }) +
           ", " + timeOf(then);
  }

  /* ── API ──────────────────────────────────────────────────────────────── */

  function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    opts.headers = Object.assign({ Authorization: "Bearer " + token }, opts.headers);
    return fetch(path, opts);
  }

  function apiJson(path) {
    return api(path).then(function (res) {
      if (!res.ok) throw new Error(path + " → " + res.status);
      return res.json();
    });
  }

  /* ── State ────────────────────────────────────────────────────────────── */

  const STAGE_STEPS = [
    { key: "dictation", label: "Dictating the case" },
    { key: "screening", label: "Screening against referral criteria" },
    { key: "drafting",  label: "Drafting the plan" },
    { key: "checking",  label: "Contraindication check" },
    { key: "review",    label: "Your review" },
    { key: "saved",     label: "Saved" }
  ];

  // Which rail step a plan_stage maps to. `revising` deliberately shares the
  // drafting step rather than adding a seventh: it is the same work happening
  // again, and a rail that grows a step mid-run is hard to read on video.
  const STAGE_TO_STEP = {
    dictating: 0, screening: 1, drafting: 2,
    revising: 2, checking: 3, confirming: 4
  };

  const STAGE_HINT = {
    dictating:  "Dictating a patient case.",
    screening:  "Checking the case against her referral criteria.",
    drafting:   "Drafting the plan from her documents.",
    revising:   "Reworking the draft.",
    checking:   "Re-checking the draft for contraindications.",
    confirming: "Waiting for your decision."
  };

  const plan = {
    active: false, stage: null, stepIndex: -1,
    // Which rail steps actually happened. Not derivable from stepIndex: the
    // referral-memo path reaches "your review" and "saved" without ever
    // drafting or checking, and a rail that ticked those would be claiming
    // two safety stages ran when they did not.
    visited: new Set(),
    screening: null, drafted: null, checked: null, saved: null,
    doc: null, docError: null, ended: null
  };

  let assistantState = "idle";
  let lastEventAt = 0;
  let dictating = false;
  let dictationNode = null;
  let dictationSegments = [];

  /* ── Presence + chrome ────────────────────────────────────────────────── */

  const STATE_LABELS = {
    idle: "Idle", listening: "Listening",
    thinking: "Thinking", speaking: "Speaking"
  };

  function setState(next) {
    if (!STATE_LABELS[next]) return;
    assistantState = next;
    lastEventAt = Date.now();

    $("state-pill").className = "pill pill-" + next;
    $("state-label").textContent = STATE_LABELS[next];
    $("orb").className = "orb orb-" + next;
    $("presence-label").textContent = STATE_LABELS[next];

    let hint;
    if (plan.active && plan.stage && STAGE_HINT[plan.stage]) {
      hint = STAGE_HINT[plan.stage];
    } else if (next === "listening") {
      hint = "Go ahead — she's recording.";
    } else if (next === "thinking") {
      hint = "Working on it.";
    } else if (next === "speaking") {
      hint = "";
    } else {
      hint = "Say “Hey Lana” to begin.";
    }
    $("presence-hint").textContent = hint;
  }

  function setSocket(text, good) {
    $("socket-value").textContent = text;
    $("socket-chip").className = "chip" + (good ? " is-good" : " is-stale");
  }

  function showError(message) {
    const bar = $("error-bar");
    if (!message) { bar.hidden = true; return; }
    bar.textContent = message;
    bar.hidden = false;
  }

  /* ── Transcript ───────────────────────────────────────────────────────── */

  function addTurn(who, text, className) {
    const transcript = $("transcript");
    const placeholder = transcript.querySelector(".empty-state");
    if (placeholder) placeholder.remove();

    const turn = el("div", "turn " + (className || ""));
    const label = el("div", "turn-who", who);
    const body = el("div");
    body.append(el("div", "turn-text", text));
    body.append(el("div", "turn-time", timeOf(new Date())));
    turn.append(label, body);
    transcript.append(turn);
    transcript.scrollTop = transcript.scrollHeight;
    return turn;
  }

  function startDictation() {
    dictating = true;
    dictationSegments = [];
    dictationNode = null;
  }

  function addDictationSegment(text) {
    const transcript = $("transcript");
    const placeholder = transcript.querySelector(".empty-state");
    if (placeholder) placeholder.remove();

    dictationSegments.push(text);

    if (!dictationNode) {
      const wrap = el("div", "turn");
      const block = el("div", "dictation is-live");
      const head = el("div", "dictation-head");
      head.append(el("span", null, "Dictated case"));
      head.append(el("span", "dictation-count", ""));
      block.append(head);
      block.append(el("div", "dictation-text", ""));
      wrap.append(block);
      transcript.append(wrap);
      dictationNode = block;
    }

    dictationNode.querySelector(".dictation-text").textContent =
      dictationSegments.join(" ");
    dictationNode.querySelector(".dictation-count").textContent =
      dictationSegments.length + " " +
      plural(dictationSegments.length, "segment", "segments");
    transcript.scrollTop = transcript.scrollHeight;
  }

  function endDictation() {
    dictating = false;
    if (dictationNode) dictationNode.classList.remove("is-live");
    dictationNode = null;
  }

  /* ── Plan panel ───────────────────────────────────────────────────────── */

  function resetPlan() {
    plan.active = false; plan.stage = null; plan.stepIndex = -1;
    plan.visited = new Set();
    plan.screening = null; plan.drafted = null; plan.checked = null;
    plan.saved = null; plan.doc = null; plan.docError = null; plan.ended = null;
  }

  function showPlanPanel(on) {
    $("plan-live").hidden = !on;
    $("plan-empty").hidden = on;
  }

  function renderStageRail() {
    const rail = $("stage-rail");
    clear(rail);

    const furthest = plan.visited.size ? Math.max.apply(null, [...plan.visited]) : -1;

    STAGE_STEPS.forEach(function (step, index) {
      const visited = plan.visited.has(index);
      const active = visited && !plan.ended && index === plan.stepIndex;
      const done = visited && !active;
      // Passed over, not pending: the run moved beyond this step without it.
      const skipped = !visited && index < furthest;

      const li = el("li", "stage" + (done ? " stage-done" : "") +
                          (active ? " stage-active" : "") +
                          (skipped ? " stage-skipped" : ""));
      li.append(el("span", "stage-icon", done ? "✓" : (skipped ? "–" : "")));
      li.append(el("span", null, step.label));

      let note = "";
      if (skipped) note = "not run";
      else if (active && plan.stage === "revising") note = "revising";
      else if (step.key === "saved" && plan.saved) {
        note = plan.saved.kind === "referral_memo" ? "referral note" : "draft";
      }
      if (note) li.append(el("span", "stage-note", note));

      rail.append(li);
    });
  }

  function card(title, tone, countLabel) {
    const wrap = el("section", "pcard" + (tone ? " pcard-" + tone : ""));
    const head = el("div", "pcard-head");
    head.append(el("h3", "pcard-title", title));
    if (countLabel) head.append(el("span", "count-tag", countLabel));
    wrap.append(head);
    const body = el("div", "pcard-body");
    wrap.append(body);
    return { wrap: wrap, body: body };
  }

  /* One referral flag: what matched, then the criterion in her own words.
     The verbatim quote is the point — without it this is just model output. */
  function renderFlag(flag) {
    const node = el("div", "flag" + (flag.confidence === "possible" ? " flag-possible" : ""));

    const top = el("div", "flag-top");
    top.append(el("span", "flag-id", flag.rule_id || "—"));

    const isTest = flag.kind && flag.kind !== "refer_out";
    const action = (isTest ? "Test first" : "Refer out") +
                   (flag.refer_to ? " — " + flag.refer_to : "");
    top.append(el("span", "flag-action", action));
    top.append(el("span", "confidence-tag confidence-" +
      (flag.confidence === "possible" ? "possible" : "confident"),
      flag.confidence === "possible" ? "possible" : "matched"));
    node.append(top);

    if (flag.matched_because) node.append(el("p", "flag-why", flag.matched_because));

    if (flag.verbatim) {
      const quote = el("blockquote", "flag-verbatim", flag.verbatim);
      quote.append(el("cite", "flag-source", "from her documentation"));
      node.append(quote);
    }
    return node;
  }

  function renderReferralCard() {
    const flags = (plan.screening && plan.screening.referral_flags) || [];
    if (!flags.length) {
      const c = card("Referral screening", "ok");
      c.body.append(el("p", null,
        "No referral criterion matched this case."));
      c.body.append(el("p", "muted",
        "Screened against all " +
        ((plan.screening && plan.screening.applicable_rule_ids) ? "" : "") +
        "of her referral rules before any drafting began."));
      return c.wrap;
    }

    const confident = flags.filter(function (f) { return f.confidence !== "possible"; });
    const possible = flags.filter(function (f) { return f.confidence === "possible"; });

    // Red is reserved for a criterion that was actually met. A run of
    // partial matches dressed as certainties is what trains someone to skim
    // past the one that matters — the same reason the backend splits these.
    // Both halves are still always shown: this changes emphasis, not content.
    const c = confident.length
      ? card("Referral indicated", "alarm",
             confident.length + " " + plural(confident.length, "criterion", "criteria"))
      : card("Possible referral — worth your eye", "caution",
             possible.length + " partial");

    if (confident.length) {
      c.body.append(el("p", null,
        "This case matches " + confident.length + " " +
        plural(confident.length, "criterion", "criteria") +
        " in her own documentation."));
      confident.forEach(function (f) { c.body.append(renderFlag(f)); });
    } else {
      c.body.append(el("p", null,
        "Nothing squarely meets a referral criterion, but " +
        (possible.length === 1 ? "this partial match is" : "these partial matches are") +
        " close enough to surface rather than drop."));
    }

    if (possible.length) {
      // Only a heading when there is something above it to distinguish from.
      if (confident.length) c.body.append(el("h4", "subhead", "Also worth checking"));
      possible.forEach(function (f) { c.body.append(renderFlag(f)); });
    }
    return c.wrap;
  }

  function renderCheckCard() {
    const check = plan.checked;
    if (!check) return null;

    // Three genuinely different outcomes. "Did not run" must never be
    // presented as "clear" — that is the whole reason check_ran exists.
    if (!check.check_ran) {
      const c = card("Safety check did not run", "caution");
      c.body.append(el("p", null,
        "The automated contraindication check failed to complete, so this " +
        "draft has not been machine-checked against her rules. Treat it as " +
        "unchecked."));
      return c.wrap;
    }

    if (!check.violations || !check.violations.length) {
      const c = card("Contraindication check", "ok", "clear");
      c.body.append(el("p", null,
        check.auto_cleared
          ? "A violation was caught and cleared, and the replacement re-checked clean."
          : "No contraindicated items found in the drafted plan."));
      return c.wrap;
    }

    const c = card("Contraindication warnings — unresolved", "alarm",
      check.violations.length + " " +
      plural(check.violations.length, "item", "items"));
    c.body.append(el("p", null,
      "Flagged by the post-draft check and not cleared by a revision. " +
      "Review before using anything in this plan."));

    check.violations.forEach(function (v) {
      const node = el("div", "flag");
      const top = el("div", "flag-top");
      top.append(el("span", "flag-id", v.rule_id || "—"));
      top.append(el("span", "flag-action", v.item || "—"));
      if (v.phase) top.append(el("span", "confidence-tag confidence-possible", v.phase));
      node.append(top);
      if (v.explanation) node.append(el("p", "flag-why", v.explanation));
      if (v.verbatim) {
        const quote = el("blockquote", "flag-verbatim", v.verbatim);
        quote.append(el("cite", "flag-source", "from her documentation"));
        node.append(quote);
      }
      c.body.append(node);
    });
    return c.wrap;
  }

  function renderDraftCard() {
    if (!plan.drafted) return null;
    const d = plan.drafted;
    const c = card("Drafted plan", null,
      d.phase_count + " " + plural(d.phase_count, "phase", "phases"));

    if (d.total_months) {
      const kv = el("div", "kv");
      kv.append(el("span", "kv-key", "Timeline"));
      kv.append(el("span", null, "about " + d.total_months + " months"));
      c.body.append(kv);
    }

    const list = el("div", "phase-list");
    (d.phases || []).forEach(function (phase) {
      const row = el("div", "phase");
      row.append(el("span", "phase-name", phase.name));
      const bits = [];
      if (phase.duration_weeks) bits.push(phase.duration_weeks + " weeks");
      bits.push(phase.supplement_count + " " +
        plural(phase.supplement_count, "supplement", "supplements"));
      row.append(el("span", "phase-meta", bits.join(" · ")));
      list.append(row);
    });
    c.body.append(list);
    return c.wrap;
  }

  function renderGapsCard() {
    const s = plan.screening;
    if (!s) return null;
    const missing = s.missing_safety_fields || [];
    const uncovered = s.uncovered_topics || [];
    if (!missing.length && !uncovered.length) return null;

    const c = card("Flagged, not guessed at", "caution");

    if (uncovered.length) {
      c.body.append(el("h4", "subhead", "Her documents don't cover"));
      const ul = el("ul", "plain-list");
      uncovered.forEach(function (t) { ul.append(el("li", null, t)); });
      c.body.append(ul);
    }
    if (missing.length) {
      c.body.append(el("h4", "subhead", "Not confirmed during dictation"));
      const ul = el("ul", "plain-list");
      missing.forEach(function (t) { ul.append(el("li", null, t)); });
      c.body.append(ul);
      c.body.append(el("p", "muted", "Drafted cautiously around these."));
    }
    return c.wrap;
  }

  function renderDocCard() {
    if (!plan.saved) return null;

    const isMemo = plan.saved.kind === "referral_memo";
    const c = card(isMemo ? "Saved referral note" : "Saved draft", null);
    c.wrap.classList.add("pcard-doc");

    const head = el("div", "doc-head");
    head.append(el("span", "doc-file", plan.saved.filename));
    c.body.append(head);

    if (plan.saved.unsure) {
      c.body.append(el("p", "muted",
        "Saved because your answer wasn't clear — she kept it rather than " +
        "lose the dictation."));
    }

    if (plan.docError) {
      c.body.append(el("p", "muted", plan.docError));
    } else if (plan.doc == null) {
      c.body.append(el("p", "muted", "Loading document…"));
    } else {
      const scroll = el("div", "doc-scroll");
      const doc = el("div", "doc");
      doc.innerHTML = window.LanaMarkdown.render(plan.doc);  // escaped inside
      scroll.append(doc);
      c.wrap.append(scroll);
    }
    return c.wrap;
  }

  function renderPlan() {
    if (!plan.active) { showPlanPanel(false); return; }
    showPlanPanel(true);

    $("draft-banner").hidden = !(plan.drafted || plan.saved);
    renderStageRail();

    const body = $("plan-body");
    clear(body);

    if (plan.screening) body.append(renderReferralCard());
    const check = renderCheckCard();
    if (check) body.append(check);
    const draft = renderDraftCard();
    if (draft) body.append(draft);
    const gaps = renderGapsCard();
    if (gaps) body.append(gaps);
    const doc = renderDocCard();
    if (doc) body.append(doc);

    if (plan.ended && plan.ended !== "saved") {
      const messages = {
        discarded: "Draft discarded. Nothing was saved.",
        abandoned: "Dictation ended before a case was captured. Nothing was saved.",
        failed: "A stage failed, so nothing was drafted or saved.",
        unavailable: "Plan drafting is unavailable — the clinical corpus didn't load."
      };
      const c = card("Ended", null);
      c.body.append(el("p", null, messages[plan.ended] || "Ended."));
      body.append(c.wrap);
    }
  }

  function loadPlanDocument(filename) {
    api("/plans/" + encodeURIComponent(filename))
      .then(function (res) {
        if (!res.ok) throw new Error(String(res.status));
        return res.text();
      })
      .then(function (text) {
        plan.doc = text;
        renderPlan();
        const doc = document.querySelector(".pcard-doc");
        if (doc) doc.scrollIntoView({ behavior: "smooth", block: "nearest" });
      })
      .catch(function () {
        plan.docError = "Couldn't load the document — it is on disk in " +
                        "data/treatment_plans/.";
        renderPlan();
      });
  }

  /* ── Saved drafts list (shown when no plan is running) ─────────────────── */

  function loadRecentPlans() {
    apiJson("/plans").then(function (data) {
      const host = $("recent-plans");
      clear(host);
      const items = data.plans || [];
      if (!items.length) return;

      host.append(el("div", "recent-head", "Saved drafts"));
      items.slice(0, 8).forEach(function (item) {
        const button = el("button", "recent-item");
        const line = el("div", "recent-line");
        line.append(el("span", "recent-name", item.patient_label));
        // Every saved artifact is a draft, and the list says so on every row.
        line.append(el("span",
          "recent-tag" + (item.kind === "referral_memo" ? " recent-tag-memo" : ""),
          item.kind === "referral_memo" ? "referral note" : "unreviewed draft"));
        line.append(el("span", "recent-when", whenLabel(item.saved_at)));
        button.append(line);
        button.addEventListener("click", function () {
          resetPlan();
          plan.active = true;
          plan.saved = { filename: item.filename, kind: item.kind, unsure: false };
          plan.ended = "saved";
          renderPlan();
          loadPlanDocument(item.filename);
        });
        host.append(button);
      });
    }).catch(function () { /* no drafts yet is not an error */ });
  }

  /* ── Grounding card ───────────────────────────────────────────────────── */

  function loadGrounding() {
    apiJson("/plans/knowledge").then(function (data) {
      const host = $("grounding-body");
      clear(host);

      if (!data.available) {
        host.append(el("p", "muted",
          "Clinical corpus not loaded — plan drafting is unavailable."));
        return;
      }

      const dl = el("dl");
      function row(key, value, extra) {
        const r = el("div", "grounding-row");
        r.append(el("dt", null, key));
        const dd = el("dd", null, String(value));
        if (extra) dd.append(el("span", "grounding-split", " " + extra));
        r.append(dd);
        dl.append(r);
      }
      row("Documents", data.doc_count);
      row("Safety rules", data.rule_count,
        "(" + data.referral_rule_count + " referral / " +
        data.contraindication_rule_count + " contra.)");
      row("Rulebook", "v" + data.rules_version);
      host.append(dl);

      if (data.doc_names && data.doc_names.length) {
        const details = el("details", "grounding-docs");
        details.append(el("summary", null, "Source documents"));
        const ul = el("ul");
        data.doc_names.forEach(function (n) { ul.append(el("li", null, n)); });
        details.append(ul);
        host.append(details);
      }

      host.append(el("div", "grounding-hash", data.corpus_hash));

      // Shown, never hidden. She is the reviewer; the rendered document
      // carries the same notice.
      if (data.reviewed) {
        host.append(el("p", "review-ok", "✓ Safety rules clinically reviewed."));
      } else {
        const note = el("div", "review-note");
        note.append(el("strong", null, "Safety rules not yet clinically reviewed. "));
        note.append(document.createTextNode(
          "Quoted criteria are verified against the source documents; the " +
          "scoping around them is awaiting sign-off."));
        host.append(note);
      }
    }).catch(function () {
      const host = $("grounding-body");
      clear(host);
      host.append(el("p", "muted", "Corpus unavailable."));
    });
  }

  /* ── Email panel ──────────────────────────────────────────────────────── */

  function renderEmail(data) {
    const host = $("email-body");
    clear(host);

    const accounts = data.accounts || [];
    if (!accounts.length) {
      host.append(el("p", "muted", "No email accounts connected."));
      return;
    }

    accounts.forEach(function (account) {
      const wrap = el("section", "account");

      const head = el("div", "account-head");
      head.append(el("span", "account-label", account.label));
      head.append(el("span", "account-provider",
        account.provider === "gmail_oauth" ? "Gmail" : "IMAP"));
      // unread_count is the provider's whole-inbox total; `recent` is the
      // last-2-days cache. They describe different sets, so they are labelled
      // separately and never stacked as though one listed the other.
      head.append(el("span", "account-unread",
        account.unread_count + " unread total"));
      wrap.append(head);

      if (account.last_error) {
        wrap.append(el("div", "account-error", account.last_error));
      }

      const body = el("div", "account-body");
      body.append(el("div", "account-window", "Last 2 days"));

      const recent = account.recent || [];
      if (!recent.length) {
        const empty = el("div", "mail");
        empty.append(el("div", "mail-subject", "Nothing in the last 2 days."));
        body.append(empty);
      } else {
        recent.slice(0, 6).forEach(function (mail) {
          const item = el("div", "mail" + (mail.unread ? " mail-unread" : ""));
          const top = el("div", "mail-top");
          top.append(el("span", "mail-from",
            mail.sender_name || mail.sender_email || "Unknown"));
          top.append(el("span", "mail-when", whenLabel(mail.date)));
          item.append(top);
          item.append(el("div", "mail-subject", mail.subject || "(no subject)"));
          body.append(item);
        });
      }

      wrap.append(body);
      host.append(wrap);
    });
  }

  function loadEmail() {
    apiJson("/email/summary").then(renderEmail).catch(function () {
      const host = $("email-body");
      clear(host);
      host.append(el("p", "muted", "Email summary unavailable."));
    });
  }

  /* ── Tabs ─────────────────────────────────────────────────────────────── */

  function selectTab(which) {
    const isPlan = which === "plan";
    $("tab-plan").classList.toggle("tab-active", isPlan);
    $("tab-email").classList.toggle("tab-active", !isPlan);
    $("panel-plan").hidden = !isPlan;
    $("panel-email").hidden = isPlan;
    if (!isPlan) loadEmail();
  }

  function flagTab(id, on) {
    const tab = $(id);
    const existing = tab.querySelector(".tab-badge");
    if (on && !existing) tab.append(el("span", "tab-badge"));
    if (!on && existing) existing.remove();
  }

  /* ── Events ───────────────────────────────────────────────────────────── */

  function handleEvent(data) {
    switch (data.event) {

      case "wake_word_detected":
        $("wake-value").textContent = "triggered";
        setTimeout(function () { $("wake-value").textContent = "armed"; }, 2500);
        addTurn("System", "Wake word detected", "turn-system");
        break;

      case "listening_started":
        setState("listening");
        break;

      case "transcription":
        if (dictating) addDictationSegment(data.text);
        else addTurn("Alexandra", data.text, "turn-boss");
        break;

      case "llm_response":
        setState("thinking");
        addTurn("Lana", data.text, "turn-lana");
        break;

      case "speaking_started": setState("speaking"); break;
      case "speaking_ended":   lastEventAt = Date.now(); break;

      case "idle":
        setState("idle");
        endDictation();
        break;

      case "error":
        showError(data.message || "A component failed.");
        break;

      /* ── Plan ── */

      case "plan_started":
        resetPlan();
        plan.active = true;
        plan.stepIndex = 0;
        selectTab("plan");
        addTurn("System",
          "Treatment plan requested" +
          (data.patient_hint ? " — " + data.patient_hint : ""), "turn-system");
        renderPlan();
        break;

      case "plan_stage":
        plan.stage = data.stage;
        if (STAGE_TO_STEP[data.stage] != null) {
          plan.stepIndex = STAGE_TO_STEP[data.stage];
          plan.visited.add(plan.stepIndex);
        }
        if (data.stage === "dictating") startDictation();
        else endDictation();
        renderPlan();
        setState(assistantState);
        break;

      case "plan_screened":
        plan.screening = data;
        renderPlan();
        break;

      case "plan_drafted":
        plan.drafted = data;
        renderPlan();
        break;

      case "plan_checked":
        plan.checked = data;
        renderPlan();
        break;

      case "plan_saved":
        plan.saved = data;
        plan.doc = null;
        plan.stepIndex = STAGE_STEPS.length - 1;
        plan.visited.add(plan.stepIndex);
        renderPlan();
        loadPlanDocument(data.filename);
        break;

      case "plan_ended":
        plan.ended = data.outcome;
        plan.stage = null;
        endDictation();
        renderPlan();
        loadRecentPlans();
        break;

      /* ── Email ── */

      case "contact_email_requested":
        $("contact-name").textContent = data.name || "this contact";
        $("contact-msg").textContent = "";
        $("contact-msg").className = "contact-msg";
        $("contact-request").hidden = false;
        selectTab("email");
        flagTab("tab-email", true);
        break;

      case "contact_email_resolved":
        $("contact-request").hidden = true;
        flagTab("tab-email", false);
        break;
    }
  }

  /* ── WebSocket ────────────────────────────────────────────────────────── */

  let socket = null;
  let retryDelay = 1000;

  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const url = scheme + "://" + location.host +
                "/events?token=" + encodeURIComponent(token);

    socket = new WebSocket(url);

    socket.onopen = function () {
      retryDelay = 1000;
      setSocket("live", true);
      showError("");
    };

    socket.onmessage = function (message) {
      let data;
      try { data = JSON.parse(message.data); } catch (e) { return; }
      try { handleEvent(data); }
      catch (e) { console.error("event handler failed", data, e); }
    };

    socket.onclose = function (event) {
      setSocket("reconnecting…", false);
      // 1008 = the server rejected the token. Retrying cannot fix that.
      if (event.code === 1008) {
        sessionStorage.removeItem(STORE_KEY);
        setSocket("bad token", false);
        showGate("That token was rejected.");
        return;
      }
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 1.7, 12000);
    };

    socket.onerror = function () { setSocket("reconnecting…", false); };
  }

  /* ── Status polling — the backstop if the socket ever drops ───────────── */

  function pollStatus() {
    apiJson("/status").then(function (status) {
      $("wake-value").textContent = status.running ? "armed" : "asleep";
      $("btn-start").disabled = status.running;
      $("btn-stop").disabled = !status.running;
      if (status.error) showError(status.error);
      // Events are more timely; only correct from polling when none has
      // arrived recently, so the UI never stutters between the two.
      if (Date.now() - lastEventAt > 4000 && status.state !== assistantState) {
        setState(status.state);
      }
    }).catch(function () { /* server restarting; the next tick will catch up */ });
  }

  /* ── Wiring ───────────────────────────────────────────────────────────── */

  function showGate(message) {
    $("app").hidden = true;
    $("token-gate").hidden = false;
    if (message) {
      const hint = document.querySelector(".gate-hint");
      hint.textContent = message + " " + hint.textContent;
    }
    $("token-input").focus();
  }

  function boot() {
    $("token-gate").hidden = true;
    $("app").hidden = false;

    setState("idle");
    setSocket("connecting…", false);
    connect();
    pollStatus();
    setInterval(pollStatus, 3000);

    loadGrounding();
    loadRecentPlans();
    loadEmail();
    setInterval(function () {
      if (!$("panel-email").hidden) loadEmail();
    }, 45000);
  }

  $("token-form").addEventListener("submit", function (event) {
    event.preventDefault();
    const value = $("token-input").value.trim();
    if (!value) return;
    token = value;
    sessionStorage.setItem(STORE_KEY, token);
    location.reload();
  });

  $("btn-start").addEventListener("click", function () {
    api("/start", { method: "POST" }).then(pollStatus);
  });
  $("btn-stop").addEventListener("click", function () {
    api("/stop", { method: "POST" }).then(pollStatus);
  });
  $("btn-clear").addEventListener("click", function () {
    const transcript = $("transcript");
    clear(transcript);
    transcript.append(el("p", "empty-state", "Transcript cleared."));
    dictationNode = null;
    dictationSegments = [];
  });

  $("tab-plan").addEventListener("click", function () { selectTab("plan"); });
  $("tab-email").addEventListener("click", function () { selectTab("email"); });

  $("contact-form").addEventListener("submit", function (event) {
    event.preventDefault();
    const value = $("contact-input").value.trim();
    const message = $("contact-msg");
    if (!value) return;
    api("/email/pending-contact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: value })
    }).then(function (res) {
      if (res.ok) {
        message.className = "contact-msg";
        message.textContent = "Sent — Lana will pick it up on her next round.";
        $("contact-input").value = "";
      } else {
        message.className = "contact-msg is-bad";
        message.textContent = res.status === 409
          ? "Lana isn't waiting for an address right now."
          : "That doesn't look like a valid address.";
      }
    }).catch(function () {
      message.className = "contact-msg is-bad";
      message.textContent = "Couldn't reach Lana.";
    });
  });

  if (token) boot(); else showGate("");
})();
