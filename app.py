import os
import re
import uuid
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from groq import Groq

# Load environment configuration
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("provenance_guard")

app = Flask(__name__)

# Initialize rate limiter with local in-memory storage
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)

# Initialize Groq client
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
if not groq_client:
    logger.warning(
        "GROQ_API_KEY not set: Signal 2 (LLM) will fall back to a neutral 0.5. "
        "Set the key in .env to enable real multi-signal scoring."
    )

# In-memory structured datastore for audit logging and state tracking
AUDIT_LOG = {}

def compute_stylometric_score(text: str) -> float:
    """
    Signal 1: Stylometric Heuristics (Pure Python structural analysis)
    Measures vocabulary diversity using a Type-Token Ratio (TTR) variant.
    AI text patterns are typically highly uniform, yielding values closer to 1.0, 
    whereas human creative prose shows high variability and complexity.
    """
    words = [w.strip(".,!?\"()").lower() for w in text.split() if w.strip(".,!?\"()")]
    if not words:
        return 0.5
    
    unique_words = set(words)
    ttr = len(unique_words) / len(words)
    
    # Map raw TTR to an AI likelihood metric [0.0 = Human, 1.0 = AI]
    # If vocabulary diversity is lower than 60%, tilt probability toward AI generation patterns
    if ttr < 0.60:
        return min(1.0, 0.5 + (0.60 - ttr) * 2)
    else:
        return max(0.0, 0.5 - (ttr - 0.60) * 1.2)

def compute_burstiness_score(text: str):
    """
    Signal 3: Burstiness (sentence-length variance) — pure Python.
    Human writing is "bursty": it mixes long and short sentences. AI prose trends
    toward uniform sentence lengths. We measure the coefficient of variation of
    sentence word-counts; low variation -> high AI score.
    Output [0.0 = Human, 1.0 = AI], or None to ABSTAIN on very short inputs (< 4
    sentences) where the variance estimate is unstable. Abstaining (rather than
    voting 0.5) lets the ensemble redistribute this signal's weight instead of
    diluting a confident verdict toward the middle.
    """
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sentences) < 4:
        return None  # too few sentences to judge variance reliably -> abstain

    lengths = [len(s.split()) for s in sentences]
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return None
    variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
    cv = (variance ** 0.5) / mean  # coefficient of variation (scale-independent)

    # Empirically, human prose lands around cv >= 0.5; uniform AI text sits lower.
    # Map cv linearly: cv 0.0 -> 1.0 (AI), cv >= 0.5 -> 0.0 (human).
    return max(0.0, min(1.0, 1.0 - (cv / 0.5)))

def compute_llm_score(text: str) -> float:
    """
    Signal 2: Semantic and Holistic LLM Verification (Groq Llama 3.3)
    Analyzes systemic phrases, syntax predictability, and formatting markers.
    """
    if not groq_client:
        # Graceful degradation to baseline fallback if key is missing
        return 0.5

    try:
        prompt = (
            "Analyze the following text sample to evaluate the probability that it was generated "
            "by an automated language model versus written by a human. Respond with exactly one decimal number "
            "between 0.00 and 1.00, where 1.00 signifies an absolute high-confidence AI pattern and 0.00 represents "
            "a purely human structure. Do not include any conversational text or explanation.\n\n"
            f"Text Sample:\n{text}"
        )
        
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10
        )
        
        response_text = completion.choices[0].message.content.strip()
        # Extract the first decimal even if the model adds stray words/punctuation.
        match = re.search(r"[01](?:\.\d+)?|0?\.\d+", response_text)
        if not match:
            logger.warning("LLM returned no parseable number (%r); falling back to 0.5", response_text)
            return 0.5
        return max(0.0, min(1.0, float(match.group())))
    except Exception as exc:
        # Degrade gracefully, but never silently — a 401/timeout must be visible.
        logger.warning("Groq call failed (%s: %s); falling back to 0.5", type(exc).__name__, exc)
        return 0.5

