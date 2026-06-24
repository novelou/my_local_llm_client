# Local File Agent Client

`agent_client.py` は、ローカル LLM にファイル操作 tool を渡す CLI です。`/set_directory` で作業ディレクトリを指定し、その内側のファイルだけを読み書きできます。

```text
.gguf -> llama-server -> agent_client.py -> active working directory
          /v1/chat/completions
```

## Requirements

- Python 3.11+
- OpenAI 互換 API として起動しているローカル LLM server

既定値:

- LLM endpoint: `http://127.0.0.1:8081/v1/`
- model: `local-model`
- config: `local_llm_clients/config/agent-client.config.json`
- sessions: `local_llm_clients/sessions/agent/`

## Setup

設定ファイルを作成します。

```powershell
python agent_client_textual.py --init-config
```

必要に応じて `agent-client.config.json` を編集します。設定例は `agent-client.config.example.json` です。

```json
{
  "llama_base_url": "http://127.0.0.1:8081/v1/",
  "llama_model": "local-model",
  "temperature": 0.2,
  "request_timeout": 120,
  "max_tool_rounds": 8,
  "max_invalid_tool_retries": 0,
  "workdir": "."
}
```

環境変数でも上書きできます。

```powershell
$env:LLAMA_BASE_URL="http://127.0.0.1:8081/v1/"
$env:LLAMA_MODEL="local-model"
```

## Start

```powershell
python agent_client_textual.py
```

起動時に作業ディレクトリを指定する場合:

```powershell
python agent_client_textual.py --set-directory F:\path\to\project
```

起動後に変更する場合:

```text
/set_directory F:\path\to\project
```

## Commands

```text
/help                         Show help
/multiline                    Enter a multi-line prompt; finish with a line containing only .
/set_directory PATH           Set the active working directory
/pwd                          Show the active working directory
/tools                        List local file tools
/call NAME {json}             Call a local file tool directly
/sessions                     List saved sessions
/load SESSION_ID              Load a saved session
/new                          Start a fresh session
/config                       Show active config
/quit                         Save and exit
```

## Tools

LLM が使える local tools は次の通りです。すべての path は active working directory からの相対パスです。

| Tool | Description |
| --- | --- |
| `list_files` | ファイルとディレクトリを一覧表示 |
| `search_files` | ファイル名・ディレクトリ名を検索 |
| `search_text` | UTF-8 テキストファイル内の文字列を検索 |
| `read_file` | UTF-8 テキストファイルを読み込み |
| `write_file` | UTF-8 テキストファイルを作成または上書き |
| `replace_text` | ファイル内の完全一致テキストを置換 |
| `append_file` | ファイル末尾にテキストを追記 |
| `delete_file` | ファイルを 1 つ削除 |
| `make_directory` | ディレクトリを作成 |

複数行の依頼を送る場合は `/multiline` を入力し、本文の最後に `.` だけの行を入力します。
`search_text` の `query` は部分一致です。`file_pattern` は `*.tjs` のようなファイル名 glob、または `system/AnimationLayer.tjs` のような相対パスで指定できます。

## Direct Tool Calls

会話に入らず tool だけ確認できます。

```powershell
python agent_client_textual.py --set-directory . --call-tool list_files --arguments "{""path"":""."",""max_entries"":20}"
python agent_client_textual.py --set-directory . --call-tool read_file --arguments "{""path"":""README.md""}"
```

長い JSON はファイルにして渡せます。

```powershell
python agent_client_textual.py --call-tool write_file --arguments @args.json
```

`args.json`:

```json
{
  "path": "notes/example.txt",
  "content": "hello\n"
}
```

## Safety

- 絶対パスは file tools の引数として拒否されます。
- `../` などで active working directory の外へ出る path は拒否されます。
- `path`, `old_text` などの必須値に `"null"`, `"unknown"`, 空文字などの placeholder が入った場合は実行前に拒否されます。
- `content` と `new_text` は空文字が正当な操作になるため許可されます。

## Tool Fallback

接続先モデルや server が OpenAI の `tools` field に対応していない場合、client は JSON tool fallback に切り替えます。その場合、モデルは次の形だけを返すことで tool を実行できます。

```json
{"tool":"read_file","arguments":{"path":"README.md"}}
```
