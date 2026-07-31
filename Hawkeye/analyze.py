#!/usr/bin/env python3
"""
Classify Instagram comments exported by IG DOM Watcher.

Usage:
    python3 analyze.py instagram-data.csv

Outputs:
    instagram-data-analyzed.csv   original columns + stance / tone / topic
    analysis-summary.md           per-account aggregate report

Needs Ollama running locally (ollama serve). If it isn't, the script
writes prompt.txt instead, which you can paste into ChatGPT or Claude.

SCOPE: this labels COMMENTS. It does not score people. Per-account
output is limited to counts and the topics they engaged with - both
derived from behaviour, not from inferences about who someone is.
"""

import csv
import json
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"          # change to any model you've pulled

STANCES = {"supportive", "critical", "neutral", "question", "unclear"}
TONES = {"neutral", "positive", "angry", "abusive"}

SYSTEM_RULES = """You label social media comments. Return ONLY valid JSON.

Label the COMMENT TEXT, never the person who wrote it.

stance - the comment's position toward the post's subject:
  supportive | critical | neutral | question | unclear
tone - how it is expressed:
  neutral | positive | angry | abusive
  (abusive = slurs, insults directed at a person or group, harassment)
topic - 2-4 words naming what the POST is about, from its caption

If the comment is only emoji or too short to judge, use "unclear"
for stance and "neutral" for tone. Do not guess.

Respond exactly as:
{"stance": "...", "tone": "...", "topic": "..."}"""


def build_prompt(row):
    return f"""{SYSTEM_RULES}

POST CAPTION: {row.get('postCaption', 'N/A')[:400]}
COMMENT: {row.get('commentText', 'N/A')[:400]}

JSON:"""


def ask_ollama(prompt):
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0}
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["response"]


def parse_labels(raw):
    """Models wander. Keep only values from the allowed sets."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"stance": "unclear", "tone": "neutral", "topic": "N/A"}

    stance = str(data.get("stance", "")).lower().strip()
    tone = str(data.get("tone", "")).lower().strip()
    topic = str(data.get("topic", "")).strip() or "N/A"

    return {
        "stance": stance if stance in STANCES else "unclear",
        "tone": tone if tone in TONES else "neutral",
        "topic": topic
    }


def ollama_available():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False


def write_summary(rows, path):
    by_user = defaultdict(list)
    for r in rows:
        by_user[r["username"]].append(r)

    lines = [
        "# Comment analysis",
        "",
        f"{len(rows)} rows · {len(by_user)} accounts",
        "",
        "> Labels describe individual comments. Per-account sections list",
        "> what someone engaged with and how those comments were labelled.",
        "> They are not assessments of the person.",
        ""
    ]

    all_tones = Counter(r["tone"] for r in rows if r.get("commentText") not in ("", "N/A"))
    all_stances = Counter(r["stance"] for r in rows if r.get("commentText") not in ("", "N/A"))

    lines += ["## Overall", "", "| tone | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in all_tones.most_common()]
    lines += ["", "| stance | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in all_stances.most_common()]
    lines += ["", "## By account", ""]

    for user, items in sorted(by_user.items()):
        real = [i for i in items if i.get("commentText") not in ("", "N/A")]
        topics = sorted({i["topic"] for i in real if i["topic"] != "N/A"})
        tones = Counter(i["tone"] for i in real)

        lines += [
            f"### @{user}",
            "",
            f"- comments saved: {len(real)}",
            f"- engaged with: {', '.join(topics) if topics else 'N/A'}",
            f"- tone of those comments: {', '.join(f'{k} x{v}' for k, v in tones.most_common()) or 'N/A'}",
            ""
        ]
        for i in real:
            lines.append(f"  - \"{i['commentText'][:120]}\" -> {i['stance']} / {i['tone']}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "instagram-data.csv")
    if not src.exists():
        sys.exit(f"Not found: {src}\nUsage: python3 analyze.py instagram-data.csv")

    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    if not rows:
        sys.exit("CSV is empty")

    print(f"Loaded {len(rows)} rows from {src.name}")

    if not ollama_available():
        prompt_file = src.with_name("prompt.txt")
        blocks = [
            build_prompt(r) for r in rows
            if r.get("commentText") not in ("", "N/A")
        ]
        prompt_file.write_text(
            "\n\n---\n\n".join(blocks), encoding="utf-8"
        )
        print(
            "Ollama isn't running (start it with: ollama serve)\n"
            f"Wrote {prompt_file.name} - paste it into ChatGPT or Claude instead."
        )
        return

    for i, row in enumerate(rows, 1):
        if row.get("commentText") in ("", "N/A"):
            row.update(stance="N/A", tone="N/A", topic="N/A")
            continue
        try:
            row.update(parse_labels(ask_ollama(build_prompt(row))))
        except Exception as err:                       # noqa: BLE001
            print(f"  row {i} failed: {err}")
            row.update(stance="unclear", tone="neutral", topic="N/A")
        print(f"  [{i}/{len(rows)}] @{row['username']}: {row['stance']} / {row['tone']}")

    out = src.with_name(src.stem + "-analyzed.csv")
    fields = list(rows[0].keys())
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = src.with_name("analysis-summary.md")
    write_summary(rows, summary)

    print(f"\nWrote {out.name} and {summary.name}")


if __name__ == "__main__":
    main()