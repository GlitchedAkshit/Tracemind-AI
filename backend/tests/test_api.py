"""End-to-end API behaviour: auth, authorization boundaries, ingestion → detection →
analysis → investigation → report, and evaluation runs."""

import json

from app.llm.providers import LLMError, StaticProvider, set_provider_override

API = "/api/v1"


def create_project(c, name="Shop", envs=("production",)):
    r = c.post(f"{API}/projects", json={"name": name, "environments": list(envs)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def upload(c, pid, content: bytes, filename="data.jsonl", key=None, **form):
    headers = {"Idempotency-Key": key} if key else {}
    return c.post(f"{API}/projects/{pid}/ingestions", files={"file": (filename, content)}, data=form, headers=headers)


def test_health_and_meta(client):
    assert client.get(f"{API}/health").json()["status"] == "ok"
    meta = client.get(f"{API}/meta").json()
    assert meta["llm_provider"] == "deterministic" and meta["algorithm_version"]


def test_auth_flow_and_errors(client):
    assert client.get(f"{API}/projects").status_code == 401
    err = client.get(f"{API}/projects").json()["error"]
    assert err["code"] == "unauthenticated" and err["request_id"]
    r = client.post(f"{API}/auth/register", json={"email": "a@x.io", "password": "short", "display_name": "A"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    client.post(f"{API}/auth/register", json={"email": "a@x.io", "password": "long-enough-pass", "display_name": "A"})
    assert client.post(f"{API}/auth/register", json={"email": "A@x.io", "password": "long-enough-pass", "display_name": "A"}).status_code == 409
    client.post(f"{API}/auth/logout")
    assert client.post(f"{API}/auth/login", json={"email": "a@x.io", "password": "wrong-password"}).status_code == 401
    r = client.post(f"{API}/auth/login", json={"email": "a@x.io", "password": "long-enough-pass"})
    assert r.status_code == 200
    # bearer tokens work as well as the cookie
    client.cookies.clear()
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 200


def test_project_isolation_and_roles(make_user):
    alice, bob = make_user("alice@x.io"), make_user("bob@x.io")
    pid = create_project(alice)
    # bob cannot see that the project exists
    assert bob.get(f"{API}/projects/{pid}").status_code == 404
    assert bob.get(f"{API}/projects/{pid}/incidents").status_code == 404
    assert upload(bob, pid, b'{"timestamp":"2026-01-01T00:00:00Z","service":"a"}').status_code == 404
    assert pid not in [p["id"] for p in bob.get(f"{API}/projects").json()]
    # viewer can read but not ingest or change settings
    assert alice.post(f"{API}/projects/{pid}/members", json={"email": "bob@x.io", "role": "viewer"}).status_code == 201
    assert bob.get(f"{API}/projects/{pid}").json()["role"] == "viewer"
    assert upload(bob, pid, b'{"timestamp":"2026-01-01T00:00:00Z","service":"a"}').status_code == 403
    assert bob.patch(f"{API}/projects/{pid}", json={"name": "Mine now"}).status_code == 403
    # owners cannot remove the last owner
    members = alice.get(f"{API}/projects/{pid}/members").json()
    owner = next(m for m in members if m["role"] == "owner")
    assert alice.delete(f"{API}/projects/{pid}/members/{owner['id']}").status_code == 409


def test_settings_validation(make_user):
    alice = make_user("alice@x.io")
    pid = create_project(alice)
    assert alice.patch(f"{API}/projects/{pid}", json={"analysis_settings": {"z_threshold": 999}}).status_code == 422
    assert alice.patch(f"{API}/projects/{pid}", json={"analysis_settings": {"rm -rf": 1}}).status_code == 422
    r = alice.patch(f"{API}/projects/{pid}", json={"analysis_settings": {"z_threshold": 5}, "environments": ["production", "Staging"]})
    assert r.status_code == 200 and r.json()["environments"] == ["production", "staging"]


def test_upload_validation(make_user):
    alice = make_user("alice@x.io")
    pid = create_project(alice)
    assert upload(alice, pid, b"", "a.jsonl").status_code == 422
    assert upload(alice, pid, b"MZ\x00\x00binary", "a.csv").status_code == 415
    assert upload(alice, pid, b"{}", "a.exe").status_code == 415
    assert upload(alice, pid, b'{"timestamp":"2026-01-01T00:00:00Z","service":"a"}', environment="mars").status_code == 422
    assert upload(alice, pid, b'{"a":1}', service_mapping="not json").status_code == 422


def test_preview_reports_schema_and_errors(make_user):
    alice = make_user("alice@x.io")
    pid = create_project(alice)
    content = b'{"timestamp":"2026-01-15T09:00:00","service":"Api Gateway","message":"x"}\n{bad json\n'
    r = alice.post(f"{API}/projects/{pid}/ingestions/preview", files={"file": ("p.jsonl", content)},
                   data={"service_mapping": json.dumps({"Api Gateway": "api-gateway"})})
    body = r.json()
    assert r.status_code == 200
    assert body["accepted_rows"] == 1 and body["rejected_rows"] == 1
    assert body["services"] == ["api-gateway"]
    assert body["warnings"] == {"timestamp had no timezone; assumed UTC": 1}
    assert {c["name"] for c in body["columns"]} == {"timestamp", "service", "message"}


def test_full_workflow(make_user, scenario_jsonl):
    alice = make_user("alice@x.io")
    pid = create_project(alice)
    bad_rows = b'\n{"service":"x"}\nnot-json\n'
    r = upload(alice, pid, scenario_jsonl + bad_rows, key="upload-1")
    assert r.status_code == 202, r.text
    job = alice.get(f"{API}/ingestions/{r.json()['id']}").json()
    assert job["status"] == "completed", job
    assert job["rejected_rows"] == 2 and job["accepted_rows"] + job["rejected_rows"] + job["duplicate_rows"] == job["total_rows"]
    assert {e["row"] for e in job["errors"]}
    assert job["detection_summary"]["environments"]["production"]["incidents"] >= 1

    # idempotency key returns the same job; re-uploading the same file is fully deduplicated
    again = upload(alice, pid, scenario_jsonl, key="upload-1")
    assert again.status_code == 200 and again.json()["id"] == job["id"]
    dup = alice.get(f"{API}/ingestions/{upload(alice, pid, scenario_jsonl).json()['id']}").json()
    assert dup["accepted_rows"] == 0 and dup["duplicate_rows"] == dup["total_rows"]
    assert alice.get(f"{API}/projects/{pid}/ingestions").json()["total"] == 2

    services = alice.get(f"{API}/projects/{pid}/services").json()
    assert "payments-service" in {s["name"] for s in services}
    graph = alice.get(f"{API}/projects/{pid}/service-graph").json()
    assert {"source": "checkout-service", "target": "payments-service"}.items() <= next(
        e for e in graph["edges"] if e["id"] == "checkout-service->payments-service").items()

    incidents = alice.get(f"{API}/projects/{pid}/incidents?sort=severity&order=desc").json()
    assert incidents["total"] >= 1
    incident = next(i for i in incidents["items"] if "payments-service" in i["affected_services"])
    iid = incident["id"]
    assert alice.get(f"{API}/incidents/{iid}/hypotheses").json()["hypotheses"] == []
    assert alice.post(f"{API}/incidents/{iid}/reports").status_code == 409

    run = alice.post(f"{API}/incidents/{iid}/analyze", json={}).json()
    run = alice.get(f"{API}/analysis-runs/{run['id']}").json()
    assert run["status"] == "completed" and run["progress"] == 100, run
    assert run["summary"]["meta"]["writer"] == "deterministic"

    hyps = alice.get(f"{API}/incidents/{iid}/hypotheses").json()["hypotheses"]
    assert hyps[0]["service"] == "payments-service" and hyps[0]["cause_type"] == "deployment_regression"
    for ev in hyps[0]["evidence"]:  # every evidence reference opens a real event
        detail = alice.get(f"{API}/events/{ev['event_id']}").json()
        assert detail["service"] and detail["occurred_at"] and detail["event_type"]
    assert alice.get(f"{API}/incidents/{iid}").json()["leading_hypothesis"] == "payments-service"
    assert alice.get(f"{API}/incidents/{iid}/timeline").json()
    assert alice.get(f"{API}/incidents/{iid}/metrics").json()["services"]

    a = alice.post(f"{API}/incidents/{iid}/investigate", json={"question": "What changed immediately before the first failure?"}).json()
    assert a["evidence"] and any(e["event_type"] == "deployment" for e in a["evidence"])
    assert len(alice.get(f"{API}/incidents/{iid}/investigations").json()) == 1

    fb = alice.post(f"{API}/incidents/{iid}/feedback", json={"verdict": "correct", "hypothesis_id": hyps[0]["id"]})
    assert fb.status_code == 201 and fb.json()["analysis_run_id"] == run["id"]

    report = alice.post(f"{API}/incidents/{iid}/reports").json()
    md = alice.get(f"{API}/reports/{report['id']}/markdown")
    assert md.status_code == 200 and md.text.startswith("# Incident report")
    assert "not probabilities" in md.text and hyps[0]["evidence"][0]["event_id"] in md.text

    overview = alice.get(f"{API}/projects/{pid}/overview?range=all").json()
    assert overview["stats"]["active_incidents"] >= 1 and overview["series"]["overall"]
    assert overview["recent_investigations"][0]["leading_hypothesis"]

    # another user cannot reach any of it
    eve = make_user("eve@x.io")
    for path in (f"/incidents/{iid}", f"/analysis-runs/{run['id']}", f"/reports/{report['id']}",
                 f"/events/{hyps[0]['evidence'][0]['event_id']}", f"/ingestions/{job['id']}"):
        assert eve.get(f"{API}{path}").status_code == 404, path
    assert eve.post(f"{API}/incidents/{iid}/analyze", json={}).status_code == 404


def test_llm_failure_keeps_workflow_usable(make_user, scenario_jsonl):
    alice = make_user("alice@x.io")
    pid = create_project(alice)
    upload(alice, pid, scenario_jsonl)
    iid = alice.get(f"{API}/projects/{pid}/incidents").json()["items"][0]["id"]
    set_provider_override(StaticProvider([LLMError("provider returned HTTP 500")] * 3, model="anthropic/test"))
    run = alice.post(f"{API}/incidents/{iid}/analyze", json={}).json()
    run = alice.get(f"{API}/analysis-runs/{run['id']}").json()
    assert run["status"] == "completed"
    assert run["summary"]["meta"]["writer"] == "deterministic" and "HTTP 500" in run["summary"]["meta"]["fallback_reason"]
    ans = alice.post(f"{API}/incidents/{iid}/investigate", json={"question": "What should I investigate next?"}).json()
    assert ans["writer"] == "deterministic" and ans["fallback_reason"]


def test_rate_limit_on_analysis(make_user, scenario_jsonl):
    alice = make_user("alice@x.io")
    pid = create_project(alice)
    upload(alice, pid, scenario_jsonl)
    iid = alice.get(f"{API}/projects/{pid}/incidents").json()["items"][0]["id"]
    codes = [alice.post(f"{API}/incidents/{iid}/analyze", json={}).status_code for _ in range(8)]
    assert codes.count(202) == 6 and codes[-1] == 429


def test_demo_session_is_read_mostly(client, scenario_jsonl):
    from app.db import SessionLocal
    from app.seed import seed_demo

    with SessionLocal() as db:
        seed_demo(db, analyze=False)
    r = client.post(f"{API}/auth/demo")
    assert r.status_code == 200 and r.json()["user"]["is_demo"]
    projects = client.get(f"{API}/projects").json()
    demo = next(p for p in projects if p["is_demo"])
    assert demo["role"] == "analyst"
    assert upload(client, demo["id"], scenario_jsonl).status_code == 403
    assert client.patch(f"{API}/projects/{demo['id']}", json={"name": "Renamed demo"}).status_code == 403
    incidents = client.get(f"{API}/projects/{demo['id']}/incidents").json()["items"]
    assert len(incidents) >= 2
    assert client.post(f"{API}/incidents/{incidents[0]['id']}/analyze", json={}).status_code == 202
    members = client.get(f"{API}/projects/{demo['id']}/members").json()
    assert all("@" not in m["email"] for m in members)
    # demo visitors may create a few sandbox projects of their own
    for i in range(3):
        assert client.post(f"{API}/projects", json={"name": f"sandbox {i}"}).status_code == 201
    assert client.post(f"{API}/projects", json={"name": "one too many"}).status_code == 403


def test_evaluation_run(client):
    client.post(f"{API}/auth/register", json={"email": "e@x.io", "password": "long-enough-pass", "display_name": "E"})
    run = client.post(f"{API}/evaluation-runs", json={"seed": 3, "scenario_count": 2, "strategies": ["rules_only", "hybrid"]}).json()
    run = client.get(f"{API}/evaluation-runs/{run['id']}").json()
    assert run["status"] == "completed", run
    m = run["metrics"]
    assert set(m["strategies"]) == {"rules_only", "hybrid"}
    for s in m["strategies"].values():
        assert 0 <= s["top1_accuracy"] <= s["top3_accuracy"] <= 1
    assert m["strategies"]["hybrid"]["invalid_citation_rate"] == 0
    results = client.get(f"{API}/evaluation-runs/{run['id']}/results").json()
    assert len(results) == 2 * m["injected_faults"]
    csv_body = client.get(f"{API}/evaluation-runs/{run['id']}/export?format=csv").text
    assert csv_body.startswith("scenario_id,")
    assert client.post(f"{API}/evaluation-runs", json={"scenario_count": 1000}).status_code == 422


def test_evaluation_run_with_two_fault_scenario(client):
    from app.bench.generator import random_scenario

    # seed 42, scenario 4 injects two faults; both must be stored without clashing
    assert len(random_scenario(42, 4).faults) == 2
    client.post(f"{API}/auth/register", json={"email": "m@x.io", "password": "long-enough-pass", "display_name": "M"})
    run = client.post(f"{API}/evaluation-runs", json={"seed": 42, "scenario_count": 5, "strategies": ["hybrid"]}).json()
    run = client.get(f"{API}/evaluation-runs/{run['id']}").json()
    assert run["status"] == "completed", run["error_message"]
    results = client.get(f"{API}/evaluation-runs/{run['id']}/results").json()
    assert len(results) == run["metrics"]["injected_faults"] == 6
    assert sum(r["multi_fault"] for r in results) == 2
