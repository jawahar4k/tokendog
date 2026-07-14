from __future__ import annotations

def dense_tool_schema(name: str, description: str, params: dict) -> dict:
    """params: {param_name: (json_type, required_bool, description)}."""
    properties = {}
    required = []
    for pname, (ptype, req, desc) in params.items():
        properties[pname] = {"type": ptype, "description": desc}
        if req:
            required.append(pname)
    input_schema: dict = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required
    return {"name": name, "description": description, "inputSchema": input_schema}
