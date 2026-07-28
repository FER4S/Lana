# Safety rules

<!-- GENERATED FROM safety_rules.json — DO NOT EDIT BY HAND -->
<!-- RULES-HASH: 3868daa65a7f -->

**18 referral rules · 58 contraindication rules**

---

## About this document

Lana drafts treatment plans grounded only in your own documents. To make the safety-critical
parts machine-checkable, your **When to Refer for Further Testing** and **Supplementation
Fundamentals** documents have been broken into individual rules, listed below. Each one shows:

- **Your words** — quoted directly from your document. These are checked automatically against
  the source text, so they cannot have been reworded or paraphrased.
- **Applied when** — the situation that rule has been set to fire in.

The quotes are verified. **What's worth your eye is the scoping** — whether each rule has been
drawn around your words too widely or too narrowly. Automated testing can't catch that, since
the test cases come from the same reading of your documents that produced the rules.

**An example of the kind of judgment involved — `CI-KID-2` (calcium).** Your sheet says
*"Use cautiously; citrate form preferred if needed. Avoid in stage 3+ CKD unless deficiency."*
That's two instructions at two thresholds: general caution, and outright avoidance at stage 3+.
It's currently encoded as one rule that fires for kidney impairment generally, with the stage 3+
avoidance carried in the note. Whether that's the right shape — or whether mild impairment and
stage 3+ should behave as separate rules — is a clinical call. Several kidney and liver rules
involve thresholds like this.

Two questions worth holding while reading:

1. **Too wide or too narrow?** Would this fire in situations where it shouldn't, or stay quiet
   in situations where it should?
2. **Anything missing?** A situation you'd always refer on, or always avoid, that isn't here.

### Two things worth knowing

**Over-referring is safe; under-referring is not.** The system is deliberately tuned to raise a
referral whenever one is plausibly warranted, and it never concludes on its own that a case is
fine. Flags come in two forms — criteria squarely matched, and partial matches shown separately
as worth checking — so a borderline case surfaces without competing for attention with a clear
one.

**Nothing is ever sent anywhere.** Lana writes a draft file to be opened and reviewed. There is
no path for a plan to reach a patient, by design.

---

# Part 1 — When to refer

Source: your *When to Refer for Further Testing* document (plus referral triggers
in *Skin Conditions Protocols*).

Two kinds of rule here, and the difference matters:

- **Refer out** — the case should go to someone else, in addition to or instead of a plan.
- **Test first** — not a referral of the whole case. It means one specific intervention
  must not start until certain labs exist. Everything else can proceed.

## Unexplained or severe symptoms

### `REF-A1` · REFER OUT

**Your words:** *“Weight loss or gain without cause”*

**Applied when:** Unexplained weight loss or weight gain with no identified cause

**Points to:** Physician for deeper investigation

### `REF-A2` · REFER OUT

**Your words:** *“Dizziness, syncope, arrhythmia-like symptoms”*

**Applied when:** Dizziness, fainting, or arrhythmia-like symptoms (palpitations, irregular heartbeat)

**Points to:** Physician / cardiology

### `REF-A3` · REFER OUT

**Your words:** *“Persistent fatigue despite mitochondrial/GI support”*

**Applied when:** Fatigue that persists despite mitochondrial and GI support already having been tried

**Points to:** Physician for deeper investigation


## No improvement after 8–12 weeks

### `REF-B1` · REFER OUT

**Your words:** *“E.g., no change in constipation after full 5R plan”*

**Applied when:** No symptom change after a full protocol has run 8–12 weeks (e.g. constipation unchanged after a complete 5R plan)

**Points to:** Physician for deeper investigation

### `REF-B2` · REFER OUT

**Your words:** *“Ongoing skin rashes despite gut + histamine protocols”*

**Applied when:** Skin rashes ongoing despite gut and histamine protocols having been completed

**Points to:** Dermatology / physician


## Red flag clusters

### `REF-C1` · REFER OUT

