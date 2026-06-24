# Tool-free Local LLM Chat Client

`chat_client_textual.py` is the root launcher for the tool-free chat Textual UI.
The legacy CLI lives at `local_llm_clients/entrypoints/chat_client.py`. It connects
to a local OpenAI-compatible chat-completions endpoint and saves conversation
sessions, but does not expose file tools or MCP tools to the model.

## Setup

```powershell
python chat_client_textual.py --init-config
python chat_client_textual.py
```

The default config file is `local_llm_clients/config/chat-client.config.json`.
Examples live under `local_llm_clients/config/examples/`.

Environment variables `LLAMA_BASE_URL` and `LLAMA_MODEL` override the config.

## Multi-line input

Run `/multiline`, enter any number of lines, and finish with a line containing
only `.`:

```text
user> /multiline
Enter multi-line input. Finish with a line containing only .
... First line
... Second line
... .
```

## Commands

```text
/help                  Show help
/multiline             Enter a multi-line prompt; finish with .
/sessions              List saved sessions
/load SESSION_ID       Load a saved session
/new                   Start a fresh session
/config                Show active config
/quit                  Save and exit
```

Sessions are saved under `local_llm_clients/sessions/chat/`.
