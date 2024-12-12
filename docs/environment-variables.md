# Environment Variables

## Injected by `run-tool.*`/`run-agent.*`

| Name                  | Description                                                                                                          |
| --------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `LLM_ROOT_DIR`        | Path to `<llm-functions-dir>`                                                                                        |
| `LLM_TOOL_NAME`       | Tool name, such as `execute_command`                                                                                 |
| `LLM_TOOL_CACHE_DIR`  | Path to `<llm-functions-dir>/cache/<tool-name>`,<br>The tool script can use this directory to store some cache data  |
| `LLM_AGENT_NAME`      | Agent name, such as `todo`                                                                                           |
| `LLM_AGENT_FUNC`      | Agent function, such as `list_todos`                                                                                 |
| `LLM_AGENT_ROOT_DIR`  | Path to `<llm-functions-dir>/agents/<agent-name>`                                                                    |
| `LLM_AGENT_CACHE_DIR` | Path to `<llm-functions-dir>/cache/<agent-name>`,<br>The tool script can use this directory to store some cache data |

## Injected by runtime (AIChat)

| Name                   | Description                                          |
| ---------------------- | ---------------------------------------------------- |
| `LLM_OUTPUT`           | File to store the the execution results of the tool. |
| `LLM_AGENT_VAR_<NAME>` | Agent variables.                                     |

## Provided by users

| Name               | Description                                                                                   |
| ------------------ | --------------------------------------------------------------------------------------------- |
| `LLM_DUMP_RESULTS` | Controls whether to print the execution results of the tool, e.g. `get_current_weather\|fs.*` |
