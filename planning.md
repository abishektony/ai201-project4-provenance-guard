# Planning Specification - Provenance Guard

## Architecture

```text

        +-------------------+
        |   POST /submit    |
        +---------+---------+
                  |
                  v
   +--------------+--------------+
   |   Rate Limiter Activation   |
   +--------------+--------------+
                  |
                  v
    +-------------+-------------+
    | Multi-Signal Pipeline     |
    | - Heuristics (TTR)        |
    | - Groq LLM Classifier     |
    +-------------+-------------+
                  |
                  v
   +--------------+--------------+
   | Continuous Confidence Engine |
   +--------------+--------------+
                  |
                  v
   +--------------+--------------+
   | Structural Transparency UX   |
   +--------------+--------------+
                  |
                  v
   +--------------+--------------+
   | In-Memory SQLite/JSON Audit |
   +-----------------------------+
```

A submission text flows directly through rate-limiting middleware into the multi-signal computation pipeline. The blended metrics pass through an uncertainty scoring calculator which selects the descriptive transparency text, indexes it inside an internal database log, and updates dynamically upon tracking a `POST /appeal` payload context request.

## Detection Signals
* **Signal 1: Stylometric Structural Heuristic:** Evaluates Type-Token Ratio vocabulary density markers. *Blind Spot:* Short micro-fiction or specialized, deliberately repetitive poetry can falsely spike AI markers due to lower statistical unique text arrays.
* **Signal 2: Groq Holistic Inference:** Evaluates syntax predictability using `llama-3.3-70b-versatile`. *Blind Spot:* Highly formal or technical human legal/academic research writing is frequently misclassified as automated text.

**Combination (spec):** Each signal outputs a float in `[0, 1]` where `1.0 = AI`.
Blend into a single `combined_score = 0.40·stylometric + 0.60·llm`.
> *Implementation note (post-testing): re-weighted to `0.25 / 0.75` — see README "Spec Reflection". The LLM proved far more reliable than the TTR heuristic on formal text.*

## Uncertainty Representation
* `Score > 0.60`: Categorized as `likely_ai`. 
* `Score < 0.40`: Categorized as `likely_human`.
* `0.40 <= Score <= 0.60`: Flags an immediate `uncertain` verdict. This range shifts boundaries safely into a protected gray zone to avoid falsely penalizing real human creators.

## Transparency Label Design
* **High Confidence Human:** `"Authenticated Creative Human Work: This prose presents distinctive diversity and structural variation matching human craft."`
* **High Confidence AI:** `"Automated Signature Identified: This entry demonstrates structural uniformity typical of AI generation."`
* **Uncertain Boundary:** `"Mixed Markers Detected: This submission exhibits blending styles. Content origin cannot be definitively verified automatically."`

## Appeals Workflow
Any author whose document drops inside database history logs can execute an appeal. When a valid request hits the server, the structural verification pipeline updates the item tracking state parameter to `"under_review"`. Human administrative audit logs capture this reason immediately for manual prioritization queues.

## Anticipated Edge Cases
1.  **Repetitive Minimalist Poetry:** Short human poems using looping conceptual refrains present lower overall vocabulary diversity, easily tripping up stylometric analysis.
2.  **Non-Native English Formal Prose:** Human writers speaking English as a second language often apply highly regularized, grammatically precise phrasing patterns that look identical to an LLM's default structural templates.

## AI Tool Plan
* **M3:** Generate base Flask framing structures along with basic evaluation route containers. Verify endpoints using local `curl` requests.
* **M4:** Merge text token matrix functions alongside composite metric weighing equations. Verify with distinct testing criteria text inputs.
* **M5:** Implement conditional transparency text formatting strings and state-altering appeal endpoints. Confirm error handles act securely.

## Stretch Features (planned before implementation)

### Stretch 1 — Ensemble Detection (3+ signals, weighted)
Add a **third signal** so the pipeline is a true ensemble, and document the weighting.
* **Signal 3 — Burstiness (sentence-length variance):** measures how much sentence
  length varies across the text. Human writing is "bursty" — it mixes long and short
  sentences; AI text trends toward uniform sentence lengths. *Output:* `[0, 1]` where
  low variance → high AI score. *Blind spot:* very short inputs (1–2 sentences) give an
  unstable variance estimate, so it abstains toward 0.5 below a minimum sentence count.
* **Weighting (voting by weighted average), to be tuned empirically:**
  `combined = 0.60·llm + 0.25·burstiness + 0.15·stylometric`. The LLM stays dominant;
  burstiness outranks the weak TTR heuristic. Verify the 4-input battery still separates
  AI from human, and that the third signal moves at least one borderline case.

### Stretch 2 — Analytics Dashboard
A read-only view computed from the audit log, exposed as `GET /analytics` (JSON) and a
human-friendly `GET /dashboard` (HTML).
* **Detection patterns:** count + percentage of submissions per verdict (`likely_ai`,
  `likely_human`, `uncertain`).
* **Appeal rate:** `appeals_filed / total_submissions`.
* **Additional metric:** average confidence overall, plus average per-signal scores, to
  surface whether one signal is consistently dominating.
* No new storage — derived entirely from the existing in-memory `AUDIT_LOG`.