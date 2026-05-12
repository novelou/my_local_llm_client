<<<<<<< ours
# Lightweight Unity MCP Client

ローカル LLM と Unity MCP をつなぐ、依存なしの軽量 CLI です。

```text
.gguf -> llama-server -> mcp_unity_client.py -> unity-mcp
          127.0.0.1:8081/v1/       127.0.0.1:8080/mcp
```

## 前提

- Python 3.11+
- `llama-server` が OpenAI 互換 API として `http://127.0.0.1:8081/v1/` で起動していること
- Unity MCP が Streamable HTTP として `http://127.0.0.1:8080/mcp` で起動していること

## 起動

```powershell
python mcp_unity_client.py --init-config
python mcp_unity_client.py
```

設定を変える場合は `mcp-client.config.json` を編集します。環境変数でも上書きできます。

```powershell
$env:LLAMA_BASE_URL="http://127.0.0.1:8081/v1/"
$env:LLAMA_MODEL="local-model"
$env:UNITY_MCP_URL="http://127.0.0.1:8080/mcp"
python mcp_unity_client.py
```

## CLI コマンド

```text
/help                  コマンド一覧
/tools                 Unity MCP の tool 一覧
/call NAME {json}      MCP tool を直接呼び出す
/sessions              保存済みセッション一覧
/load SESSION_ID       保存済みセッションをロード
/new                   新しいセッションを開始
/config                接続先と保存先を表示
/quit                  保存して終了
```

会話履歴は `.mcp-client/sessions/` に JSON として保存されます。Codex CLI 風に、同じ会話をロードして続きから作業できます。

LLM が `{"action":"null"}` のような無効な placeholder を出した場合、クライアントは Unity MCP に送る前に拒否します。既定では `max_invalid_tool_retries` が `0` なので、そのターンは即停止してユーザーへ確認を返します。モデルに再試行させたい場合だけ `1` 以上にしてください。

`make sphere at (0,0,0)` のようにユーザー文から意図が明確な場合は、`manage_gameobject` の `null` 引数を最小限だけ補正します。たとえば `action=null, primitive_type=null` は `action=create, primitive_type=Sphere, position=[0,0,0]` に修復されます。

LM Studio と llama-server で挙動が違う場合、多くはモデルそのものより chat template、function calling 形式、JSON/tool call の整形処理の差です。このクライアント側では、弱い tool calling 出力を前提に正規化と安全な補正を入れています。

## 直接 tool を試す例

対話 CLI に入らず、疎通だけ確認することもできます。

```powershell
python mcp_unity_client.py --list-tools
python mcp_unity_client.py --call-tool manage_scene --arguments "{""action"":""get_active""}"
```

長い引数は JSON ファイルにして `--arguments @args.json` と渡せます。

対話中は次のように実行できます。

```text
/tools
/call manage_scene {"action":"get_active"}
/call manage_camera {"action":"screenshot","include_image":false}
```

## 補足

llama-server の function calling 対応モデルでは OpenAI 互換の `tools` を使います。モデルが tool call を返さない場合でも、LLM が次のような JSON だけを返せばフォールバックで tool を実行します。

```json
{"tool":"manage_scene","arguments":{"action":"get_active"}}
```
=======
# Local LLM Client Tools

ローカル LLM を `llama-server` などの OpenAI 互換 API 経由で呼び出し、外部操作を tool として実行するための軽量 CLI 群です。

このリポジトリには、用途の違う 2 つのクライアントがあります。

```text
.gguf / local model
  -> llama-server or compatible /v1/chat/completions
    -> agent_client.py        -> local files
    -> mcp_unity_client.py    -> Unity MCP
```

## Clients

| File | Purpose | Details |
| --- | --- | --- |
| `agent_client.py` | `/set_directory` で指定した作業ディレクトリ内のファイルを読み書きするローカル agent | [Readme.agent.md](Readme.agent.md) |
| `mcp_unity_client.py` | Unity MCP の Streamable HTTP server に接続し、Unity の MCP tools を呼び出す client | [readme.mcp_client.md](readme.mcp_client.md) |

## Requirements

- Python 3.11+
- OpenAI 互換 API として起動しているローカル LLM server
  - 例: `llama-server`, LM Studio, Ollama の OpenAI-compatible endpoint など
- Unity MCP client を使う場合のみ、Unity MCP の Streamable HTTP server

## Quick Start

ローカルファイル agent:

```powershell
python agent_client.py --init-config
python agent_client.py
```

Unity MCP client:

```powershell
python mcp_unity_client.py --init-config
python mcp_unity_client.py
```

設定値は各 config JSON か環境変数で変更できます。

```powershell
$env:LLAMA_BASE_URL="http://127.0.0.1:8081/v1/"
$env:LLAMA_MODEL="local-model"
```

Unity MCP client では追加で次を使えます。

```powershell
$env:UNITY_MCP_URL="http://127.0.0.1:8080/mcp"
```

## Files

- `agent_client.py`: local file agent CLI
- `agent-client.config.example.json`: local file agent の設定例
- `mcp_unity_client.py`: Unity MCP bridge CLI
- `mcp-client.config.example.json`: Unity MCP client の設定例
- `.agent-client/sessions/`: local file agent の会話履歴
- `.mcp-client/sessions/`: Unity MCP client の会話履歴
>>>>>>> theirs
