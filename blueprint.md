# LLM Functions Blueprint

> Comprehensive architectural overview of the llm-functions framework

## Overview

The llm-functions framework enables AIChat to execute custom tools and agents. It provides a standardized way to define, build, and run functions that AI models can invoke.

## Architecture

```
llm-functions/
├── agents/              # Agent implementations
│   └── <agent-name>/
│       ├── index.yaml   # Agent definition
│       ├── functions.json
│       ├── tools.txt    # Shared tools list
│       └── tools.{sh,js,py}
├── tools/               # Common tools
│   └── <tool-name>.{sh,js,py}
├── scripts/             # Build & utility scripts
├── docs/                # Documentation
├── cache/               # Runtime cache
├── bin/                 # Executables
├── Argcfile.sh         # Main build automation
└── functions.json      # Generated declarations
```

## Tool Definition

### Parameter Declaration

Tools define parameters using special comments that `Argcfile.sh` parses to generate JSON declarations.

#### Bash
```sh
# @describe Tool description
# @option --param-name! <TYPE> Description
# @flag --flag-name Description
# @env VAR_NAME=default Description
```

#### JavaScript
```js
/**
 * Tool description
 * @typedef {Object} Args
 * @property {string} param - Description
 * @param {Args} args
 */
exports.run = function(args) { ... }
```

#### Python
```py
def run(param: str, optional_param: Optional[str] = None):
    """Tool description
    Args:
        param: Description
        optional_param: Description
    """
```

### Parameter Types

| Suffix | Meaning |
|--------|---------|
| `!` | Required |
| `*` | Array (optional) |
| `+` | Required array |
| none | Optional |

### Output

- Write to `$LLM_OUTPUT` (Bash) or `process.env.LLM_OUTPUT` (JS/Python)
- Return values are auto-JSONified
- Use `$LLM_OUTPUT_COLOR` for TTY detection

## Agent Definition

### index.yaml Structure

```yaml
name: agent_name
description: Agent description
version: 0.1.0

instructions: |
  Agent behavior instructions...

variables:
  - name: var_name
    description: Description
    default: value

documents:
  - local-file.txt
  - local-dir/
  - https://example.com/file.txt

conversation_starters:
  - "What can you do?"
```

### Built-in Variables

| Variable | Description |
|----------|-------------|
| `__os__` | OS name |
| `__os_family__` | OS family |
| `__arch__` | Architecture |
| `__shell__` | Default shell |
| `__locale__` | Locale settings |
| `__now__` | ISO timestamp |
| `__cwd__` | Working directory |
| `__tools__` | Tool list |

## Environment Variables

### Injected by run-tool/run-agent

| Variable | Description |
|----------|-------------|
| `LLM_ROOT_DIR` | Path to llm-functions |
| `LLM_TOOL_NAME` | Current tool name |
| `LLM_TOOL_CACHE_DIR` | Tool cache directory |
| `LLM_AGENT_NAME` | Agent name |
| `LLM_AGENT_FUNC` | Agent function name |
| `LLM_AGENT_ROOT_DIR` | Agent directory |
| `LLM_AGENT_CACHE_DIR` | Agent cache directory |
| `LLM_OUTPUT_COLOR` | TTY color support |

### Injected by runtime

| Variable | Description |
|----------|-------------|
| `LLM_OUTPUT` | Output file path |
| `LLM_AGENT_VAR_<NAME>` | Agent variables |

### User-provided

| Variable | Description |
|----------|-------------|
| `LLM_DUMP_RESULTS` | Print results (regex) |
| `LLM_MCP_NEED_CONFIRM` | Require confirmation |
| `LLM_MCP_SKIP_CONFIRM` | Skip confirmation |

## Build Commands (Argcfile.sh)

```sh
# Build
argc build              # Build all
argc build@tool        # Build all tools
argc build@tool foo.sh # Build specific tools
argc build@agent       # Build all agents
argc build@agent todo  # Build specific agents

# Check
argc check             # Validate all
argc check@tool        # Validate tools
argc check@agent       # Validate agents

# Run
argc run@tool foo.sh '{"param":"value"}'
argc run@agent todo add_todo '{"desc":"Task"}'

# Test
argc test              # Test all
argc test@tool         # Test tools
argc test@agent        # Test agents

# Clean
argc clean
argc clean@tool
argc clean@agent

# Misc
argc link-to-aichat    # Link to aichat
argc version           # Show versions
```

## MCP Integration

```sh
argc mcp start    # Start MCP server
argc mcp stop     # Stop MCP server
argc mcp run@tool fs_read_file '{"path":"/tmp/file"}'
argc mcp logs     # Show logs
```

## Scripts