def generate_transparency_label(confidence: float, attribution: str) -> str:
    """Maps continuous confidence outputs onto strict descriptive strings for end users."""
    if attribution == "uncertain":
        return "Mixed Markers Detected: This submission exhibits blending styles. Content origin cannot be definitively verified automatically."
    elif attribution == "likely_ai":
        return f"Automated Signature Identified: This entry demonstrates structural uniformity typical of AI generation (Confidence: {confidence:.0%})."
    else:
        return f"Authenticated Creative Human Work: This prose presents distinctive diversity and structural variation matching human craft (Confidence: {confidence:.0%})."

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute; 100 per day")
def submit_content():
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    creator_id = data.get("creator_id", "").strip()

    if not text or not creator_id:
        return jsonify({"error": "Missing mandatory parameters: 'text' and 'creator_id'"}), 400

    # Execute the 3-signal ensemble pipeline.
    stylometrics = compute_stylometric_score(text)
    llm_analysis = compute_llm_score(text)
    burstiness = compute_burstiness_score(text)  # may be None (abstained)

    # Ensemble: weighted-average vote. 0.0 = strongly human, 1.0 = strongly AI.
    # The LLM leads (0.60) because it is by far the most reliable signal. Burstiness
    # (0.25) outranks the TTR heuristic (0.15): empirical testing showed TTR is weak
    # and sometimes inverted on formal text (polished AI prose uses varied vocabulary,
    # so TTR reads it as "human"), whereas sentence-length variance is a sturdier
    # structural cue. If a signal abstains (burstiness on short text), its weight is
    # redistributed across the remaining signals rather than diluting the vote.
    votes = [(llm_analysis, 0.60), (stylometrics, 0.15)]
    if burstiness is not None:
        votes.append((burstiness, 0.25))
    total_weight = sum(w for _, w in votes)
    combined_score = sum(score * w for score, w in votes) / total_weight

    # Confidence is decoupled from attribution: it measures how far the signals
    # are from a 0.5 coin-flip, scaled to [0, 1]. A score of 0.5 yields confidence
    # 0 (maximally uncertain); a score of 0.0 or 1.0 yields confidence 1.0 (signals
    # strongly agree). This means an "uncertain" verdict always carries LOW confidence.
    confidence = round(abs(combined_score - 0.50) * 2, 3)

    # Tiered thresholds isolate the protected gray zone from the two verdicts.
    if combined_score > 0.60:
        attribution = "likely_ai"
    elif combined_score < 0.40:
        attribution = "likely_human"
    else:
        attribution = "uncertain"

    content_id = str(uuid.uuid4())
    label_text = generate_transparency_label(confidence, attribution)

    # Write highly structured entry to the Audit Log
    log_entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attribution": attribution,
        "confidence": round(confidence, 3),
        "signals": {
            "stylometric_score": round(stylometrics, 3),
            "llm_score": round(llm_analysis, 3),
            "burstiness_score": round(burstiness, 3) if burstiness is not None else None
        },
        "status": "classified",
        "appeal_reasoning": None
    }
    
    AUDIT_LOG[content_id] = log_entry

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": round(confidence, 3),
        "transparency_label": label_text
    }), 200

@app.route("/appeal", methods=["POST"])
def appeal_classification():
    data = request.get_json() or {}
    content_id = data.get("content_id", "").strip()
    reasoning = data.get("creator_reasoning", "").strip()

    if not content_id or not reasoning:
        return jsonify({"error": "Missing mandatory field records: 'content_id' and 'creator_reasoning'"}), 400

    if content_id not in AUDIT_LOG:
        return jsonify({"error": "Target content record identifier not discovered in active database"}), 404

    # Atomic record state shift updating status securely
    AUDIT_LOG[content_id]["status"] = "under_review"
    AUDIT_LOG[content_id]["appeal_reasoning"] = reasoning

    return jsonify({
        "message": "Appeal logged successfully. Document state modified to 'under_review'.",
        "content_id": content_id,
        "current_status": "under_review"
    }), 200

@app.route("/log", methods=["GET"])
def retrieve_audit_logs():
    # Return structured entry arrays directly for validation checks
    return jsonify({"entries": list(AUDIT_LOG.values())}), 200

