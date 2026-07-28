# ─────────────────────────────────────────────────────────────────────────────
#  core/plan_knowledge.py – Alexandra's clinical corpus, loaded and validated
#  Reads knowledge/sources/*.md (her converted documents) and
#  knowledge/safety_rules.json (the distilled referral + contraindication
#  layer), validates them, and exposes the byte-stable context block that every
#  treatment-plan LLM call is grounded in.
#
#  Fail-closed by design: if anything here fails to load or validate, the whole
#  feature reports unavailable and Lana refuses to draft. These are committed
#  files, so a failure means a bad commit — the right response is to fail loud,
#  not to quarantine and carry on (which is what memory.py does, correctly, for
#  a runtime store it owns).
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

# Ensure the project root is on sys.path so `import config` works when this
# file is run directly (e.g. `python core/plan_knowledge.py`).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from loguru import logger

# ── Storage location ─────────────────────────────────────────────────────────
# Committed, unlike the git-ignored data/ store: this is clinical IP with no
# patient data in it, and every change to a safety rule should have an author
# and a diff. See knowledge/README.md.
_KNOWLEDGE_DIR: str = os.path.join(_PROJECT_ROOT, "knowledge")
_SOURCES_DIR: str = os.path.join(_KNOWLEDGE_DIR, "sources")
_RULES_FILE: str = os.path.join(_KNOWLEDGE_DIR, "safety_rules.json")

# The template file whose BOILERPLATE blocks the renderer copies verbatim.
_TEMPLATE_SOURCE: str = "08_plan_template.md"
# The formulary file whose table first-columns become the known-product set.
_FORMULARY_SOURCE: str = "09_seed_formulary.md"

# Boilerplate blocks that must exist for a draft to render at all. Others
# (ordering_note, the next_appointment_* variants) are situational.
_REQUIRED_BOILERPLATE: tuple[str, ...] = ("general_recommendations", "the_process")

_BOILERPLATE_RE = re.compile(
    r"<!--\s*BOILERPLATE-START:\s*(?P<key>[a-z0-9_]+)\s*-->\n"
    r"(?P<body>.*?)"
    r"\n<!--\s*BOILERPLATE-END:\s*(?P=key)\s*-->",
    re.DOTALL,
)

# Markdown table cells that are structure, not data — skipped when harvesting
# product names from the formulary tables.
_TABLE_SEPARATOR_CHARS: frozenset[str] = frozenset(":- ")
_FORMULARY_HEADER_CELLS: frozenset[str] = frozenset({"product", "supplement", "item"})

# Linguistic filler only. Product-form words (gel, spray, powder, drops) are
# deliberately NOT here: in her catalog they are what distinguishes one product
# from another ("Magic Gel" vs "Magic Mantra powder"). Stripping them collapsed
# "Magnesium Gel" to the single token {magnesium}, which then matched — and so
# made ambiguous — every other magnesium product.
_FORMULARY_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "with", "for", "of", "a",
})

# A trailing quantity is not part of a product's identity: "Magnesium Citrate
# 200mg" is the same product as "Magnesium Citrate". Matches bare numbers and
# number+unit tokens (200, 200mg, 1000iu).
_QUANTITY_TOKEN_RE = re.compile(r"^\d+[a-z]*$")


class PlanKnowledgeError(RuntimeError):
    """Raised by load() when the corpus or rules fail validation."""


