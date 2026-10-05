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