| Script | Purpose |
|--------|---------|
| `build-declarations.sh/py/js` | Generate functions.json |
| `run-tool.sh/py/js` | Execute tools |
| `run-agent.sh/py/js` | Execute agents |
| `validate_tools.sh/py` | Validate declarations |
| `generate_docs.py` | Generate markdown docs |
| `mcp.sh` | MCP bridge management |
| `check-deps.sh` | Check dependencies |
| `create-tool.sh` | Scaffold new tools |

## Key Files

- `Argcfile.sh` - Main entry point (like Makefile)
- `functions.json` - Generated tool declarations
- `mcp.json` - MCP server configuration

## Quick Start

1. Create tool in `tools/`:
```sh
argc create@tool mytool.sh param1! param2*
```

2. Build declarations:
```sh
argc build@tool mytool.sh
```

3. Link to aichat:
```sh
argc link-to-aichat
```

## Dependencies

- `argc` - CLI argument parser
- `jq` - JSON processor
- `node` - For JS tools
- `python3` - For Python tools

## Bybit Pro Suite Tool

The `tools/bybit_pro_suite.py` is a unified trading tool that combines all Bybit functionality:

### Features
- **Smart Order**: Auto position sizing based on risk %
- **Orderbook Analysis**: Volume imbalance detection
- **Trading Dashboard**: Account overview with risk metrics
- **Technical Indicators**: RSI, EMA calculations
- **Position Manager**: Move to break-even or auto-close
- **Trading Stop**: TP/SL with profit-after-fees logic

### Commands
```sh
python tools/bybit_pro_suite.py analyze_orderbook --symbol BTCUSDT
python tools/bybit_pro_suite.py trading_dashboard
python tools/bybit_pro_suite.py smart_order --symbol BTCUSDT --side Buy --risk-pct 1.0 --sl-dist 100
python tools/bybit_pro_suite.py set_position_mode --symbol BTCUSDT --mode 3
python tools/bybit_pro_suite.py position_manager --symbol BTCUSDT --action be
python tools/bybit_pro_suite.py set_trading_stop --symbol BTCUSDT --tp-usdt 50 --sl-usdt 25
```

### Environment Variables
| Variable | Description |
|----------|-------------|
| `BYBIT_API_KEY` | Bybit API Key |
| `BYBIT_API_SECRET` | Bybit API Secret |
| `BYBIT_TESTNET` | Use testnet (true/false) |
| `USE_TOR` | Use Tor proxy (true/false) |
| `TOR_PROXY` | Tor proxy URL |

### Dependencies
- `pybit` (optional, falls back to requests)
- `requests` (required for fallback mode)

## Trading Bot with Fibonacci VWAP

The `tools/trading_bot_vwap.py` is a complete trading bot with Ehlers SuperTrend + 5 Fibonacci VWAP Bands:

### Features
- **Ehlers SuperTrend Cross**: Adaptive trailing stop mechanism
- **5 Fibonacci VWAP Bands**: Dynamic support/resistance levels
- **Adaptive Position Sizing**: Risk adjusted by Fibonacci zone
- **Performance Tracking**: Per-band win rate and P&L

### Commands
```sh
python tools/trading_bot_vwap.py --symbol BTCUSDT --period 10 --multiplier 3.0 --risk-pct 1.0
python tools/trading_bot_vwap.py --demo --iterations 100
python tools/trading_bot_vwap.py --initial-balance 10000 --risk-pct 2.0
```

### Fibonacci Bands
| Band | Risk Multiplier | Win Bias |
|------|-----------------|----------|
| FIB_0236 | 0.5x | 55% |
| FIB_0382 | 0.75x | 52% |
| FIB_0500 | 1.0x | 50% |
| FIB_0618 | 1.25x | 48% |
| FIB_0786 | 1.5x | 45% |

## Trading Bot with Batch Limit Orders

The `tools/trading_bot_batch.py` is a complete trading bot with batch limit order management:

### Features
- **Batch Limit Orders**: Place multiple orders simultaneously
- **Grid Trading**: Create buy/sell grids around current price
- **Fibonacci Grid**: Orders at Fibonacci band levels
- **Order Lifecycle Management**: Track active, filled, cancelled orders

### Commands
```sh
python tools/trading_bot_batch.py --symbol BTCUSDT --demo
python tools/trading_bot_batch.py --grid-levels 5 --grid-spacing 0.01
python tools/trading_bot_batch.py --iterations 100 --initial-balance 10000
```

### Batch Order Methods
- `create_batch_orders()` - Create orders at price levels
- `create_grid_orders()` - Create grid around price
- `create_fibonacci_grid()` - Orders at Fibonacci bands
- `place_batch()` - Place all orders in batch
- `cancel_all_orders()` - Cancel all active orders