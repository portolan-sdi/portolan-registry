from pathlib import Path

import yaml


def test_site_dispatch_names_the_registry_commit():
    path = Path(".github/workflows/refresh-site-cache.yml")
    workflow = yaml.safe_load(path.read_text())
    steps = workflow["jobs"]["refresh"]["steps"]
    dispatch = next(step for step in steps if step.get("name") == "Request site coverage bake")

    command = dispatch["run"]
    assert "event_type=registry-export-updated" in command
    assert "client_payload[registry_sha]=$GITHUB_SHA" in command
    assert "repos/portolan-sdi/portolan-sdi.org/dispatches" in command
