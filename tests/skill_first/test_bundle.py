"""Skill bundle metadata checks, without importing Hermes or installing a skill."""
from pathlib import Path
import re


BUNDLE = Path(__file__).resolve().parents[2] / "skills/hermes-pr-review"


def test_entrypoint_links_resolve_and_frontmatter_is_portable():
    text = (BUNDLE / "SKILL.md").read_text()
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    name_match = re.search(r"^name: (.+)$", frontmatter, re.MULTILINE)
    description_match = re.search(r'^description: "(.+)"$', frontmatter, re.MULTILINE)
    assert name_match is not None and description_match is not None
    name, description = name_match.group(1), description_match.group(1)
    assert name == BUNDLE.name
    assert 0 < len(description) <= 60 and description.endswith(".")
    for field in ("version", "author", "license", "platforms", "metadata"):
        assert re.search(rf"^{field}:", frontmatter, re.MULTILINE)
    assert "/home/" not in text and "/Users/" not in text
    references = re.findall(r"\]\((references/[^)]+)\)", body)
    assert references and all((BUNDLE / path).is_file() for path in references)
    assert (BUNDLE / "scripts/pr_review.py").is_file()


def test_url_install_inventory_names_every_runtime_file():
    """Bare SKILL.md URL installs do not follow Python imports or nested links."""
    body = (BUNDLE / "SKILL.md").read_text()
    links = set(re.findall(r"\]\(((?:scripts|references)/[^)]+)\)", body))
    required = {str(path.relative_to(BUNDLE)) for path in BUNDLE.rglob("*")
                if path.is_file() and path.suffix in {".py", ".md"} and path.name != "SKILL.md"}
    assert links == required
