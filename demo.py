"""
Provenance Guard — end-to-end demo / walkthrough driver.

Starts the Flask server, exercises every feature in order, prints clean output,
then shuts the server down. Use this to verify the system runs and as the live
demo for your portfolio walkthrough video.

    uv run python demo.py        (or: python demo.py)

It is self-contained: it launches app.py itself, so you do NOT need a second
terminal running the server.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# The transparency labels contain emoji; force UTF-8 so Windows consoles
# (which default to cp1252) don't crash on them.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://localhost:5000"


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    def parse(raw):
        try:
            return json.loads(raw.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None  # e.g. Flask-Limiter returns an HTML body on 429
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, parse(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, parse(e.read())


def banner(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def show_submission(label, text, creator_id):
    print(f"\n--- {label} ---")
    print(f'  text: "{text[:70]}..."')
    _, body = call("POST", "/submit", {"text": text, "creator_id": creator_id})
    cid = body["content_id"]
    _, log = call("GET", "/log")
    sig = next(e for e in log["entries"] if e["content_id"] == cid)["signals"]
    burst = "abstained" if sig["burstiness_score"] is None else sig["burstiness_score"]
    print(f"  -> signals     : llm={sig['llm_score']}  burstiness={burst}  ttr={sig['stylometric_score']}")
    print(f"  -> attribution : {body['attribution']}")
    print(f"  -> confidence  : {body['confidence']}")
    print(f"  -> label       : {body['transparency_label']}")
    return cid


def wait_for_server(timeout=20):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            call("GET", "/log")
            return True
        except Exception:
            time.sleep(0.5)
    return False


SAMPLES = {
    "1. CLEARLY AI (formal, templated)":
        ("Artificial intelligence represents a transformative paradigm shift in modern "
         "society. It is important to note that while the benefits of AI are numerous, it "
         "is equally essential to consider the ethical implications. Furthermore, "
         "stakeholders across various sectors must collaborate to ensure responsible "
         "deployment.", "demo-ai"),
    "2. CLEARLY HUMAN (casual, irregular)":
        ("ok so i finally tried that new ramen place downtown and honestly? underwhelming. "
         "the broth was fine but they put WAY too much sodium in it and i was thirsty for "
         "like three hours after. probably wont go back unless someone drags me there",
         "demo-human"),
    "3. BORDERLINE (formal human / academic)":
        ("The relationship between monetary policy and asset price inflation has been "
         "extensively studied in the literature. Central banks face a fundamental tension "
         "between their mandate for price stability and the unintended consequences of "
         "prolonged low interest rates on equity and real estate valuations.", "demo-formal"),
    "4. SIGNAL CONFLICT -> uncertain (LLM says AI, burstiness disagrees)":
        ("Climate change is a pressing issue. We must act now. Future generations depend "
         "on the choices we make today. Everyone has a role to play in protecting the "
         "planet.", "demo-conflict"),
    "5. LONG UNIFORM AI (burstiness signal fires)":
        ("The system processes data efficiently. The architecture supports many users. "
         "The framework handles requests reliably. The platform scales across regions. "
         "The service maintains high availability. The pipeline ensures consistent results.",
         "demo-uniform"),
}


def main():
    # Refuse to run against a stale/other server already on the port.
    try:
        call("GET", "/log")
        print("ERROR: something is already listening on localhost:5000. Stop it first")
        print("       (this demo starts its own server) and re-run.")
        return
    except Exception:
        pass

    print("Starting Flask server...")
    # Launch WITHOUT Flask's debug reloader: the reloader spawns a child process
    # that survives terminate() and would leak the port between runs.
    server = subprocess.Popen(
        [sys.executable, "-c",
         "from app import app; app.run(host='127.0.0.1', port=5000, debug=False)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_server():
            print("ERROR: server did not start. Run `python app.py` manually to see why.")
            return

        banner("MILESTONE 4 + ENSEMBLE — 3-signal scoring across 5 inputs")
        first_id = None
        for label, (text, cid) in SAMPLES.items():
            content_id = show_submission(label, text, cid)
            if first_id is None:
                first_id = content_id

        banner("MILESTONE 5 — appeals workflow")
        print(f"\nAppealing the first submission (content_id={first_id})...")
        status, body = call("POST", "/appeal", {
            "content_id": first_id,
            "creator_reasoning": "I wrote this myself; my academic style is naturally formal.",
        })
        print(f"  -> [{status}] {body['message']}")
        print("  Re-fetching it from the log to confirm the status changed:")
        _, log = call("GET", "/log")
        entry = next(e for e in log["entries"] if e["content_id"] == first_id)
        print(f"     status         : {entry['status']}")
        print(f"     appeal_reasoning: {entry['appeal_reasoning']}")

        banner("MILESTONE 5 — rate limiting (limit: 10/min)")
        print("\nNote: the 10/min budget is shared across ALL /submit calls this minute,")
        print("so the submissions above already used some of it. Watch for 429s once the")
        print("limit is hit (firing 12 rapid requests):")
        codes = []
        for i in range(1, 13):
            status, _ = call("POST", "/submit",
                             {"text": "rate limit test payload.", "creator_id": "rl-test"})
            codes.append(status)
            print(f"  request {i:>2} -> {status}")
        print(f"  summary: {codes.count(200)} accepted, {codes.count(429)} rejected (429)")

        banner("AUDIT LOG — full structured record (GET /log)")
        _, log = call("GET", "/log")
        # Show just the classification entries + the appealed one for readability
        shown = [e for e in log["entries"] if e["creator_id"] != "rl-test"]
        print(json.dumps({"entries": shown}, indent=2))

        banner("STRETCH — Analytics dashboard (GET /analytics)")
        _, stats = call("GET", "/analytics")
        print(json.dumps(stats, indent=2))
        print("\n  HTML dashboard available in a browser at: http://localhost:5000/dashboard")

        print("\nDemo complete. Every feature ran end-to-end.\n")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        # On Windows, force-kill the whole tree so the port is released immediately
        # (avoids a brief window where a back-to-back re-run sees the old listener).
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