**Your words:** *“Night sweats + weight loss + pain (consider imaging, oncology referral)”*

**Applied when:** Night sweats AND weight loss AND pain occurring together

**Points to:** Imaging / oncology referral

### `REF-C2` · REFER OUT

**Your words:** *“Blood in stool, black tarry stools, anemia of unknown origin (GI referral)”*

**Applied when:** Blood in stool, black tarry stools, or anemia of unknown origin

**Points to:** GI referral

### `REF-C3` · REFER OUT

**Your words:** *“Frequent infections, persistent lymphadenopathy (immune workup)”*

**Applied when:** Frequent infections together with persistent swollen lymph nodes

**Points to:** Immune workup

### `REF-C4` · REFER OUT

**Your words:** *“Unilateral headaches or neurological symptoms (neuro or imaging)”*

**Applied when:** Unilateral (one-sided) headaches, or any neurological symptoms

**Points to:** Neurology or imaging


## Test before starting a specific intervention

### `REF-D1` · TEST FIRST

**Your words:** *“Considering DHEA → check hormones & adrenal markers”*

**Applied when:** DHEA is being considered

**Points to:** Hormone and adrenal marker testing before starting

**Blocks until labs exist:** DHEA

### `REF-D2` · TEST FIRST

**Your words:** *“Considering methylation support → check homocysteine, B12, MTHFR”*

**Applied when:** Methylation support is being considered

**Points to:** Homocysteine, B12 and MTHFR testing before starting

**Blocks until labs exist:** methylation support, 5-MTHF, methylfolate, methylated B vitamins, SAMe

### `REF-D3` · TEST FIRST

**Your words:** *“Recurring miscarriages → test thyroid, PCOS, progesterone, clotting factors”*

**Applied when:** History of recurring miscarriages

**Points to:** Thyroid, PCOS, progesterone and clotting factor testing


## Symptoms suggesting another specialty

### `REF-E1` · REFER OUT

**Your words:** *“Pelvic pain unresponsive to gut support → GYN, pelvic ultrasound”*

**Applied when:** Pelvic pain that has not responded to gut support

**Points to:** GYN, pelvic ultrasound

### `REF-E2` · REFER OUT

**Your words:** *“Severe joint pain → rheumatology”*

**Applied when:** Severe joint pain

**Points to:** Rheumatology

### `REF-E3` · REFER OUT

**Your words:** *“Chronic cough + GI issues → rule out GERD, pulmonary referral”*

**Applied when:** Chronic cough together with GI issues

**Points to:** Rule out GERD; pulmonary referral


## Dermatology referral (from your skin protocols)

### `REF-F1` · REFER OUT

**Your words:** *“Refer to dermatologist if severe cystic acne or scarring present.”*

**Applied when:** Severe cystic acne, or acne scarring present

**Points to:** Dermatologist

### `REF-F2` · REFER OUT

**Your words:** *“Referral to dermatologist for possible laser or topical prescription meds if moderate/severe.”*

**Applied when:** Moderate or severe rosacea

**Points to:** Dermatologist (laser or topical prescription)

### `REF-F3` · REFER OUT

**Your words:** *“Referral to dermatologist if severe or infected lesions.”*

**Applied when:** Severe or infected eczema lesions

**Points to:** Dermatologist


---

# Part 2 — Contraindications

Source: your *Supplementation Fundamentals* contraindications sheet (plus cautions
from *Immune Priming Protocols* and the *5R* document, marked where they apply).

Each rule says: when this patient situation is present, these things are restricted.

## Kidney impairment

### `CI-KID-1`

**Your words:** *“Keep dose ≤ 2000 IU unless 25(OH)D is < 20 ng/mL. Monitor calcium, phosphorus.”*

**Applied when:** patient has kidney impairment / CKD / low eGFR / kidney stones

**Risk:** Can elevate calcium → risk of vascular calcification, stones

**Restricts:**

- Vitamin D — **cap the dose** to 2000 IU/day unless 25(OH)D is < 20 ng/mL _(Monitor calcium, phosphorus.)_

