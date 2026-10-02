# Job search kit — remote AI engineering, ₹1L+/month

Built 2 Oct 2026. Target: offer signed before December. That means applications flowing
from this week, because a hiring cycle is 6–9 weeks *from the day you apply*, not from today.

---

## 1. WHAT YOU ARE SELLING

Not "I'm learning AI." This:

> **I build LLM systems that know when they're wrong.** Two projects with published
> evaluation harnesses, hand-labelled golden sets and documented failure taxonomies —
> including one where I audited my own benchmark, found it overstated, and rebuilt it.

Most candidates at this level show a RAG chatbot and a demo video. You have measured
accuracy, a failure taxonomy, and evidence of self-correction. **Lead with measurement every
time.** It is the rarest thing you have.

---

## 2. WHERE TO APPLY — in priority order

**Tier 1 — highest conversion for your profile**

| Platform | Why | Cadence |
|---|---|---|
| **Wellfound** (wellfound.com) | Startups, remote-first, founders read applications directly. Best signal-to-noise for portfolio-led candidates. | 5/day |
| **LinkedIn Jobs** | Volume plus the ability to find the hiring manager and message them. Filter: Remote + India + posted in last 7 days. | 5/day |
| **Y Combinator Work at a Startup** (workatastartup.com) | YC companies hire aggressively for AI roles and weigh projects heavily. Free profile, they come to you too. | profile + 3/week |
| **Instahyre** | Indian market, strong for product-engineering roles, recruiters actively source. | profile + 3/week |

**Tier 2 — worth setting up, lower daily effort**

- **Cutshort**, **Hirist** — Indian tech hiring, decent AI role volume
- **ai-jobs.net**, **RemoteOK**, **WeWorkRemotely** — remote-first boards, many international
- **Naukri** — high volume, lower quality, but Indian recruiters live there

**Tier 3 — the one most people skip and shouldn't**

Direct email to founders and engineering leads at 10–20 person AI startups. No ATS, no
keyword filter, a human reads it. Conversion is dramatically higher than job boards.
Find them via Wellfound company pages, YC's directory, LinkedIn.

---

## 3. SEARCH TERMS TO USE

Search these exact titles, not "AI":

```
AI Engineer              LLM Engineer             Applied AI Engineer
Generative AI Engineer   AI Software Engineer     Machine Learning Engineer (LLM)
AI Platform Engineer     Backend Engineer (AI)    Forward Deployed Engineer
AI Automation Engineer   Agentic AI Engineer      Solutions Engineer (AI)
```

**"Forward Deployed Engineer"** is underrated and underapplied-to. It means building AI
systems directly with customers, pays well, and values exactly what you have — shipping,
measuring, handling failure. Few candidates search for it.

**Do not filter yourself out by salary.** A posting at ₹1.5–2L will interview a strong
portfolio. Apply across the whole band.

---

## 4. THE APPLICATION NOTE

Most applications have a "message to hiring manager" box. Most people leave it blank.
Four sentences, under 100 words, specific to the company.

```
Hi [name],

I build LLM systems with measured reliability rather than demos. My most recent project
monitors federal regulations and determines which clauses of a company's internal policy
library each new rule affects — with a validator that makes fabricated legal citations
structurally impossible, and an evaluation harness I audited and rebuilt after finding it
overstated its own accuracy.

[One sentence on what THIS company does and why it interests you — specific, not flattery.]

Both projects are public: github.com/Ananyapkumar

— Ananya
```

**Rules:**
- Rewrite the middle sentence for every company. Generic notes read as spam.
- Never write "I am passionate about AI."
- Never attach a cover letter. Nobody reads them.

---

## 5. COLD OUTREACH TO A HIRING MANAGER

Send after applying, or instead of applying when there's no posting. LinkedIn DM or email.
Under 120 words. No attachments on first contact.

