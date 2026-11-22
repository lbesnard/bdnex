"""Conventions of the files that are not Python: the page's script and the workflows."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "bdnex" / "api" / "static"


def test_the_page_never_builds_html_from_strings_or_runs_text_as_code():
    for name in ("app.js", "index.html"):
        text = (STATIC / name).read_text(encoding="utf-8")
        for sink in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write", "eval(", "new Function"):
            assert sink not in text, f"{name} uses {sink}: everything from the server goes through textContent"
    assert not re.search(r"[\"']javascript:", (STATIC / "app.js").read_text(encoding="utf-8"))  # (the comment that says so is fine)
    assert "<script>" not in (STATIC / "index.html").read_text(encoding="utf-8")  # the CSP forbids inline scripts


def test_every_view_gives_up_when_a_newer_draw_started_while_it_waited_for_the_server():
    lines = (STATIC / "app.js").read_text(encoding="utf-8").split("\n")
    drawn = [i for i, line in enumerate(lines) if "async draw(" in line]
    assert drawn
    for i in drawn:
        window = "\n".join(lines[i:i + 9])
        assert "const mine = ++drawToken;" in window and "if (mine !== drawToken) return;" in window, \
            f"app.js line {i + 1}: a slow answer for an older request would render over a newer view"


def test_every_workflow_action_is_pinned_to_a_commit_and_no_workflow_has_a_broad_token():
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        for use in re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", text, flags=re.MULTILINE):
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", use), f"{workflow.name}: {use} is not pinned to a commit"
        assert re.search(r"^permissions:\s*\n\s+contents:\s*read", text, flags=re.MULTILINE), \
            f"{workflow.name}: the default token must be read-only (a job that needs more asks for it)"
        for line in text.split("\n"):
            if "github.event.inputs" in line:  # only as `NAME: ${{ ... }}` (an environment variable), never inside a script
                assert re.match(r"^\s+[A-Z_]+:\s+\$\{\{ github\.event\.inputs\.\w+ \}\}", line), \
                    f"{workflow.name}: an input pasted into a script is an injection: pass it through env ({line.strip()})"


def test_the_release_pushes_the_bump_and_its_tag_last_and_together():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert text.index("build-push-action") < text.index("git push --atomic") < text.index("gh release create")
    assert "if: github.ref == 'refs/heads/main'" in text and "ref: ${{ github.sha }}" in text  # main only, the tested commit