### `CI-KID-2`

**Your words:** *“Use cautiously; citrate form preferred if needed. Avoid in stage 3+ CKD unless deficiency.”*

**Applied when:** patient has stage 3+ CKD / kidney stones / kidney impairment

**Risk:** Increases stone risk, vascular calcification

**Restricts:**

- Calcium — use with monitoring _(Citrate form preferred if needed. Avoid in stage 3+ CKD unless deficiency.)_

### `CI-KID-3`

**Your words:** *“Avoid in CKD or elevated creatinine.”*

**Applied when:** patient has CKD / elevated creatinine / kidney impairment

**Risk:** Risk of elevated creatinine and metabolic load

**Restricts:**

- Creatine — **do not use**

### `CI-KID-4`

**Your words:** *“Monitor potassium if GFR < 60. Avoid potassium-containing blends.”*

**Applied when:** patient has kidney impairment / GFR below 60 / CKD

**Risk:** Risk of hyperkalemia

**Restricts:**

- Potassium (including multivitamins and electrolyte blends containing potassium) — **do not use** _(Monitor potassium if GFR < 60.)_

### `CI-KID-5`

**Your words:** *“Use ≤ 200–300 mg/day, prefer bisglycinate or taurate. Avoid magnesium oxide.”*

**Applied when:** patient has kidney impairment / GFR below 30 / CKD

**Risk:** Accumulation if GFR < 30 → arrhythmia risk

**Restricts:**

- Magnesium oxide — **do not use**
- Magnesium (any form) — **cap the dose** to 200–300 mg/day _(Prefer bisglycinate or taurate.)_

### `CI-KID-6`

**Your words:** *“Avoid unless phosphorus deficiency diagnosed.”*

**Applied when:** patient has kidney impairment / CKD

**Risk:** Risk of phosphorus overload

**Restricts:**

- Phosphorus-containing supplements — **do not use** _(Unless phosphorus deficiency diagnosed.)_

### `CI-KID-7`

**Your words:** *“Avoid high-dose retinol; prefer beta-carotene if needed. Max 2500 IU/day retinol form.”*

**Applied when:** patient has kidney impairment / CKD

**Risk:** Can build up, especially in CKD

**Restricts:**

- Vitamin A (retinol form) — **cap the dose** to 2500 IU/day retinol form _(Prefer beta-carotene if needed.)_


## Liver dysfunction

### `CI-LIV-1`

**Your words:** *“Use flush-free or low-dose niacin if needed. Monitor LFTs.”*

**Applied when:** patient has liver dysfunction / fatty liver / NAFLD / NASH / hepatitis / elevated liver enzymes

**Risk:** Hepatotoxic at >1000 mg/day

**Restricts:**

- Niacin — **cap the dose** to below 1000 mg/day _(Use flush-free or low-dose niacin if needed. Monitor LFTs.)_

### `CI-LIV-2`

**Your words:** *“Avoid chronic use >5000 IU retinol. Use beta-carotene if needed.”*

**Applied when:** patient has liver dysfunction / fatty liver / NAFLD / NASH / hepatitis / elevated liver enzymes

**Risk:** Hepatotoxic in high doses

**Restricts:**

- Vitamin A (retinol) — **cap the dose** to 5000 IU retinol, chronic use _(Use beta-carotene if needed.)_

### `CI-LIV-3`

**Your words:** *“Avoid unless ferritin < 40 ng/mL and TSAT < 20%.”*

**Applied when:** patient has liver dysfunction / fatty liver / NAFLD / NASH

**Risk:** Risk of hepatic overload in NAFLD/NASH

**Restricts:**

- Iron — **require labs first** _(Only if ferritin < 40 ng/mL and TSAT < 20%.)_

### `CI-LIV-4`

**Your words:** *“Hepatotoxic potential | Avoid completely.”*

**Applied when:** patient has liver dysfunction / fatty liver / NAFLD / NASH / hepatitis / elevated liver enzymes

