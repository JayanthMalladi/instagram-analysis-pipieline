"""Launcher for Claude Desktop: runs the Instagram pipeline as a local MCP server.

Point Claude Desktop at this file (see README). Running it as a script puts
this folder on the import path, so no install step or PYTHONPATH is needed.
"""

from instagram_pipeline.cli import main

raise SystemExit(main(["mcp"]))
