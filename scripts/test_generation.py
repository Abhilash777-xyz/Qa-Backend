"""
Live end-to-end LLM generation test.
Runs against a running server on localhost:8000.
"""
import json
import urllib.request as ur
import sys

BASE = "http://127.0.0.1:8000/api/v1"


def get(path):
    r = ur.urlopen(BASE + path, timeout=10)
    return json.loads(r.read())


def post(path, body):
    req = ur.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    r = ur.urlopen(req, timeout=120)
    return json.loads(r.read())


# ── 1. Get the existing selection or create one ───────────────────────────────
sels = get("/selections")
if sels["total"] == 0:
    # Create one from version 1 nodes
    nodes = get("/nodes?version_id=1&limit=5")
    node_ids = [n["id"] for n in nodes[:3]]
    sel = post("/selections", {"name": "Safety Nodes", "version_id": 1, "node_ids": node_ids})
    sel_id = sel["id"]
    print(f"Created selection id={sel_id} with {sel['node_count']} nodes")
else:
    sel_id = sels["items"][0]["id"]
    print(f"Using existing selection id={sel_id}")

# ── 2. Generate test cases ────────────────────────────────────────────────────
print(f"\nCalling POST /generate for selection_id={sel_id} ...")
result = post("/generate", {"selection_id": sel_id})

print(f"\nGeneration id    : {result['generation_id']}")
print(f"Selection id     : {result['selection_id']}")
print(f"Version id       : {result['document_version_id']}")
print(f"LLM model        : {result['llm_model']}")
print(f"Prompt version   : {result['prompt_version']}")
print(f"Is stale         : {result['is_stale']}")
print(f"Test cases count : {len(result['test_cases'])}")

print("\n--- TEST CASES ---")
for i, tc in enumerate(result["test_cases"], 1):
    print(f"\n[{i}] {tc['title']}")
    print(f"    Priority  : {tc['priority']}")
    print(f"    Reference : {tc['requirement_reference']}")
    print(f"    Objective : {tc['objective'][:80]}...")
    print(f"    Steps     : {len(tc['steps'])} step(s)")

print("\n=== LLM GENERATION SUCCESSFUL ===")