**Risk:** Hepatotoxic potential

**Restricts:**

- Kava — **do not use**
- Comfrey — **do not use**
- Chaparral — **do not use**
- Skullcap — **do not use**

### `CI-LIV-5`

**Your words:** *“Keep ≤ 500 mg BID; use BCM-95 or Meriva for better tolerability.”*

**Applied when:** patient has liver dysfunction / fatty liver / NAFLD / NASH / elevated liver enzymes

**Risk:** Can raise liver enzymes in sensitive patients

**Restricts:**

- Curcumin — **cap the dose** to 500 mg BID _(Use BCM-95 or Meriva for better tolerability.)_

### `CI-LIV-6`

**Your words:** *“Avoid in severe liver failure. Use cautiously in NAFLD/NASH.”*

**Applied when:** patient has severe liver failure / advanced liver dysfunction / NAFLD / NASH

**Risk:** May be poorly metabolized in advanced liver dysfunction

**Restricts:**

- Acetyl-L-carnitine — **do not use** _(Avoid in severe liver failure; use cautiously in NAFLD/NASH.)_


## Autoimmune conditions

### `CI-AI-1`

**Your words:** *“Avoid; can worsen autoimmunity.”*

**Applied when:** patient has autoimmune / Hashimoto's / MS / RA / SLE / lupus

**Risk:** Immune stimulant

**Restricts:**

- Echinacea — **do not use**

### `CI-AI-2`

**Your words:** *“Avoid unless used under supervision for adrenal support (short-term).”*

**Applied when:** patient has autoimmune / Hashimoto's / MS / RA / SLE / lupus

**Risk:** May upregulate immune response

**Restricts:**

- Astragalus — **do not use** _(Unless used under supervision for adrenal support (short-term).)_
- Licorice root — **do not use** _(Unless used under supervision for adrenal support (short-term).)_

### `CI-AI-3`

**Your words:** *“Use with caution; monitor autoimmune symptoms.”*

**Applied when:** patient has autoimmune / Hashimoto's / MS / RA / SLE / lupus

**Risk:** Can stimulate immune activity

**Restricts:**

- DHEA — use with monitoring _(Monitor autoimmune symptoms.)_

### `CI-AI-4`

**Your words:** *“Avoid in active AI disease unless under supervision.”*

**Applied when:** patient has active autoimmune disease / autoimmune

**Risk:** Can flare immune reactivity

**Restricts:**

- Colostrum — **do not use** _(Unless under supervision.)_


## Thyroid conditions

### `CI-THY-1`

**Your words:** *“Avoid high-dose iodine unless tested deficient. Selenium must be sufficient first.”*

**Applied when:** patient has Hashimoto's / thyroid condition / autoimmune thyroid / hypothyroidism

**Risk:** Can trigger Hashimoto's flare

**Restricts:**

- Iodine — **cap the dose** to 150 mcg _(Selenium must be sufficient first. Only exceed if tested deficient.)_

### `CI-THY-2`

**Your words:** *“May increase T3/T4; avoid in hyperthyroid. OK in hypo with monitoring.”*

**Applied when:** patient has hyperthyroidism / Graves' / thyroid condition

**Risk:** Modulates thyroid hormones; may increase T3/T4

**Restricts:**

- Ashwagandha — **do not use** _(Avoid in hyperthyroid. OK in hypo with monitoring.)_

### `CI-THY-3`

**Your words:** *“Check ferritin (>70 optimal for thyroid). Avoid unnecessary iron in AI thyroid.”*

**Applied when:** patient has autoimmune thyroid / Hashimoto's

**Risk:** Needed for T4→T3 conversion but not to be given unnecessarily in autoimmune thyroid

**Restricts:**

- Iron — **require labs first** _(Check ferritin (>70 optimal for thyroid). Avoid unnecessary iron in AI thyroid.)_

### `CI-THY-4`

