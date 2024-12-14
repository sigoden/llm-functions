# Agent

## folder structure

The agent follows a specific organizational structure to ensure streamlined functionality and easy access to essential files:
```
└── agents
    └── myagent
        ├── functions.json                  # Auto-generated JSON declarations for functions
        ├── index.yaml                      # Main agent definition file
        ├── tools.txt                       # List of shared tools
        └── tools.{sh,js,py}                # Scripts implementing agent-specific tools
```

## index.yaml

This is the main definition file for your agent where you provide all essential information and configuration for the agent.

### metadata

Metadata provides basic information about the agent:

- `name`: A unique name for your agent, which helps in identifying and referencing the agent.
- `description`: A brief explanation of what the agent is or its primary purpose.
- `version`: The version number of the agent, which helps track changes or updates to the agent over time.

```yaml
name: TestAgent                             
description: This is test agent
version: 0.1.0
```

### instructions

Defines the initial context or behavior directives for the agent:

```yaml
instructions: You are a test ai agent to ... 
```

### variables

Variables store user-related data, such as behavior or preferences. Below is the syntax for defining variables:

```yaml
variables:
  - name: foo
    description: This is a foo
  - name: bar
    description: This is a bar with default value
    default: val
```
> For sensitive information such as api_key, client_id, client_secret, and token, it's recommended to use environment variables instead of agent variables.

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

Variables can be used within `instructions` and within tool scripts:

```yaml
instructions: |
  The instructions can access user-defined variables: {{foo}} and {{bar}}, or built-in variables: {{__cwd__}}
```

```sh
echo "he tools script can access user-defined variables in environment variables: $LLM_AGENT_VAR_FOO and $LLM_AGENT_VAR_BAR"
```

### documents

A list of resources or references that the agent can access. Documents are used for building RAG.

```yaml
documents:
  - local-file.txt
  - local-dir/
  - https://example.com/remote-file.txt
```

> All local files and directories are relative to the agent directory (where index.yaml is located).

### conversation_starters

Define Predefined prompts or questions that users can ask to initiate interactions or conversations with the agent.
This helps provide guidance for users on how to engage with the agent effectively.

 ```yaml
 conversation_starters:
   - What can you do?
 ```

## tools.{sh,js,py}

Scripts for implementing tools tailored to the agent's unique requirements.

## tools.txt

`tools.txt` facilitates the reuse of tools specified in the `/tools` directory within this project.
