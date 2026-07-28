# Treatment Plan Template (output format)

> Source: `_Treatment Plan Phase__DATE_Alexa_NAME.docx` (Alexandra Daccache, SEED Health and Wellness
> Centre), cross-checked against `_Treatment Plan with SEED prodcuts.docx`.
>
> **This file defines the shape of the document Lana produces.** The `BOILERPLATE-*` blocks below are
> extracted by `PlanKnowledge.template_boilerplate()` and copied **byte-identical** into every
> rendered draft by `PlanManager.render_markdown()`. **No model ever generates this text** — that is
> deliberate, so a drafting model cannot reword, soften, or drop her standing patient instructions.
> Editing a boilerplate block here changes every future draft.

## Document structure

Sections in order, matching her Word template:

1. **Header** — `Patient:` / `Evaluation Date:` / `Practitioner: Alexa`
2. **Nutritional Recommendations Follow-Up**
3. **Goals & Timeline** — "Approximately X Months in Total, divided into X phases", then numbered goals
4. **Supplement Recommendations (N WEEKS)** — the main table (columns below)
5. **KEEP** — a second table with identical columns, listing supplements the patient continues
6. **Food Therapy (X WEEKS)** — table
7. **Lifestyle Therapy (X WEEKS)** — table
8. **Summary of Findings & Treatment** — diagnosis, findings, and which test(s) they came from
9. **General Recommendations** — fixed boilerplate (below)
10. **Next Appointment** — booking instruction
11. **TESTING:** — `LAB` / `FUNCTIONAL` checklist
12. **The Process** — fixed boilerplate (below)

### Supplement Recommendations table columns

Exactly these eleven, in this order:

| Supplement | Purpose | Frequency | Upon waking | With breakfast | Mid Morning | With Lunch | Mid Afternoon | With Dinner | Before Bed | Comments |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |

The seven timing columns hold a count (e.g. `1`, `2`) or are left blank. `Comments` commonly carries
availability ("Available at SEED") and administration notes ("1 ampoule upon waking up. Keep in mouth
for 30 sec before swallowing.", "1 teaspoon to be mixed with water. On empty stomach.").

### Food Therapy table columns

| Food | Purpose | Frequency | Comments |
| :-: | :-: | :-: | :-: |

### Lifestyle Therapy table columns

| Lifestyle | Purpose | Frequency | Comments |
| :-: | :-: | :-: | :-: |

Examples she uses: Sisal Brush (dry brushing — circulation, lymph flow, unclogs pores), Castor Oil
Packs (digestive health, liver detox, reduced inflammation, circulation, pain relief, stress
reduction), Enema Kit (detoxifies and cleanses the colon).

## Fixed boilerplate blocks

### General Recommendations

<!-- BOILERPLATE-START: general_recommendations -->
General Recommendations:

ONLY INTRODUCE A NEW SUPPLEMENT AFTER THREE DAYS OF BEING SYMPTOM FREE FROM THE LAST ONE – Always start slow.

Recommended supplements are a pathway to improvement and not a permanent solution.

All elimination diets are designed for limited periods to help your healing. Removing large groups of food for long periods of time is not recommended.
<!-- BOILERPLATE-END: general_recommendations -->

### The Process

<!-- BOILERPLATE-START: the_process -->
The Process:

At Seed Health and Wellness Centre, we help you learn how to take better care of yourself. This plan is designed to help you get from Point A to Point B.

The plan is designed to be adaptable, with phases, based on your symptoms throughout the protocol. You will have a set number of phase with a specific duration each, which will be estimated after receiving your test results and will also be based on your symptoms and progress. Changes in how you feel are natural and tracking symptoms is important so that we can adapt accordingly.

We are here to support you throughout your plan and are available to answer any questions that may come up.

You can reach me via +974 7784 7085 or alexa@seed.qa
<!-- BOILERPLATE-END: the_process -->

### Supplement ordering note

Used when the plan references Biogena products the patient orders themselves rather than collecting at SEED.

<!-- BOILERPLATE-START: ordering_note -->
Kindly note that the names of the supplements noted in the table below are clickable links. In order to purchase them from Amrita you will need to create an account using the following invite code:

Invite Code: B35C44965F38

Please note that if you are finding difficult to order your supplements, we can assist you with the latter for an extra charge.

For that service, Proceed with contacting +974 7784 5112
<!-- BOILERPLATE-END: ordering_note -->

### Next Appointment

Two variants appear in her templates; the drafting model picks whichever the case calls for, or
Alexandra edits it.

<!-- BOILERPLATE-START: next_appointment_after_tests -->
Next Appointment: Kindly book an appointment 4 weeks after you submit the test samples (book ahead of the day of submission)
<!-- BOILERPLATE-END: next_appointment_after_tests -->

<!-- BOILERPLATE-START: next_appointment_results_pending -->
Next Appointment: We will contact you once your results are out. / Kindly book a consultation during the 5th week of your treatment plan.
<!-- BOILERPLATE-END: next_appointment_results_pending -->

## Phrasing notes

Transitional lines she writes between phases, for tone reference:

> "We will reintroduce foods and continue to support your nervous system so your digestion and adrenal
> systems can stay balanced."

Plans are addressed to the patient in second person ("your"), warm and plain — not clinical shorthand.