**Your words:** *“Use cautiously in Graves' or with active medication adjustment.”*

**Applied when:** patient has Graves' / hyperthyroidism / thyroid condition; or is taking thyroid medication

**Risk:** Thyroid precursor amino acid

**Restricts:**

- L-tyrosine — use with monitoring

### `CI-THY-5`

**Your words:** *“Discontinue at least 3 days before testing. Can skew TSH, T3, T4 readings.”*

**Applied when:** patient has thyroid condition / upcoming thyroid labs

**Risk:** Interferes with thyroid labs; can skew TSH, T3, T4 readings

**Restricts:**

- Biotin — stop before testing (distorts results) _(Discontinue at least 3 days before testing.)_


## Other cautions (hormone-sensitive, psychiatric meds, active GI inflammation)

### `CI-OTH-1`

**Your words:** *“Avoid unless hormone labs show clear deficiency and patient is not high risk.”*

**Applied when:** patient has estrogen dominance / history of hormone-sensitive cancer / breast cancer history

**Risk:** Can worsen hormone-driven symptoms

**Restricts:**

- DHEA — **do not use**
- Pregnenolone — **do not use**
- Maca — **do not use**
- Tribulus — **do not use**

### `CI-OTH-2`

**Your words:** *“Avoid unless cleared with prescribing psychiatrist.”*

**Applied when:** patient is taking SSRI / MAOI / psychiatric medication / antidepressant

**Risk:** Risk of serotonin syndrome

**Restricts:**

- 5-HTP — **do not use**
- St. John's Wort — **do not use**
- SAMe — **do not use**

### `CI-OTH-3`

**Your words:** *“Use chelated forms; consider iron bisglycinate, zinc picolinate.”*

**Applied when:** patient has IBD / Crohn's / ulcerative colitis / gastritis / active GI inflammation

**Risk:** GI irritation

**Restricts:**

- Iron — use with monitoring _(Use chelated forms; consider iron bisglycinate.)_
- NSAIDs — **do not use**
- Zinc sulfate — use with monitoring _(Consider zinc picolinate instead.)_


## High-risk medications

### `CI-MED-1`

**Your words:** *“Vitamin K2 (MK-4, MK-7) — can antagonize warfarin.”*

**Applied when:** patient is taking warfarin / Coumadin / anticoagulant

**Risk:** Can antagonize warfarin

**Restricts:**

- Vitamin K2 (MK-4, MK-7) — **do not use**

### `CI-MED-10`

**Your words:** *“Avoid herbs with CNS effects (e.g. valerian, skullcap, kava).”*

**Applied when:** patient has epilepsy / seizure disorder; or is taking anticonvulsant / phenytoin / carbamazepine

**Risk:** CNS effects

**Restricts:**

- Valerian — **do not use**
- Skullcap — **do not use**
- Kava — **do not use**

### `CI-MED-11`

**Your words:** *“Grapefruit extract, CBD, curcumin may alter drug metabolism.”*

**Applied when:** patient has epilepsy / seizure disorder; or is taking anticonvulsant / phenytoin / carbamazepine

**Risk:** May alter drug metabolism

**Restricts:**

- Grapefruit extract — use with monitoring
- CBD — use with monitoring
- Curcumin — use with monitoring

### `CI-MED-12`

**Your words:** *“High-dose antioxidants, curcumin, resveratrol, CoQ10 during active chemo/radiation unless approved.”*

**Applied when:** patient has active cancer treatment; or is taking chemotherapy / radiation

**Risk:** Oxidative stress interference during active treatment

**Restricts:**

- High-dose antioxidants — **do not use** _(Unless approved by oncology.)_
- Curcumin — **do not use** _(Unless approved by oncology.)_
- Resveratrol — **do not use** _(Unless approved by oncology.)_
- CoQ10 — **do not use** _(Unless approved by oncology.)_

### `CI-MED-2`

**Your words:** *“Fish oil, vitamin E (≥400 IU), garlic, ginkgo biloba, ginger, and turmeric — may increase bleeding risk.”*