```
Subject: AI engineer — built a regulatory-compliance agent, 94 tests, measured evals

Hi [name],

I saw [specific thing — a post they wrote, something the company shipped, a role you
applied for].

I've spent the last two months building LLM systems that are measured rather than
demonstrated. The most recent one monitors regulators, maps new rules onto a company's
internal policy clauses, and refuses to answer when its confidence is insufficient instead
of guessing. I wrote the evaluation harness myself, then audited it and found my own
benchmark was overstating accuracy — so I rebuilt the test set from real regulatory text.

Repos: github.com/Ananyapkumar

Would a 15-minute conversation be useful? Happy to walk through the architecture.

Ananya
```

**Why this works:** it names a specific engineering judgement in the first paragraph, and
the self-audit line does the work. It is the sentence a senior engineer stops on.

---

## 6. THE REFERRAL ASK — highest conversion of all

Referred candidates convert several times better than cold applicants. Message anyone
already at a company you've applied to, even a stranger.

```
Hi [name] — I applied for the [role] at [company] last week and wondered if you'd be
willing to refer me internally.

Short version: I build LLM systems with real evaluation. Two public projects, one deployed,
both with measured accuracy and documented failure modes: github.com/Ananyapkumar

Completely understand if you'd rather not refer someone you haven't worked with. Either way,
thanks for reading.
```

The last line matters. It removes the social cost of saying no, which makes yes more likely.

---

## 7. LINKEDIN — fix these three fields today

**Headline** (this is what appears in every search result):
```
AI Engineer | LLM systems with measured reliability — evals, RAG, agentic workflows | Python · FastAPI · Postgres
```

**About** — first two lines are all anyone reads before "see more":
```
I build LLM systems that know when they're wrong.

Most AI projects demonstrate capability. Mine measure it. Both of my recent projects ship
with an evaluation harness, a hand-labelled test set and a documented failure taxonomy —
including one where I audited my own benchmark, found it was overstating accuracy, and
rebuilt the test set from scratch.

Recent work:
• Redline — an unattended agent that monitors federal regulators and maps new obligations
  onto a company's internal policy clauses. Citation grounding makes fabricated legal
  references structurally impossible; low-confidence cases route to human review instead of
  being guessed at. 94 tests.
• Clause — structured extraction from product datasheets. 99.4% field accuracy across 162
  judgements, with variance analysis and a pre-registered prediction that matched. Deployed.

Python · FastAPI · Postgres/pgvector · LLM APIs · RAG · evaluation · Docker

Open to remote AI engineering roles. github.com/Ananyapkumar
```

**Open to work** — switch it on, recruiters-only if you're employed.

---

## 8. DAILY CADENCE — non-negotiable from today

| | |
|---|---|
| **5** | applications, each with a rewritten application note |
| **3** | cold outreach messages to hiring managers or engineers |
| **1** | referral ask, if you applied anywhere with a connection |

That's **45 minutes a day.** Over the next 4 weeks: ~100 applications, ~60 outreach messages.

**Expected conversion, so you aren't discouraged by normal numbers:**
- 100 applications → 10–20 replies → 5–10 first conversations → 2–4 later-stage → 1–2 offers
- Cold outreach converts better: 60 messages → 6–12 replies → 3–6 conversations

**If 30 applications produce zero replies, the message is wrong, not the market.** Bring me
the numbers and we rewrite. Do not simply send more of something that isn't working.

---

## 9. TRACK IT

One spreadsheet. Six columns. Nothing fancier.

```
Date | Company | Role | Channel | Status | Next action
```

Review every Sunday: how many sent, how many replied, which channel converts. The weekly
number is what tells us whether to change the message, the targeting, or neither.

---

## 10. WHAT NOT TO DO

- **Don't wait for Redline to be finished.** Clause is already enough to apply with. Redline
  becomes a second touch — "I've since shipped this" — which is more effective than a perfect
  first application sent three weeks later.
- **Don't do more certifications.** Nobody in this pipeline will ask.
- **Don't start a third project** before you have interviews. Two deep projects beat three
  shallow ones, and you need pipeline, not portfolio.
- **Don't apply only to roles you feel 100% qualified for.** Apply at 60%.
- **Don't send the same note twice.** The rewritten middle sentence is the whole point.
