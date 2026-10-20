import os
import subprocess
import sys


def test_runtime_startup_mapper_resolution_in_isolated_process():
    """Verify that runtime startup via create_app() configures all mappers.

    This test executes in a completely clean, isolated Python subprocess
    so it does not inherit any pre-imported models from tests/conftest.py.
    It verifies that WorkItem.sprint and WorkItem.comments relationships
    are cleanly resolved to Sprint and WorkItemComment classes.
    """
    python_code = """
import sys
from taskflow.main import create_app
from sqlalchemy.orm import class_mapper
from taskflow.domains.work_items.models import WorkItem
from taskflow.domains.scrum.models import Sprint
from taskflow.domains.collaboration.models import WorkItemComment

# Create FastAPI application (triggers configure_mappers())
app = create_app()

mapper = class_mapper(WorkItem)

# 1. Verify WorkItem.sprint relationship resolves to Sprint mapper
sprint_prop = mapper.get_property("sprint")
assert sprint_prop.mapper.class_ is Sprint, f"Expected Sprint, got {sprint_prop.mapper.class_}"

# 2. Verify WorkItem.comments relationship resolves to WorkItemComment mapper
comments_prop = mapper.get_property("comments")
assert comments_prop.mapper.class_ is WorkItemComment, f"Expected WorkItemComment, got {comments_prop.mapper.class_}"

print("REGRESSION_CHECK_PASSED")
"""
    env = os.environ.copy()
    src_path = os.path.abspath("src")
    env["PYTHONPATH"] = f"{src_path}:{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        [sys.executable, "-c", python_code],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, f"Process failed with exit code {result.returncode}.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "REGRESSION_CHECK_PASSED" in result.stdout
