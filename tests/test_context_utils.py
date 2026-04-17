from aidlc.context_utils import parse_project_type


def test_parse_project_type_empty_when_marker_missing():
    assert parse_project_type("no type info here") == ""


def test_parse_project_type_empty_when_no_colon_line():
    text = "We discuss project type but never use a colon format\nsecond line"
    assert parse_project_type(text) == ""


def test_parse_project_type_extracts_suffix():
    ctx = "Summary\nProject type: python, rust\n"
    assert "python" in parse_project_type(ctx)
