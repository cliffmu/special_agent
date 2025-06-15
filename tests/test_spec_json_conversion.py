import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import voluptuous as vol
from agent_core import _spec_to_json, ToolSpec


async def dummy_func(force: bool = False):
    return "done"


SPEC = ToolSpec(
    name="build_vector_index",
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


