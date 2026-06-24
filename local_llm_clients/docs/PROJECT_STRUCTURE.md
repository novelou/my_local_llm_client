# Project Structure

Python implementation lives under `local_llm_clients/`.

```text
chat_client_textual.py       Root launcher for chat Textual UI
agent_client_textual.py      Root launcher for file-agent Textual UI
mcp_client_textual.py        Root launcher for Unity MCP Textual UI

local_llm_clients/
  __init__.py         Package paths for config and sessions
  common.py          Shared llama-server HTTP client and session storage
  config/            Active config files and allowed tool presets
    examples/        Example config files
  sessions/          Runtime session history, ignored by git
  docs/              README and structure notes
  entrypoints/       Legacy non-Textual launchers kept out of root
  chat/
    cli.py           Tool-free chat CLI
    textual.py       Tool-free chat Textual UI
  agent/
    cli.py           Local file agent CLI and file tools
    textual.py       Local file agent Textual UI
  mcp/
    unity.py         Unity MCP HTTP client, tool schemas, and CLI
    textual.py       Unity MCP Textual UI
```

The project root intentionally keeps only the Textual launchers. Config files,
docs, legacy non-Textual entry points, and runtime sessions live under
`local_llm_clients/`.
