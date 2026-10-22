"""Typed dispatch is explicit and validation never executes handlers."""

from unittest.mock import Mock

import pytest
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from jsonschema import ValidationError as SchemaValidationError

from ntg_llm.errors import RoutingError
from ntg_llm.schemas import Answer, response_schema, validate_response
from ntg_llm.tools.models import ToolError, ToolSpec
from ntg_llm.tools.registry import ToolRegistry


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: StrictStr


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: StrictStr


def spec(handler=None, name="echo"):
    return ToolSpec(
        name,
        "Echo the supplied value",
        Arguments,
        Result,
        handler or (lambda args: Result(result=args.value)),
    )


def test_dispatch_and_catalog_are_typed_and_side_effect_free():
    handler = Mock(return_value=Result(result="hi"))
    registry = ToolRegistry([spec(handler)])
    decision = registry.build_decision_model(Answer)
    schema = response_schema(decision)
    assert schema["additionalProperties"] is False
    assert registry.catalog()[0]["name"] == "echo"
    parsed = validate_response(
        '{"action":"tool","tool":{"name":"echo","arguments":{"value":"hi"}},"output":null}',
        decision,
        schema,
    )
    handler.assert_not_called()
    result = registry.execute(parsed.tool.name, parsed.tool.arguments, call_id="tool_001")
    assert result.ok and result.data.result == "hi"
    assert result.error is None
    assert handler.call_count == 1
    assert result.model_dump(mode="json")["data"] == {"result": "hi"}


@pytest.mark.parametrize("specs", [[spec(name="bad-name")], [spec(), spec()]])
def test_invalid_registration_fails_before_dispatch(specs):
    with pytest.raises(RoutingError) as error:
        ToolRegistry(specs)
    assert error.value.status_code == 400


def test_open_argument_schema_is_rejected():
    class Open(BaseModel):
        data: dict[str, str]

    with pytest.raises(RoutingError):
        ToolRegistry([ToolSpec("open", "Open", Open, Result, Mock())])


@pytest.mark.parametrize("arguments", [{}, {"value": 3}, {"value": "hi", "extra": True}])
def test_invalid_arguments_do_not_invoke_handler(arguments):
    handler = Mock()
    result = ToolRegistry([spec(handler)]).execute("echo", arguments, call_id="tool_001")
    assert not result.ok and result.error.code == "invalid_arguments"
    handler.assert_not_called()


def test_unknown_tool_never_executes_anything():
    handler = Mock()
    result = ToolRegistry([spec(handler)]).execute("unknown", {}, call_id="tool_001")
    assert result.error.code == "unknown_tool"
    handler.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        '{"action":"tool","tool":null,"output":null}',
        '{"action":"final","tool":null,"output":null}',
        '{"action":"final","tool":{"name":"echo","arguments":{"value":"x"}},"output":{"answer":"x"}}',
        '{"action":"tool","tool":{"name":"unknown","arguments":{"value":"x"}},"output":null}',
    ],
)
def test_invalid_decisions_never_execute(payload):
    handler = Mock()
    registry = ToolRegistry([spec(handler)])
    decision = registry.build_decision_model(Answer)
    with pytest.raises((ValueError, ValidationError, SchemaValidationError)):
        validate_response(payload, decision, response_schema(decision))
    handler.assert_not_called()


def test_expected_errors_are_fixed_observations_and_unexpected_errors_stop():
    def denied(args):
        raise ToolError("path_denied")

    result = ToolRegistry([spec(denied)]).execute("echo", {"value": "x"}, call_id="tool_001")
    assert not result.ok and result.error.code == "path_denied"

    for handler in (
        Mock(side_effect=RuntimeError("private-secret")),
        Mock(return_value={"result": 3}),
    ):
        with pytest.raises(RoutingError) as error:
            ToolRegistry([spec(handler)]).execute("echo", {"value": "x"}, call_id="tool_001")
        assert error.value.status_code == 500
        assert "private-secret" not in str(error.value)


def test_empty_registry_is_false_and_catalog_is_a_copy():
    assert not ToolRegistry([])
    registry = ToolRegistry([spec()])
    registry.catalog().clear()
    assert len(registry) == 1
