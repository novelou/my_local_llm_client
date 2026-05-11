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
