#!/usr/bin/env python3
"""Check specification provenance and document links, not application correctness."""

import argparse
import json
import re
import sys
from hashlib import sha256
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def run():
    errors = []

    def check(value, message):
        if not value:
            errors.append(message)

    def read(name):
        return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))

    def keyed(rows, key, label):
        result = {}
        for row in rows:
            value = row[key]
            check(value not in result, f"Duplicate {label}: {value}")
            result[value] = row
        return result

    def refs(values, targets, origin):
        for value in values:
            check(value in targets, f"Unknown reference {value} from {origin}")

    manifest = read("00_manifest.yaml")
    contract = read("01_contract.yaml")
    acceptance = read("02_acceptance.yaml")
    api = read("03_api_discovery.yaml")
    plan = read("04_tasks.yaml")
    source = ROOT / manifest["active_spec"]["file"]
    data = source.read_bytes()
    text = data.decode("utf-8")
    lines = text.splitlines()
    check(
        sha256(data).hexdigest() == manifest["active_spec"]["sha256"],
        "Active specification hash mismatch",
    )
    check(
        len(lines) == manifest["active_spec"]["lines"], "Active specification line count mismatch"
    )
    archive = ROOT / manifest["historical_evidence"]["file"]
    check(
        sha256(archive.read_bytes()).hexdigest() == manifest["historical_evidence"]["sha256"],
        "Historical evidence archive hash mismatch",
    )
    for item in manifest["files"]:
        if item["path"] != "checks/validation.json":
            check((ROOT / item["path"]).is_file(), f"Missing package file: {item['path']}")
    sections = keyed(manifest["section_index"], "section", "section")
    source_heads = []
    for i, line in enumerate(lines):
        m = re.match(r"^(#{2,3}) (\d+(?:\.\d+)?)\.?(?: )(.+)$", line)
        if m:
            source_heads.append(
                {
                    "section": m.group(2),
                    "title": m.group(3),
                    "level": len(m.group(1)),
                    "line_start": i + 1,
                }
            )
    check(set(sections) == {h["section"] for h in source_heads}, "Source heading index mismatch")
    for i, h in enumerate(source_heads):
        end = next(
            (n["line_start"] - 1 for n in source_heads[i + 1 :] if n["level"] <= h["level"]),
            len(lines),
        )
        actual = sections.get(h["section"], {})
        check(
            all(actual.get(k) == v for k, v in h.items()),
            f"Heading metadata mismatch: {h['section']}",
        )
        check(actual.get("line_end") == end, f"Heading range mismatch: {h['section']}")
        digest = sha256("\n".join(lines[h["line_start"] - 1 : end]).encode()).hexdigest()
        check(actual.get("sha256") == digest, f"Section text hash mismatch: {h['section']}")

    def section_lines(sid):
        record = sections[sid]
        return lines[record["line_start"] - 1 : record["line_end"]]

    rules = keyed(contract["rules"], "id", "rule")
    criteria = keyed(acceptance["criteria"], "id", "acceptance")
    questions = keyed(api["questions"], "id", "open question")
    tasks = keyed(plan["tasks"], "id", "task")
    raw_ac = {}
    for i, line in enumerate(lines):
        if re.match(r"^\| AC-[CEQ]-\d{2} \|", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            check(len(cells) == 3, "Unexpected acceptance table shape")
            if len(cells) == 3:
                check(cells[0] not in raw_ac, "Duplicate acceptance ID in source")
                raw_ac[cells[0]] = {
                    "scenario": cells[1],
                    "expected": cells[2],
                    "source_line": i + 1,
                }
    check(set(raw_ac) == set(criteria), "AC collection does not exactly match specification")
    check(len(criteria) == 37, "Expected exactly 37 unique AC")
    for group, count in [("C", 7), ("E", 20), ("Q", 10)]:
        check(
            {x for x in criteria if x.startswith("AC-" + group + "-")}
            == {f"AC-{group}-{i:02d}" for i in range(1, count + 1)},
            f"AC-{group} range mismatch",
        )
    for cid, row in criteria.items():
        for field in ["scenario", "expected", "source_line"]:
            check(
                row[field] == raw_ac.get(cid, {}).get(field), f"AC source mismatch: {cid}.{field}"
            )
        refs([row["source_section"]], sections, cid)
        refs(row["rule_refs"], rules, cid)
        refs(row["task_refs"], tasks, cid)
        check(bool(row["rule_refs"]), f"AC without related rules: {cid}")
        for rid in row["rule_refs"]:
            if rid in rules:
                check(cid in rules[rid]["acceptance_refs"], f"Asymmetric rule/AC link: {rid}/{cid}")
        for tid in row["task_refs"]:
            if tid in tasks:
                check(cid in tasks[tid]["acceptance_refs"], f"Asymmetric task/AC link: {tid}/{cid}")
    explicit_ids = set(re.findall(r"\*\*(REQ-\d{2})\.\*\*", text)) | set(
        re.findall(r"^\| ([CE]-\d{2}) \|", text, re.M)
    )
    supported_ids = explicit_ids | set(criteria) | set(questions)
    used_ids = set()
    classes = {"REQ", "DEMO-REQ", "DESIGN", "ENG", "OPEN_GATE"}
    for rid, row in rules.items():
        check(row["class"] in classes, f"Unsupported rule class: {rid}")
        check(bool(row["statement"]) and bool(row["verification"]), f"Incomplete rule: {rid}")
        refs(row["source_sections"], sections, rid)
        refs(row["source_ids"], supported_ids, rid)
        used_ids.update(row["source_ids"])
        refs(row["api_gates"], questions, rid)
        refs(row["acceptance_refs"], criteria, rid)
        refs(row.get("related_rules", []), rules, rid)
        for cid in row["acceptance_refs"]:
            if cid in criteria:
                check(rid in criteria[cid]["rule_refs"], f"Asymmetric AC/rule link: {cid}/{rid}")
    check(
        explicit_ids.issubset(used_ids),
        "Some explicit REQ/C/E source IDs are missing from rule provenance",
    )
    actual_dod = [
        line[len("- [ ] ") :] for line in section_lines("17") if line.startswith("- [ ] ")
    ]
    check(
        actual_dod == acceptance["definition_of_done"]["items_verbatim"],
        "DoD is not a verbatim transcription",
    )
    raw_open = {}
    for line in section_lines("16.1"):
        if line.startswith("| **OPEN-"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            raw_open[cells[0].strip("*")] = cells[1:]
    check(set(raw_open) == set(questions), "OPEN collection mismatch")
    for qid, q in questions.items():
        check(
            [q["priority"], q["question_verbatim"], q["impact_verbatim"]] == raw_open.get(qid),
            f"OPEN text mismatch: {qid}",
        )
        refs(q["task_refs"], tasks, qid)
        check(
            q["kind"] == ("organizational" if qid == "OPEN-07" else "instance_api"),
            f"OPEN category mismatch: {qid}",
        )
    for tid, t in tasks.items():
        refs(t["depends_on"], tasks, tid)
        refs(t["rule_refs"], rules, tid)
        refs(t["acceptance_refs"], criteria, tid)
        refs(t["source_sections"], sections, tid)
        refs(t["api_dependencies"], questions, tid)
        refs(t.get("organizational_dependencies", []), questions, tid)
        check(
            "OPEN-07" not in t["api_dependencies"],
            f"Organizational question used as API dependency: {tid}",
        )
        for cid in t["acceptance_refs"]:
            if cid in criteria:
                check(tid in criteria[cid]["task_refs"], f"Asymmetric AC/task link: {cid}/{tid}")
    visiting = set()
    visited = set()

    def visit(tid):
        if tid in visiting:
            check(False, f"Task dependency cycle at {tid}")
            return
        if tid in visited or tid not in tasks:
            return
        visiting.add(tid)
        for dep in tasks[tid]["depends_on"]:
            visit(dep)
        visiting.remove(tid)
        visited.add(tid)

    for tid in tasks:
        visit(tid)
    check(set(tasks) == {f"TASK-{i:02d}" for i in range(1, 6)}, "Expected five task IDs")
    check(set(tasks["TASK-05"]["acceptance_refs"]) == set(criteria), "Final task must cover all AC")
    referenced_rules = {rid for t in tasks.values() for rid in t["rule_refs"]} | set(
        plan["always_read_rule_refs"]
    )
    check(set(rules) <= referenced_rules, "Rules not linked to task context")
    for example in acceptance["illustrative_cases"]["cases"]:
        refs(example["acceptance_refs"], criteria, example["id"])
        refs(
            example.get("source_sections", [example.get("source_section")]), sections, example["id"]
        )
    raw_routes = []
    for line in section_lines("8"):
        if re.match(r"^\| `(GET|POST|PUT)`", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            raw_routes.append((cells[0].strip("`"), cells[1].strip("`"), cells[2]))
    extracted_routes = [
        (r["method"], r["path"], r["purpose"]) for r in contract["internal_http_routes"]["routes"]
    ]
    check(raw_routes == extracted_routes, "Internal route transcription mismatch")
    check(
        contract["internal_http_routes"]["upstream_keitaro_routes"] is False,
        "Internal routes mislabeled as upstream",
    )
    check(
        api["facts_verified_in_this_pack"] == [],
        "This preparation pack must not claim unperformed live API verification",
    )
    counts = {
        "rules": len(rules),
        "acceptance_criteria": len(criteria),
        "tasks": len(tasks),
        "open_api_questions": sum(q["kind"] == "instance_api" for q in questions.values()),
        "organizational_questions": sum(q["kind"] == "organizational" for q in questions.values()),
        "source_sections": len(sections),
    }
    check(counts == manifest["counts"], "Manifest counts mismatch")
    return {
        "result": "valid" if not errors else "invalid",
        "scope": "Structural/provenance checks only; no application tests, API verification or proof of semantic completeness.",
        "baseline_sha256": manifest["active_spec"]["sha256"],
        "counts": counts,
        "explicit_source_ids_covered": len(explicit_ids),
        "verbatim_dod_items": len(actual_dod),
        "internal_routes": len(raw_routes),
        "checks": [
            "Active source and historical archive hashes",
            "All section anchors and section hashes",
            "37 AC verbatim and unchanged numbering",
            "DoD verbatim",
            "Existing rule/section/AC/task/API references",
            "Explicit REQ/C/E provenance coverage",
            "Task dependency acyclicity",
            "Internal route transcription and separation from upstream API",
        ],
        "errors": errors,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = run()
    except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError) as exc:
        report = {"result": "invalid", "errors": [f"{type(exc).__name__}: {exc}"]}
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    sys.exit(0 if report["result"] == "valid" else 1)