**Applied when:** patient is taking warfarin / Coumadin / anticoagulant

**Risk:** May increase bleeding risk

**Restricts:**

- Fish oil — use with monitoring
- Vitamin E (≥400 IU) — use with monitoring
- Garlic — use with monitoring
- Ginkgo biloba — use with monitoring
- Ginger — use with monitoring
- Turmeric — use with monitoring

### `CI-MED-3`

**Your words:** *“High-dose CoQ10 (structurally similar to vitamin K) — use cautiously.”*

**Applied when:** patient is taking warfarin / Coumadin / anticoagulant

**Risk:** Structurally similar to vitamin K

**Restricts:**

- CoQ10 (high dose) — use with monitoring

### `CI-MED-4`

**Your words:** *“5-HTP, tryptophan, St. John's Wort, SAMe — can increase serotonin dangerously (especially with SSRIs/SNRIs).”*

**Applied when:** patient is taking SSRI / SNRI / tricyclic / MAOI / antidepressant

**Risk:** Can increase serotonin dangerously

**Restricts:**

- 5-HTP — **do not use**
- Tryptophan — **do not use**
- St. John's Wort — **do not use**
- SAMe — **do not use**

### `CI-MED-5`

**Your words:** *“Ginkgo, ginseng, high-dose curcumin — may increase bleeding risk or interact with liver enzymes.”*

**Applied when:** patient is taking SSRI / SNRI / tricyclic / MAOI / antidepressant

**Risk:** May increase bleeding risk or interact with liver enzymes

**Restricts:**

- Ginkgo — use with monitoring
- Ginseng — use with monitoring
- Curcumin (high dose) — use with monitoring

### `CI-MED-6`

**Your words:** *“Echinacea, astragalus, elderberry, medicinal mushrooms — contraindicated (stimulate immune system).”*

**Applied when:** patient is taking immunosuppressant / corticosteroid / methotrexate / biologic

**Risk:** Stimulate immune system

**Restricts:**

- Echinacea — **do not use**
- Astragalus — **do not use**
- Elderberry — **do not use**
- Medicinal mushrooms — **do not use**

### `CI-MED-7`

**Your words:** *“High-dose antioxidants — may counteract chemotherapy or immune suppression goals.”*

**Applied when:** patient is taking immunosuppressant / corticosteroid / methotrexate / biologic / chemotherapy

**Risk:** May counteract chemotherapy or immune suppression goals

**Restricts:**

- High-dose antioxidants — **do not use** _(Unless approved by the treating physician.)_

### `CI-MED-8`

**Your words:** *“Space iron, calcium, magnesium, and fiber supplements by 4 hours from thyroid medication.”*

**Applied when:** patient is taking levothyroxine / NDT / thyroid medication

**Risk:** Absorption interference

**Restricts:**

- Iron — separate the timing _(Space by 4 hours from thyroid medication.)_
- Calcium — separate the timing _(Space by 4 hours from thyroid medication.)_
- Magnesium — separate the timing _(Space by 4 hours from thyroid medication.)_
- Fiber supplements — separate the timing _(Space by 4 hours from thyroid medication.)_

### `CI-MED-9`

**Your words:** *“Ashwagandha, bladderwrack, licorice — use cautiously and only if thyroid antibodies are negative.”*

**Applied when:** patient has positive thyroid antibodies; or is taking levothyroxine / NDT / thyroid medication

**Risk:** Thyroid hormone modulation

**Restricts:**

- Ashwagandha — use with monitoring _(Only if thyroid antibodies are negative.)_
- Bladderwrack — use with monitoring _(Only if thyroid antibodies are negative.)_
- Licorice — use with monitoring _(Only if thyroid antibodies are negative.)_


## Pregnancy

### `CI-PREG-1`

**Your words:** *“Beta-carotene is safe alternative; keep total Vitamin A < 10,000 IU/day”*

**Applied when:** patient is pregnancy

