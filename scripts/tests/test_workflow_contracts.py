from pathlib import Path

import yaml


def test_publish_dispatch_has_required_token_permissions():
    path = Path(".github/workflows/auto-merge.yml")
    workflow = yaml.safe_load(path.read_text())
    job = workflow["jobs"]["trigger-publish"]
    permissions = job.get("permissions", workflow.get("permissions", {}))

    assert permissions.get("actions") == "write"
    assert permissions.get("pull-requests") in {"read", "write"}
    assert permissions.get("contents", "none") in {"none", "read"}

    merge_job = workflow["jobs"]["auto-merge"]
    merge_permissions = merge_job.get("permissions", workflow.get("permissions", {}))
    assert merge_permissions.get("actions", "none") != "write"


def test_site_dispatch_names_the_registry_commit():
    path = Path(".github/workflows/refresh-site-cache.yml")
    workflow = yaml.safe_load(path.read_text())
    steps = workflow["jobs"]["refresh"]["steps"]
    dispatch = next(step for step in steps if step.get("name") == "Request site coverage bake")

    command = dispatch["run"]
    assert "event_type=registry-export-updated" in command
    assert "client_payload[registry_sha]=$GITHUB_SHA" in command
    assert "repos/portolan-sdi/portolan-sdi.org/dispatches" in command
