import urllib.request
import json

BASE = "http://127.0.0.1:8000/api/v1"


def get(path):
    r = urllib.request.urlopen(BASE + path)
    return json.loads(r.read())


# 1. Documents
docs = get("/documents")
print("--- GET /documents ---")
total = docs["total"]
print(f"  Total: {total}")
for d in docs["items"]:
    print(f"  Doc id={d['id']} title={d['title']} versions={d['version_count']}")

# 2. Versions
versions = get("/versions?document_id=1")
print("--- GET /versions?document_id=1 ---")
for v in versions["versions"]:
    print(f"  Version {v['version_number']} id={v['id']} nodes={v['node_count']}")

# 3. First nodes
nodes = get("/nodes?version_id=1&limit=5")
print("--- GET /nodes?version_id=1&limit=5 ---")
for n in nodes:
    print(f"  Node id={n['id']} [{n['numbering']}] title={n['title']} type={n['node_type']}")

first_node_id = nodes[0]["id"]

# 4. Children
children = get(f"/nodes/{first_node_id}/children")
child_count = len(children["children"])
print(f"--- GET /nodes/{first_node_id}/children --- {child_count} children")

# 5. Search
results = get("/search?q=safety&limit=5")
print("--- GET /search?q=safety ---")
print(f"  {results['total']} results")
for r in results["results"][:3]:
    print(f"  [{r['match_in']}] {r['title']}")

# 6. Version diff
diff = get("/changes/versions?v1=1&v2=2")
print("--- GET /changes/versions?v1=1&v2=2 ---")
print(f"  UNCHANGED={diff['unchanged']} MODIFIED={diff['modified']} ADDED={diff['added']} REMOVED={diff['removed']}")

# 7. Node change
change = get(f"/changes/{first_node_id}")
print(f"--- GET /changes/{first_node_id} ---")
print(f"  changed={change['changed']} status={change['status']}")

# 8. Create a selection
import urllib.request as ur
req = ur.Request(
    BASE + "/selections",
    data=json.dumps({"name": "Safety Check", "version_id": 1, "node_ids": [nodes[0]["id"], nodes[1]["id"]]}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
r = ur.urlopen(req)
sel = json.loads(r.read())
print(f"--- POST /selections --- id={sel['id']} name={sel['name']} nodes={sel['node_count']}")

print()
print("=== ALL CHECKS PASSED ===")