**Risk:** Teratogenic risk

**Restricts:**

- Vitamin A (retinol) — **cap the dose** to total Vitamin A < 10,000 IU/day _(Beta-carotene is safe alternative.)_

### `CI-PREG-2`

**Your words:** *“May interfere with essential hormone signaling in early pregnancy”*

**Applied when:** patient is pregnancy

**Risk:** Hormonal detoxifiers; may interfere with essential hormone signaling in early pregnancy

**Restricts:**

- DIM — **do not use**
- Calcium-D-Glucarate — **do not use**

### `CI-PREG-3`

**Your words:** *“Contraindicated; may cause virilization or miscarriage”*

**Applied when:** patient is pregnancy

**Risk:** Hormone disruption

**Restricts:**

- DHEA (high dose) — **do not use**
- Testosterone — **do not use**
- Androgens — **do not use**

### `CI-PREG-4`

**Your words:** *“May affect uterine tone or hormone axis; avoid unless guided”*

**Applied when:** patient is pregnancy

**Risk:** Limited safety data; may affect uterine tone or hormone axis

**Restricts:**

- Ashwagandha — **do not use**
- Rhodiola — **do not use**
- Herbal adaptogens (high dose) — **do not use**

### `CI-PREG-5`

**Your words:** *“Use is not recommended in pregnancy”*

**Applied when:** patient is pregnancy

**Risk:** Uterine stimulant, potential toxicity

**Restricts:**

- Berberine — **do not use**

### `CI-PREG-6`

**Your words:** *“Mixed data; generally paused in first trimester”*

**Applied when:** patient is pregnancy

**Risk:** Mixed data

**Restricts:**

- NAC — **do not use** _(Generally paused in first trimester; resume later under clinical supervision if indicated.)_

### `CI-PREG-7`

**Your words:** *“Culinary doses safe; avoid extract/supplements unless targeted use later in pregnancy”*

**Applied when:** patient is pregnancy

**Risk:** Uterine stimulant at high doses

**Restricts:**

- Curcumin (turmeric extract, high dose) — **do not use** _(Culinary doses safe.)_

### `CI-PREG-8`

**Your words:** *“Avoid during pregnancy due to fetal toxin exposure risk”*

**Applied when:** patient is pregnancy

**Risk:** Mobilizes toxins; fetal toxin exposure risk

**Restricts:**

- Detox protocols — **do not use**
- Saunas — **do not use**
- Binders — **do not use**
- Lymphatic drainage herbs — **do not use**


## Children

### `CI-PED-1`

**Your words:** *“Avoid unless prescribed by pediatric-trained practitioner (exceptions: chamomile, elderberry in safe doses).”*

**Applied when:** patient is pediatric

**Risk:** Not established as safe for children

**Restricts:**

- Herbal supplements — **do not use** _(Unless prescribed by pediatric-trained practitioner. Exceptions: chamomile, elderberry in safe doses.)_

### `CI-PED-2`

**Your words:** *“Not studied for pediatric safety; use food-based stress support instead.”*

**Applied when:** patient is pediatric

**Risk:** Not studied for pediatric safety

**Restricts:**

- Ashwagandha — **do not use**
- Rhodiola — **do not use**
- Adaptogens — **do not use**

### `CI-PED-3`

**Your words:** *“Only under professional guidance; can unmask underlying issues if used too early or in wrong context.”*

**Applied when:** patient is pediatric

**Risk:** Can unmask underlying issues if used too early or in wrong context

**Restricts:**

- Glutathione — use with monitoring _(Only under professional guidance.)_
- NAC — use with monitoring _(Only under professional guidance.)_

### `CI-PED-4`

**Your words:** *“Only if labs support need (ferritin <30 or low Hb); iron can cause constipation or GI upset.”*

**Applied when:** patient is pediatric

**Risk:** Iron can cause constipation or GI upset

**Restricts:**

- Iron — **require labs first** _(Only if labs support need (ferritin <30 or low Hb).)_

