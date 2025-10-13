import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import voluptuous as vol
from agent_core import _spec_to_json, ToolSpec


async def dummy_func(force: bool = False):
    return "done"


SPEC = ToolSpec(
    name="build_device_index",
    description="Rebuild index",
    parameters=vol.Schema({vol.Optional("force", default=False): bool}),
    returns="done",
    func=dummy_func,
)


def test_bool_parameter_serialized_as_boolean():
    js = _spec_to_json(SPEC)
    assert (
        js["function"]["parameters"]["properties"]["force"]["type"] == "boolean"
    )


def test_array_and_object_serialization():
    complex_spec = ToolSpec(
        name="dummy", 
        description="",
        parameters=vol.Schema(
            {
                vol.Required("targets"): [str],
                vol.Optional("opts"): {vol.Required("mode"): str},
            }
        ),
        returns=None,
        func=dummy_func,
    )
    js = _spec_to_json(complex_spec)
    params = js["function"]["parameters"]
    assert params["properties"]["targets"]["type"] == "array"
    assert params["properties"]["targets"]["items"]["type"] == "string"
    assert params["properties"]["opts"]["type"] == "object"
    assert params["properties"]["opts"]["properties"]["mode"]["type"] == "string"
    assert "targets" in params["required"]