class PlanKnowledge:
    """
    Loads and validates Alexandra's clinical corpus.

    Construction is cheap and never raises; call load() to actually read from
    disk. That split matches the house convention (STTEngine.load_model,
    TTSEngine.initialize, MemoryManager.load) and lets LanaAssistant construct
    this at import time without touching the filesystem.

    After load(), check `available`. If it is False, `unavailable_reason` says
    why and no drafting may proceed.
    """

    def __init__(self) -> None:
        self._sources: dict[str, str] = {}          # filename -> markdown text
        self._rules: dict = {}                       # parsed safety_rules.json
        self._rules_by_id: dict[str, dict] = {}
        self._boilerplate: dict[str, str] = {}
        self._formulary: dict[str, str] = {}         # normalized -> canonical name
        self._corpus_block: str = ""
        self._corpus_hash: str = ""
        self._available: bool = False
        self._unavailable_reason: str = "load() has not been called yet"

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(self) -> bool:
        """
        Read and validate everything. Returns True if the feature is usable.

        Never raises: a broken corpus must degrade to "Lana says she can't do
        treatment plans right now", not to a crashed conversation thread.
        """
        try:
            self._load_sources()
            self._load_rules()
            self._validate_rules()
            self._load_boilerplate()
            self._load_formulary()
            self._build_corpus_block()
        except PlanKnowledgeError as exc:
            self._available = False
            self._unavailable_reason = str(exc)
            logger.critical(f"Treatment-plan knowledge failed to load: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001 - last line of defence
            self._available = False
            self._unavailable_reason = f"unexpected error: {exc}"
            logger.exception(f"Treatment-plan knowledge failed to load: {exc}")
            return False

        self._available = True
        self._unavailable_reason = ""
        logger.success(
            f"Treatment-plan knowledge loaded: {len(self._sources)} source docs, "
            f"{len(self._rules_by_id)} safety rules, {len(self._formulary)} formulary "
            f"products, corpus {len(self._corpus_block):,} chars (hash {self._corpus_hash})."
        )
        if not self.rules_reviewed:
            logger.warning(
                "safety_rules.json has not been reviewed by Alexandra yet "
                f"(review_status: {self._rules.get('review_status', 'unset')}). "
                "Every rendered draft will say so until reviewed_by/reviewed_on are set."
            )
        return True

    def _load_sources(self) -> None:
        if not os.path.isdir(_SOURCES_DIR):
            raise PlanKnowledgeError(f"missing corpus directory: {_SOURCES_DIR}")

        names = sorted(n for n in os.listdir(_SOURCES_DIR) if n.endswith(".md"))
        if not names:
            raise PlanKnowledgeError(f"no .md files in {_SOURCES_DIR}")

        for name in names:
            path = os.path.join(_SOURCES_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError as exc:
                raise PlanKnowledgeError(f"could not read {name}: {exc}") from exc
            if not text.strip():
                raise PlanKnowledgeError(f"{name} is empty")
            # Normalize line endings so corpus_block() is byte-stable regardless
            # of how git/OneDrive checked the file out. This string is the
            # prompt-cache prefix; a stray \r\n would silently miss the cache.
            self._sources[name] = text.replace("\r\n", "\n").replace("\r", "\n")

        for required in (_TEMPLATE_SOURCE, _FORMULARY_SOURCE):
            if required not in self._sources:
                raise PlanKnowledgeError(f"required source file missing: {required}")

    def _load_rules(self) -> None:
        try:
            with open(_RULES_FILE, "r", encoding="utf-8") as f:
                self._rules = json.load(f)
        except FileNotFoundError as exc:
            raise PlanKnowledgeError(f"missing {_RULES_FILE}") from exc
        except json.JSONDecodeError as exc:
            raise PlanKnowledgeError(f"safety_rules.json is not valid JSON: {exc}") from exc
        except OSError as exc:
            raise PlanKnowledgeError(f"could not read safety_rules.json: {exc}") from exc

        for key in ("referral_rules", "contraindication_rules"):
            if not isinstance(self._rules.get(key), list):
                raise PlanKnowledgeError(f"safety_rules.json missing list key: {key}")

    def _validate_rules(self) -> None:
        """
        Enforce the contract the review step depends on.

        The load-bearing check is the verbatim-substring one. The renderer
        prints a rule's `verbatim` next to any claim the model makes citing that
        rule, so Alexandra reviews the model's judgment against *her own
        sentence*. If a verbatim drifts from its source, she is silently
        reviewing against a paraphrase instead — which is the one failure this
        whole design exists to prevent.
        """
        seen: set[str] = set()

        for bucket in ("referral_rules", "contraindication_rules"):
            for i, rule in enumerate(self._rules[bucket]):
                where = f"{bucket}[{i}]"
                if not isinstance(rule, dict):
                    raise PlanKnowledgeError(f"{where} is not an object")

                rule_id = rule.get("id", "")
                if not rule_id or not isinstance(rule_id, str):
                    raise PlanKnowledgeError(f"{where} has no id")
                if rule_id in seen:
                    raise PlanKnowledgeError(f"duplicate rule id: {rule_id}")
                seen.add(rule_id)

                verbatim = rule.get("verbatim", "")
                if not verbatim or not isinstance(verbatim, str):
                    raise PlanKnowledgeError(f"{rule_id} has no verbatim quote")

                source = rule.get("source", "")
                if not source or not isinstance(source, str):
                    raise PlanKnowledgeError(f"{rule_id} has no source")
                fname = source.split("#", 1)[0]
                if fname not in self._sources:
                    raise PlanKnowledgeError(
                        f"{rule_id} cites source file that does not exist: {fname}"
                    )
                if verbatim not in self._sources[fname]:
                    raise PlanKnowledgeError(
                        f"{rule_id} verbatim is not a literal substring of {fname} — "
                        f"the quote has drifted from the source, so Alexandra would be "
                        f"reviewing a paraphrase. Quote: {verbatim[:80]!r}"
                    )

                self._rules_by_id[rule_id] = rule

        if not self._rules_by_id:
            raise PlanKnowledgeError("safety_rules.json contains no rules")

    def _load_boilerplate(self) -> None:
        text = self._sources[_TEMPLATE_SOURCE]
        for match in _BOILERPLATE_RE.finditer(text):
            self._boilerplate[match.group("key")] = match.group("body").strip("\n")

        missing = [k for k in _REQUIRED_BOILERPLATE if not self._boilerplate.get(k)]
        if missing:
            raise PlanKnowledgeError(
                f"{_TEMPLATE_SOURCE} is missing required BOILERPLATE block(s): "
                f"{', '.join(missing)}"
            )

    def _load_formulary(self) -> None:
        """
        Harvest product names from the first column of every table in the
        formulary doc. A miss here is not fatal — an unmatched product is
        flagged on the draft ("not in your SEED formulary"), not rejected,
        because she does prescribe outside the list when a case needs it.
        """
        for line in self._sources[_FORMULARY_SOURCE].split("\n"):
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue
            name = cells[0]
            if not name:
                continue
            if set(name) <= _TABLE_SEPARATOR_CHARS:      # | :-: | separator row
                continue
            if name.lower() in _FORMULARY_HEADER_CELLS:  # header row
                continue
            self._formulary[self._normalize_product(name)] = name

        if not self._formulary:
            raise PlanKnowledgeError(
                f"no product names could be parsed from {_FORMULARY_SOURCE}"
            )

    def _build_corpus_block(self) -> None:
        """
        Build the single context string every Sonnet call shares.

        This must be byte-identical across calls within a session: it is the
        prompt-cache prefix, so all three calls (screen, draft, check) pay one
        cache write and read thereafter. Sorted filenames + normalized newlines
        + no timestamps or dict iteration order anywhere = stable.
        """
        parts: list[str] = [
            "The following is the complete clinical documentation of Alexandra "
            "Daccache (SEED Health and Wellness Centre). It is the ONLY body of "
            "knowledge you may ground a treatment plan in.\n"
        ]

        for name in sorted(self._sources):
            parts.append(f"\n=== SOURCE: {name} ===\n")
            parts.append(self._sources[name])
            parts.append("\n")

        parts.append("\n=== DISTILLED SAFETY RULES (cite these by id) ===\n")
        parts.append(self._render_rules())

        self._corpus_block = "".join(parts)
        self._corpus_hash = hashlib.sha256(
            self._corpus_block.encode("utf-8")
        ).hexdigest()[:12]

    def _render_rules(self) -> str:
        """Deterministic rendering of the rules for the prompt (sorted by id)."""
        lines: list[str] = []

        lines.append("\n-- REFERRAL RULES --")
        lines.append(
            "kind=refer_out means the case should be referred out. "
            "kind=test_first means this specific intervention must not start "
            "until the named labs exist (it is not a referral of the whole case).\n"
        )
        for rule in sorted(self._rules["referral_rules"], key=lambda r: r["id"]):
            lines.append(
                f"[{rule['id']}] kind={rule.get('kind', 'refer_out')} "
                f"category={rule.get('category', '')}\n"
                f"  criteria: {rule.get('criteria', '')}\n"
                f"  refer to: {rule.get('refer_to', '')}\n"
                f"  her words: \"{rule['verbatim']}\"\n"
                f"  source: {rule['source']}"
            )

        lines.append("\n-- CONTRAINDICATION RULES --")
        for rule in sorted(self._rules["contraindication_rules"], key=lambda r: r["id"]):
            trigger = rule.get("trigger", {})
            conditions = ", ".join(trigger.get("conditions") or []) or "—"
            medications = ", ".join(trigger.get("medications") or []) or "—"
            population = trigger.get("population") or "—"
            restricted = "; ".join(
                f"{item.get('item', '')} → {item.get('action', '')}"
                + (f" ({item['limit']})" if item.get("limit") else "")
                + (f" [{item['note']}]" if item.get("note") else "")
                for item in rule.get("restricted", [])
            )
            lines.append(
                f"[{rule['id']}]\n"
                f"  applies when — conditions: {conditions} | medications: {medications} "
                f"| population: {population}\n"
                f"  restricted: {restricted}\n"
                f"  risk: {rule.get('risk', '')}\n"
                f"  her words: \"{rule['verbatim']}\"\n"
                f"  source: {rule['source']}"
            )

        return "\n".join(lines) + "\n"

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """False means no drafting may proceed. Check this before every use."""
        return self._available

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    @property
    def rules_reviewed(self) -> bool:
        """
        Whether Alexandra has signed off on safety_rules.json.

        False does not block drafting — the rules are still applied — but the
        renderer stamps every draft with a notice, because until she has checked
        the scoping of each trigger, the distillation is one person's reading of
        her documents.
        """
        return bool(self._rules.get("reviewed_by") and self._rules.get("reviewed_on"))

    @property
    def review_status(self) -> str:
        return str(self._rules.get("review_status", "")).strip()

    @property
    def rules_version(self) -> int:
        try:
            return int(self._rules.get("version", 0))
        except (TypeError, ValueError):
            return 0

    @property
    def source_count(self) -> int:
        """How many of her documents are in the corpus."""
        return len(self._sources)

    def source_names(self) -> tuple[str, ...]:
        """
        The corpus filenames, sorted. Read-only provenance for the dashboard —
        a draft that cites "her documentation" should be able to say which
        documents. Returns names only; the contents are never served.
        """
        return tuple(sorted(self._sources))

    def corpus_block(self) -> str:
        """The full grounding context. Byte-stable — this is the cache prefix."""
        return self._corpus_block

    def corpus_hash(self) -> str:
        """Short digest of corpus_block(), stamped into every draft's provenance."""
        return self._corpus_hash

    def rule(self, rule_id: str) -> dict | None:
        """Look up one rule by id. Returns None for an unknown id."""
        return self._rules_by_id.get(rule_id)

    def all_rule_ids(self) -> frozenset[str]:
        """
        Every valid rule id. Used to catch a model citing a rule that does not
        exist — a hallucinated citation is caught deterministically in Python
        rather than trusted.
        """
        return frozenset(self._rules_by_id)

    def referral_rule_ids(self) -> frozenset[str]:
        return frozenset(r["id"] for r in self._rules.get("referral_rules", []))

    def contraindication_rule_ids(self) -> frozenset[str]:
        return frozenset(r["id"] for r in self._rules.get("contraindication_rules", []))

    def template_boilerplate(self) -> dict[str, str]:
        """
        The verbatim fixed blocks from her template. The renderer copies these;
        no model ever generates them, so her standing patient instructions
        cannot be reworded, softened, or dropped by a drafting call.
        """
        return dict(self._boilerplate)

    def formulary_names(self) -> frozenset[str]:
        """Canonical product names she prescribes."""
        return frozenset(self._formulary.values())

    def formulary_match(self, product: str) -> str | None:
        """
        Resolve a proposed product name to a formulary entry, or None.

        Token-overlap rather than substring, for the same reason
        EmailManager.subject_hint_matches is: a model writing "Magnesium
        Citrate 200mg" or "Biogena Omega 3 Forte" should still resolve. One
        name's significant tokens must be a subset of the other's, so
        "Magnesium Oil" and "Magnesium Citrate" stay distinct.

        An ambiguous name resolves to None, not to an arbitrary winner. Bare
        "Magnesium" is a subset of several entries; picking one would assert a
        specific product she never chose, and form matters here (her own sheet
        says "verify the form … magnesium glycinate vs oxide", and CI-KID-5
        bans the oxide outright). None means the draft flags it for her, which
        is the safe direction — same instinct as find_people_by_name asking
        which person rather than guessing.
        """
        if not product or not product.strip():
            return None

        normalized = self._normalize_product(product)
        if normalized in self._formulary:
            return self._formulary[normalized]

        tokens = self._significant_tokens(product)
        if not tokens:
            return None

        matches: set[str] = set()
        for candidate_norm, canonical in self._formulary.items():
            candidate_tokens = self._significant_tokens(candidate_norm)
            if not candidate_tokens:
                continue
            if tokens <= candidate_tokens or candidate_tokens <= tokens:
                matches.add(canonical)

        if len(matches) == 1:
            return next(iter(matches))
        return None  # zero matches, or ambiguous — flag it either way

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_product(name: str) -> str:
        """
        Lowercase, strip punctuation, collapse whitespace.

        Apostrophes are DELETED rather than replaced with a space, so
        "Nature's Rinse" tokenizes as {natures, rinse} and matches a model
        writing "Natures Rinse". Replacing them with a space instead yields
        {nature, s, rinse}, which matches neither spelling — a silent
        no-match on any possessive product name.
        """
        lowered = name.lower().replace("'", "").replace("’", "")
        cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
        return " ".join(cleaned.split())

    @classmethod
    def _significant_tokens(cls, name: str) -> frozenset[str]:
        """Normalized tokens minus linguistic filler and dose quantities."""
        return frozenset(
            tok
            for tok in cls._normalize_product(name).split()
            if tok not in _FORMULARY_STOPWORDS and not _QUANTITY_TOKEN_RE.match(tok)
        )


# ── Standalone smoke test ────────────────────────────────────────────────────
# python -m core.plan_knowledge
if __name__ == "__main__":
    knowledge = PlanKnowledge()
    ok = knowledge.load()

    if not ok:
        print(f"UNAVAILABLE: {knowledge.unavailable_reason}")
        sys.exit(1)

    print(f"corpus hash      : {knowledge.corpus_hash()}")
    print(f"corpus size      : {len(knowledge.corpus_block()):,} chars "
          f"(~{len(knowledge.corpus_block()) // 4:,} tokens)")
    print(f"rules version    : {knowledge.rules_version}")
    print(f"rules reviewed   : {knowledge.rules_reviewed}")
    if not knowledge.rules_reviewed:
        print(f"  status         : {knowledge.review_status}")
    print(f"referral rules   : {len(knowledge.referral_rule_ids())}")
    print(f"contra rules     : {len(knowledge.contraindication_rule_ids())}")
    print(f"formulary items  : {len(knowledge.formulary_names())}")
    print(f"boilerplate keys : {', '.join(sorted(knowledge.template_boilerplate()))}")

    # Byte-stability of the cache prefix: two loads must agree, or every call
    # silently misses the prompt cache.
    second = PlanKnowledge()
    second.load()
    assert second.corpus_hash() == knowledge.corpus_hash(), "corpus_block is not stable!"
    print("\ncorpus_block is byte-stable across loads (cache prefix holds)")

    print("\nformulary matching spot-checks:")
    for probe in ("Magnesium Citrate", "magnesium citrate 200mg", "Omega 3 Forte Superior",
                  "Quinton Isotonic", "Sleep Mantra", "Magnesium", "Some Random Product"):
        print(f"  {probe!r:38} -> {knowledge.formulary_match(probe)!r}")
    print("  (bare 'Magnesium' -> None is correct: ambiguous across her magnesium")
    print("   products, and form matters — CI-KID-5 bans the oxide specifically.)")

    print("\nsample rule lookup:")
    for probe in ("REF-C2", "CI-THY-1", "CI-MED-1", "NOPE-1"):
        rule = knowledge.rule(probe)
        quote = (rule["verbatim"][:64] + "…") if rule else None
        print(f"  {probe:10} -> {quote!r}")
