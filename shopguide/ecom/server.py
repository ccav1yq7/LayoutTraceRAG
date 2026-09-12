"""Start unmodified native tool functions over MCP, on a private data copy."""

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

from .upstream import verify


def load_server(root):
    server_dir = root / "agent/servers/offline"
    sys.path.insert(0, str(server_dir))
    spec = importlib.util.spec_from_file_location(
        "ecom_native_server", server_dir / "server.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--private-config", type=Path)
    parser.add_argument("--budget", type=Path)
    args = parser.parse_args()
    verify(args.upstream)
    module = load_server(args.upstream)
    # Keep get_image_info's original signature/template/data behavior; change transport only.
    import importlib

    vision: Any = importlib.import_module("tools.get_image_info")

    class VisionBridge:
        def generate_multimodal_response(self, messages):
            if not args.private_config or not args.budget:
                raise RuntimeError("VISION_MODEL_NOT_CONFIGURED")
            from types import SimpleNamespace

            from .gateway import ChatGateway, attach_images

            gateway = ChatGateway(args.private_config, args.budget, "vision")
            result = gateway.complete(attach_images(messages), [])
            if not result.get("content"):
                raise RuntimeError("EMPTY_VISION_RESPONSE")
            return SimpleNamespace(content=result["content"])

    vision.OnlineLLMApi = VisionBridge
    module.cache_dir = str(args.cache)
    module.data = module.get_data(str(args.cache))
    module.logger = module.set_up_logger()
    module.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
