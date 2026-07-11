# 08 · Worked Use Cases

Concrete end-to-end examples showing how the concepts compose. Each is expressed as a
workflow of steps; the runnable [`reference/`](../reference/) scaffold implements a
simplified version of the first one.

---

## 1. Invoice triage & AP entry (Finance)

**Trigger:** email received at `invoices@acme.com`.

```
1. extract   (agent, Sonnet)  → OCR + parse invoice → {vendor, number, amount, currency, due, line_items}
2. validate  (tool)           → lookup_vendor: known? matches PO? totals add up?
3. dedupe    (tool)           → has this invoice number already been posted?
4. branch                     → amount > $5,000  → step 5 (approval)  else → step 6
5. approval  (finance-team)   → human confirms; timeout 24h → escalate
6. post      (tool)           → create_ap_entry in the accounting system (idempotent)
7. notify    (tool)           → reply to sender + post summary in #ap channel
```
**Value:** removes manual data entry, catches duplicates/mismatches, keeps humans in
the loop only for large amounts.

---

## 2. IT / HR helpdesk auto-resolution (Internal Ops)

**Trigger:** message in the `#it-help` Slack channel or a new ticket.

```
1. classify (agent, Haiku)    → category: {password, access-request, how-to, hardware, other}
2. router   (branch)          → route by category
3a. how-to  (agent, Sonnet)   → RAG over internal docs → draft answer → post (read-only, no approval)
3b. access  (agent + approval)→ propose granting access → manager approval → provisioning tool
3c. other   (tool)            → create ticket, assign to human, summarize context
4. feedback (tool)            → 👍/👎 reaction captured as an eval signal
```
**Value:** deflects common requests instantly; sensitive actions (access grants) still
require approval; every answer is grounded in company docs.

---

## 3. Sales lead enrichment & routing (Revenue)

**Trigger:** new lead in the CRM / form submission.

```
1. enrich   (agent + tools)   → look up company size, industry, tech stack from allowed sources
2. score    (agent, Sonnet)   → fit score against ICP rubric
3. branch                     → hot → assign to AE + draft outreach;  cold → nurture sequence
4. update   (tool)            → write enrichment + score + owner back to CRM (idempotent)
5. draft    (agent)           → personalized first-touch email → queued for AE review (approval)
```
**Value:** every lead enriched and routed in seconds; reps get a ready-to-edit draft
instead of a blank page.

---

## 4. Contract review assist (Legal)

**Trigger:** document uploaded to the "Contracts – Intake" folder.

```
1. extract  (agent, Opus)     → identify parties, term, liability cap, auto-renewal, governing law
2. checklist(agent, Opus)     → compare against the company's standard playbook → flag deviations
3. summary  (agent)           → plain-English risk summary with clause citations
4. route    (tool + approval) → send summary + flags to legal; lawyer approves/edits
5. log      (tool)            → record outcome to the contract register
```
**Value:** first-pass review in minutes with citations; a lawyer always makes the call.
High-stakes reasoning uses the strongest model.

---

## 5. Weekly business report (Analytics)

**Trigger:** schedule — Mondays 07:00.

```
1. query    (tool)            → pull KPIs from the warehouse (read-only)
2. analyze  (agent, Sonnet)   → compute deltas, spot anomalies, explain drivers
3. narrate  (agent)           → write an exec-ready narrative + chart specs
4. render   (tool)            → build the deck/dashboard
5. deliver  (tool)            → post to the leadership channel + email
```
**Value:** a consistent, on-time report; humans spend time on decisions, not assembly.

---

## Pattern summary

Across all five, the same building blocks recur:

- **Read-only steps run freely; mutating/high-stakes steps gate on policy or approval.**
- **Model tier is chosen per step** — Haiku to classify, Sonnet to draft, Opus to reason over legal/financial risk.
- **RAG grounds answers** in company knowledge.
- **Idempotent writes** make retries safe.
- **Every run is traced** for audit and improvement.

This is the point of the platform: dozens of processes, one consistent, safe, and
observable execution model.
