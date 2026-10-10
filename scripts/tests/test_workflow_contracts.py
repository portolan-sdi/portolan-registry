from pathlib import Path

import yaml


def test_auto_merge_runs_as_the_app():
    # A merge attributed to GITHUB_TOKEN raises no push event, so Publish and
    # Refresh Site Cache never run after it.
    path = Path(".github/workflows/auto-merge.yml")
    workflow = yaml.safe_load(path.read_text())
    steps = workflow["jobs"]["auto-merge"]["steps"]

    token = next(step for step in steps if step.get("id") == "token")
    assert token["uses"].startswith("actions/create-github-app-token@")
    assert token["with"]["permission-contents"] == "write"
    assert token["with"]["permission-pull-requests"] == "write"

    merge = next(step for step in steps if step.get("name") == "Enable auto-merge")
    assert merge["env"]["GH_TOKEN"] == "${{ steps.token.outputs.token }}"

    permissions = workflow.get("permissions", {})
    assert all(level != "write" for level in permissions.values())


def test_site_dispatch_names_the_registry_commit():
    path = Path(".github/workflows/refresh-site-cache.yml")
    workflow = yaml.safe_load(path.read_text())
    steps = workflow["jobs"]["refresh"]["steps"]
    dispatch = next(step for step in steps if step.get("name") == "Request site coverage bake")

    command = dispatch["run"]
    assert "event_type=registry-export-updated" in command
    assert "client_payload[registry_sha]=$GITHUB_SHA" in command
    assert "repos/portolan-sdi/portolan-sdi.org/dispatches" in command


def _steps(name, job):
    workflow = yaml.safe_load(Path(f".github/workflows/{name}").read_text())
    return workflow["jobs"][job]["steps"]


def test_every_validating_workflow_installs_both_validators():
    # Without rashid the gate fails every new entry, and the nightly
    # publishes null for every catalog. Both must ship with the step that
    # runs them.
    for name, job, script in [
        ("validate.yml", "validate", "scripts/validate_entries.py"),
        ("revalidate.yml", "revalidate", "scripts/revalidate_all.py"),
        ("publish.yml", "publish", "scripts/publish_export.py"),
    ]:
        steps = _steps(name, job)
        runs = [step.get("run", "") for step in steps]
        assert any(
            "npm ci --prefix scripts/stac-node-validator" in run for run in runs
        ), name
        node = next(s for s in steps if s.get("uses", "").startswith("actions/setup-node@"))
        assert int(str(node["with"]["node-version"]).split(".")[0]) >= 22, name
        crawl = next(run for run in runs if script in run)
        assert "--group validators" in crawl, name


def test_the_gate_reads_the_export_from_the_base_branch():
    # The export decides which entries skip the validators. A pull request
    # can edit its own copy, so the gate must not read it.
    runs = [step.get("run", "") for step in _steps("validate.yml", "validate")]
    assert any(
        'git show "origin/$BASE_REF:exports/catalogs.json" > base-export.json' in run
        for run in runs
    )
    gate = next(run for run in runs if "scripts/validate_entries.py" in run)
    assert "--export base-export.json" in gate


def test_every_crawling_job_has_a_timeout():
    # One host that stalls must not hold a runner for the 6 hour default.
    for name, job in [
        ("validate.yml", "validate"),
        ("revalidate.yml", "revalidate"),
        ("publish.yml", "publish"),
    ]:
        workflow = yaml.safe_load(Path(f".github/workflows/{name}").read_text())
        assert 0 < workflow["jobs"][job]["timeout-minutes"] <= 180, name
