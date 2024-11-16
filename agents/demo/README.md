# Demo

This agent serves as a demo to guide agent development and showcase various agent capabilities.

## index.yaml

This file defines the agent.

### variables

Variables are generally used to store information about a user's behavior or preferences.

```yaml
variables:
  - name: foo
    description: This is a foo
  - name: bar
    description: This is a bar with default value
    default: val
```

When use define variables, please avoid these built-in variables:

| name            | description                                   | example                  |
| :-------------- | :-------------------------------------------- | :----------------------- |
| `__os__`        | Operating system name                         | linux                    |
| `__os_family__` | Operating system family                       | unix                     |
| `__arch__`      | System architecture                           | x86_64                   |
| `__shell__`     | Current user's default shell                  | bash                     |
| `__locale__`    | User's preferred language and region settings | en-US                    |
| `__now__`       | Current timestamp in ISO 8601 format          | 2024-07-29T08:11:24.367Z |
| `__cwd__`       | Current working directory                     | /tmp                     |
| `__tools__`     | List of agent tools                                 |                          |

Variables can be used in the `instructions` and tools script.

```yaml
instructions: |
  The instructions can access variables {{foo}} and {{bar}}.
```

```sh
echo "The tools script can access environment variables $LLM_AGENT_VAR_FOO and $LLM_AGENT_VAR_BAR"
```

### documents

Documents are used for RAG, supporting local files/dirs and remote URLs.

```yaml
documents:
  - local-file.txt
  - local-dir/
  - https://example.com/remote-file.txt
```

> All local files and directories are relative to the agent directory (where index.yaml is located).

## tools.{sh,js,py}

The tool script implements agent-specific tools.

## tools.txt

The `tools.txt` file enables tool reuse from the `/tools` folder in this project.