### `CI-PED-5`

**Your words:** *“Always test 25(OH)D levels in long-term use; avoid >2000 IU without labs.”*

**Applied when:** patient is pediatric

**Risk:** Over-supplementation without lab confirmation

**Restricts:**

- Vitamin D — **cap the dose** to 2000 IU without labs _(Always test 25(OH)D levels in long-term use.)_

### `CI-PED-6`

**Your words:** *“Short-term use okay (0.5–2 mg); not for chronic use without addressing root cause of sleep issues.”*

**Applied when:** patient is pediatric

**Risk:** Chronic use masks root cause

**Restricts:**

- Melatonin — **cap the dose** to 0.5–2 mg, short-term only _(Not for chronic use without addressing root cause of sleep issues.)_

### `CI-PED-7`

**Your words:** *“Never ingest; safe topically when diluted (e.g., lavender for sleep, eucalyptus for congestion).”*

**Applied when:** patient is pediatric

**Risk:** Toxicity if ingested

**Restricts:**

- Essential oils (ingested) — **do not use** _(Safe topically when diluted.)_


## Elderly

### `CI-ELD-1`

**Your words:** *“Elderly: Reduced liver/kidney function—decrease fat-soluble vitamin doses and detox-related compounds (e.g., NAC, ALA).”*

**Applied when:** patient is elderly

**Risk:** Reduced liver/kidney function

**Restricts:**

- Fat-soluble vitamins — **cap the dose** _(Decrease dose.)_
- NAC — **cap the dose** _(Decrease dose.)_
- ALA — **cap the dose** _(Decrease dose.)_


## Immune modulation (from your immune priming doc)

### `CI-IMM-1`

**Your words:** *“Avoid immune boosters if autoimmunity suspected”*

**Applied when:** patient has autoimmune / suspected autoimmunity / MCAS / early autoimmune

**Risk:** Worsens immune overactivation

**Restricts:**

- Immune boosters — **do not use** _(Includes mushroom extracts, astragalus, colostrum used for priming.)_

### `CI-IMM-2`

**Your words:** *“Avoid dampening if immune exhaustion present”*

**Applied when:** patient has immune exhaustion / frequent infections / low WBC

**Risk:** Further suppresses an already underactive immune system

**Restricts:**

- Immune dampening agents — **do not use** _(Quercetin, curcumin, LDN, boswellia used for dampening.)_

### `CI-IMM-3`

**Your words:** *“Avoid in those with cow's milk protein allergy (CMPA).”*

**Applied when:** patient has cow's milk protein allergy / CMPA

**Risk:** Allergic reaction

**Restricts:**

- Colostrum — **do not use**


## Gut protocol cautions (from your 5R doc)

### `CI-GUT-1`

**Your words:** *“Betaine HCl with pepsin (if no gastritis, ulcers, or NSAID use).”*

**Applied when:** patient has gastritis / ulcers / active GI inflammation; or is taking NSAIDs

**Risk:** Aggravates gastritis and ulcers

**Restricts:**

- Betaine HCl with pepsin — **do not use**

### `CI-GUT-2`

**Your words:** *“Avoid if SIBO is active or suspected (test first).”*

**Applied when:** patient has SIBO / suspected SIBO

**Risk:** Feeds bacterial overgrowth

**Restricts:**

- Prebiotics (inulin, FOS, GOS, resistant starch) — **require labs first** _(Test for SIBO first.)_
- Fiber supplements — use with monitoring _(Introduce slowly to prevent bloating.)_


---

# Known gaps

Your handover curriculum covered several topics that have **no document** in the corpus —
they were taught verbally:

- Functional blood chemistry ranges
- Special populations scope (cancer, MS, Crohn's)
- Bowel resection, heavy medications, fibromyalgia
- Women's health interventions beyond the DUTCH material

For a case touching these, Lana flags *"not covered by your documents"* rather than
improvising from general medical knowledge. Closing a gap is a matter of adding the document,
not changing the rules.
