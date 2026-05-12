# Unity MCP Client

`mcp_unity_client.py` は、ローカル LLM と Unity MCP の Streamable HTTP server をつなぐ軽量 CLI です。

```text
.gguf -> llama-server -> mcp_unity_client.py -> unity-mcp
          /v1/chat/completions       /mcp
```

## Requirements

- Python 3.11+
- OpenAI 互換 API として起動しているローカル LLM server
- Streamable HTTP として起動している Unity MCP server

既定値:

- LLM endpoint: `http://127.0.0.1:8081/v1/`
- model: `local-model`
- Unity MCP endpoint: `http://127.0.0.1:8080/mcp`
- config: `mcp-client.config.json`
- sessions: `.mcp-client/sessions/`

## Setup

設定ファイルを作成します。

```powershell
python mcp_unity_client.py --init-config
```

必要に応じて `mcp-client.config.json` を編集します。設定例は `mcp-client.config.example.json` です。

```json
{
  "llama_base_url": "http://127.0.0.1:8081/v1/",
  "llama_model": "local-model",
  "mcp_url": "http://127.0.0.1:8080/mcp",
  "temperature": 0.2,
  "request_timeout": 120,
  "max_tool_rounds": 8,
  "max_invalid_tool_retries": 0
}
```

環境変数でも上書きできます。

```powershell
$env:LLAMA_BASE_URL="http://127.0.0.1:8081/v1/"
$env:LLAMA_MODEL="local-model"
$env:UNITY_MCP_URL="http://127.0.0.1:8080/mcp"
```

## Start

Unity MCP server と LLM server を起動した状態で実行します。

```powershell
python mcp_unity_client.py
```

## Commands

```text
/help                  Show help
/tools                 List Unity MCP tools
/call NAME {json}      Call a Unity MCP tool directly
/sessions              List saved sessions
/load SESSION_ID       Load a saved session
/new                   Start a fresh session
/config                Show active endpoints
/quit                  Save and exit
```

## Direct Tool Calls

会話に入らず Unity MCP tool を直接確認できます。

```powershell
python mcp_unity_client.py --list-tools
python mcp_unity_client.py --call-tool manage_scene --arguments "{""action"":""get_active""}"
```

対話中にも直接呼び出せます。

```text
/tools
/call manage_scene {"action":"get_active"}
/call manage_camera {"action":"screenshot","include_image":false}
```

長い JSON はファイルにして渡せます。

```powershell
python mcp_unity_client.py --call-tool manage_scene --arguments @args.json
```

## Validation And Repair

ローカル LLM は tool call の JSON や引数型を崩すことがあります。この client は Unity MCP に送る前に、最低限の正規化と検証を行います。

- JSON 文字列として返された引数を必要に応じて復元します。
- `execute_code` の code 引数など、schema 上 object になりやすい値を文字列へ補正します。
- `manage_gameobject` でユーザー文から明確に分かる場合、`action=create`, `primitive_type`, `position` などを補正します。
- `action` は `null`, `"null"`, `"unknown"` などの欠損 placeholder のときだけ拒否します。
- `action` 以外の enum 値、必須引数、型不一致、placeholder は実行前に拒否します。

例:

```text
make sphere at (0,0,0)
```

モデルが `action=null, primitive_type=null` のように返した場合でも、文脈から明確なら `action=create, primitive_type=Sphere, position=[0,0,0]` に補正します。

## Tool Fallback

接続先モデルや server が OpenAI の `tools` field に対応していない場合、client は JSON tool fallback に切り替えます。その場合、モデルは次の形だけを返すことで tool を実行できます。

```json
{"tool":"manage_scene","arguments":{"action":"get_active"}}
```
