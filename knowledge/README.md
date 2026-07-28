# knowledge/ — Alexandra's clinical corpus

This folder is **the only thing Lana grounds treatment plans in.** It is prompt material, not
documentation: `core/plan_knowledge.py` loads every file under `sources/` into one context block that
gets sent to the drafting model verbatim. Editing a file here changes clinical behaviour on the next
run — there is no build step, no cache to clear, and no restart needed beyond the process itself.

## Why this is committed to git

`data/` is git-ignored because it holds patient-adjacent runtime output. `knowledge/` is the
opposite: it is Alexandra's own clinical documentation, contains **zero patient data**, and is
safety-critical. Committing it means every change to a contraindication rule has an author, a date,
and a diff. It also means the repo doubles as a versioned backup of her clinical IP.

It does contain SEED business contact details (clinic phone, `alexa@seed.qa`, the Amrita invite code)
because those are printed on every patient-facing plan and the renderer must reproduce her template
exactly. These are business contact details that already appear on documents she hands to patients —
not private data — but they are in version control, which is worth knowing.

## Layout

```
sources/           the nine converted documents — the corpus sent to the model
safety_rules.json  distilled referral + contraindication rules (the reviewed safety layer)
eval_vignettes.json  synthetic golden test cases; no real patients
```

### sources/

| File | Origin | Role |
| --- | --- | --- |
| `01_standard_vs_unique.md` | Standard vs Unique Plan Logic.docx | Triage: templated protocol vs custom judgment |
| `02_referral_criteria.md` | When to Refer for Further Testing.docx | **Safety.** Quoted by `REF-*` rules |
| `03_contraindications.md` | Supplementation Fundamentals.docx | **Safety core.** Quoted by `CI-*` rules |
| `04_gut_repair_5r.md` | Gut Repair Phases (5R Model).docx | Protocol framework |
| `05_dutch_hormone.md` | DUTCH Hormone Support Protocols.docx | Protocol framework |
| `06_immune_priming.md` | Immune Priming Protocols.docx | Protocol framework |
| `07_skin_conditions.md` | Skin Conditions Protocols.docx | Protocol framework |
| `08_plan_template.md` | _Treatment Plan Phase__DATE_Alexa_NAME.docx | Output shape + verbatim boilerplate |
| `09_seed_formulary.md` | _Treatment Plan with SEED prodcuts.docx | The products she actually prescribes |

Filenames are numbered because `corpus_block()` concatenates them in sorted order and the resulting
string must be **byte-stable** — it is the shared prompt-cache prefix for all three Sonnet calls.
Renaming a file changes the cache key (costs money, breaks nothing). Adding a file is fine and is the
intended way to grow the corpus.

## Updating the corpus

The documents were exported from Google Drive **once**. Lana holds no Drive credentials and never
reads Drive at runtime, so deleting the originals from Drive cannot break anything. The tradeoff is
that updates are manual and deliberate:

1. Edit the relevant file under `sources/` (or add a new numbered one).
2. If the change touches a referral criterion or a contraindication, update `safety_rules.json` too —
   including the `verbatim` quote, which must match the source text exactly.
3. Re-run the safety harnesses. **This is not optional:**
   ```
   python scratchpad/plan_rules_check.py
   python scratchpad/plan_screen_check.py
   python scratchpad/plan_draft_check.py
   ```
4. If `safety_rules.json` changed, Alexandra re-reviews it and `reviewed_on` is bumped.

## safety_rules.json — what Alexandra reviews

The rules file is a distillation of `02_referral_criteria.md` and `03_contraindications.md` into
machine-checkable form. Every rule carries a `verbatim` field quoting her own sentence, and the
renderer prints that quote next to any claim the model makes citing it — so she always reviews
against her own words rather than a paraphrase.

**What her review is actually checking is scoping, not invention.** The quotes are copied from her
sheets; the risk is that a `trigger` is drawn too wide or too narrow. If her sheet says
"Avoid in stage 3+ CKD unless deficiency" and the trigger reads only "CKD", the system becomes wrong
in a way the test vignettes cannot catch, because those vignettes were written from the same reading.
Her review is the only step where someone who knows the clinical intent checks the interpretation.

## Known gaps

Her handover curriculum covered several topics that have **no document here** — they were taught
verbally: functional blood-chemistry ranges, special-populations scope (cancer, MS, Crohn's),
bowel resection / heavy medications / fibromyalgia, and women's-health interventions beyond the
DUTCH material.

This is expected and handled by design: anything the corpus doesn't ground surfaces as an explicit
"NOT COVERED BY YOUR DOCS" flag on the draft rather than being guessed at. Adding a document here is
how that gap closes.

## Source truncation

Three files carry a `<!-- SOURCE-TRUNCATED: ... -->` comment at the point where the Drive reader cut
off (`03_contraindications.md`, `05_dutch_hormone.md`, `06_immune_priming.md`). In all three the
truncation falls in a closing summary or interpretive passage — **no dose, threshold,
contraindication, or referral criterion is affected, and no rule in `safety_rules.json` derives from
a truncated region.** Each comment describes exactly what is missing. Worth completing from the
originals when convenient.