def compute_analytics() -> dict:
    """Derive dashboard metrics from the in-memory audit log (no extra storage)."""
    entries = list(AUDIT_LOG.values())
    total = len(entries)

    verdicts = {"likely_ai": 0, "likely_human": 0, "uncertain": 0}
    appeals = 0
    conf_sum = 0.0
    signal_sums = {"stylometric_score": 0.0, "llm_score": 0.0, "burstiness_score": 0.0}
    signal_counts = {"stylometric_score": 0, "llm_score": 0, "burstiness_score": 0}

    for e in entries:
        verdicts[e["attribution"]] = verdicts.get(e["attribution"], 0) + 1
        if e.get("status") == "under_review":
            appeals += 1
        conf_sum += e.get("confidence", 0.0)
        for k in signal_sums:
            val = e.get("signals", {}).get(k)
            if val is not None:  # skip abstained signals (e.g. burstiness on short text)
                signal_sums[k] += val
                signal_counts[k] += 1

    def pct(n):
        return round(100 * n / total, 1) if total else 0.0

    return {
        "total_submissions": total,
        "detection_patterns": {
            v: {"count": c, "percent": pct(c)} for v, c in verdicts.items()
        },
        "appeals_filed": appeals,
        "appeal_rate_percent": pct(appeals),
        "average_confidence": round(conf_sum / total, 3) if total else 0.0,
        "average_signal_scores": {
            k: round(signal_sums[k] / signal_counts[k], 3) if signal_counts[k] else None
            for k in signal_sums
        },
    }

@app.route("/analytics", methods=["GET"])
def analytics_json():
    return jsonify(compute_analytics()), 200

DASHBOARD_HTML = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Provenance Guard — Analytics</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 720px; margin: 40px auto; color: #1a1a1a; }
  h1 { font-size: 1.4rem; } h2 { font-size: 1rem; color: #555; margin-top: 28px; }
  .cards { display: flex; gap: 12px; flex-wrap: wrap; }
  .card { flex: 1; min-width: 140px; background: #f4f5f7; border-radius: 10px; padding: 16px; }
  .num { font-size: 1.8rem; font-weight: 700; } .lbl { color: #666; font-size: .85rem; }
  .bar { height: 22px; border-radius: 5px; margin: 4px 0; color: #fff; font-size: .8rem;
         padding: 2px 8px; line-height: 22px; white-space: nowrap; }
  .ai { background: #c0392b; } .human { background: #27ae60; } .unc { background: #f39c12; }
  table { border-collapse: collapse; width: 100%; margin-top: 6px; }
  td, th { text-align: left; padding: 6px 10px; border-bottom: 1px solid #eee; font-size: .9rem; }
</style></head><body>
  <h1>🛡️ Provenance Guard — Analytics</h1>
  <div class="cards">
    <div class="card"><div class="num">{{ a.total_submissions }}</div><div class="lbl">submissions</div></div>
    <div class="card"><div class="num">{{ a.appeal_rate_percent }}%</div><div class="lbl">appeal rate ({{ a.appeals_filed }})</div></div>
    <div class="card"><div class="num">{{ a.average_confidence }}</div><div class="lbl">avg confidence</div></div>
  </div>
  <h2>Detection patterns</h2>
  {% set dp = a.detection_patterns %}
  <div class="bar ai" style="width: {{ [dp.likely_ai.percent, 4]|max }}%">AI {{ dp.likely_ai.count }} ({{ dp.likely_ai.percent }}%)</div>
  <div class="bar human" style="width: {{ [dp.likely_human.percent, 4]|max }}%">Human {{ dp.likely_human.count }} ({{ dp.likely_human.percent }}%)</div>
  <div class="bar unc" style="width: {{ [dp.uncertain.percent, 4]|max }}%">Uncertain {{ dp.uncertain.count }} ({{ dp.uncertain.percent }}%)</div>
  <h2>Average signal scores (0 = human, 1 = AI)</h2>
  <table>
    <tr><th>Signal</th><th>Average</th></tr>
    <tr><td>LLM (Groq)</td><td>{{ a.average_signal_scores.llm_score }}</td></tr>
    <tr><td>Burstiness</td><td>{{ a.average_signal_scores.burstiness_score if a.average_signal_scores.burstiness_score is not none else '— (abstained)' }}</td></tr>
    <tr><td>Stylometric (TTR)</td><td>{{ a.average_signal_scores.stylometric_score }}</td></tr>
  </table>
</body></html>
"""

@app.route("/dashboard", methods=["GET"])
def analytics_dashboard():
    return render_template_string(DASHBOARD_HTML, a=compute_analytics()), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)