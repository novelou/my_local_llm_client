# Local LLM Textual Clients

This repository contains small local-LLM clients for an OpenAI-compatible
`/v1/chat/completions` server such as `llama-server`.

The project root intentionally keeps only the Textual launchers:

| Launcher | Purpose |
| --- | --- |
| `chat_client_textual.py` | Tool-free local chat UI |
| `agent_client_textual.py` | Local file-agent UI |
| `mcp_client_textual.py` | Unity MCP tool UI |

Implementation, config, docs, and legacy CLI entry points live under
`local_llm_clients/`.

## Quick Start

Install Textual if needed:

```powershell
py -3 -m pip install textual
```

Create or use the bundled config files, then launch one client:

```powershell
python chat_client_textual.py
python agent_client_textual.py
python mcp_client_textual.py
```

The default config files are stored in `local_llm_clients/config/`:

```text
local_llm_clients/config/chat-client.config.json
local_llm_clients/config/agent-client.config.json
local_llm_clients/config/mcp-client.config.json
```

Example configs are in `local_llm_clients/config/examples/`.

## Environment

All clients use:

```powershell
$env:LLAMA_BASE_URL="http://127.0.0.1:8081/v1/"
$env:LLAMA_MODEL="local-model"
```

The Unity MCP client also uses:

```powershell
$env:UNITY_MCP_URL="http://127.0.0.1:8080/mcp"
```

## Sessions

Conversation history is saved under `local_llm_clients/sessions/`, which is
ignored by git.

## Detailed Docs

- [Project structure](local_llm_clients/docs/PROJECT_STRUCTURE.md)
- [Chat client](local_llm_clients/docs/Readme.chat.md)
- [Local file agent](local_llm_clients/docs/Readme.agent.md)
- [Unity MCP client](local_llm_clients/docs/readme.mcp_client.md)

## Legacy CLI Entry Points

The non-Textual CLI launchers are kept out of the root:

```text
local_llm_clients/entrypoints/chat_client.py
local_llm_clients/entrypoints/agent_client.py
local_llm_clients/entrypoints/mcp_unity_client.py
```
