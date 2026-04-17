"""aidlc.audit_schemas parse helpers delegate to schemas.parse_json_output."""

from aidlc.audit_schemas import parse_audit_feature_output, parse_audit_module_output


def test_parse_audit_module_output_json_block():
    raw = '```json\n{"module_name": "m", "description": "d"}\n```'
    data = parse_audit_module_output(raw)
    assert data["module_name"] == "m"


def test_parse_audit_feature_output_json_block():
    raw = '```json\n{"features": [], "summary": "s"}\n```'
    data = parse_audit_feature_output(raw)
    assert data["summary"] == "s"
