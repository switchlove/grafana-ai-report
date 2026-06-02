# grafana-ai

AI-powered health reports for an AlmaLinux mirror server. Fetches Prometheus
metrics via the Grafana HTTP API, forwards them to an OpenAI model for
analysis, saves a structured report to disk, and optionally posts a summary
embed to a Discord channel.

---

## Features

- Fetches 19 node-exporter metrics (CPU, RAM, disk, network, repo sync health)
- Dynamic Grafana step and pandas resample scaled to the requested time window
- Streaming OpenAI response with token cost tracking
- Severity-based exit code (`0` LOW/LOWEST · `1` MEDIUM · `2` HIGH/HIGHEST)
- Per-run comparison against a previous report with window-mismatch warning
- Discord webhook with colour-coded severity embed and custom guild emoji support
- Full CLI — see [Usage](#usage)

---

## Requirements

- Python 3.10+
- A running Grafana instance with a Prometheus datasource
- An OpenAI API key
- *(Optional)* A Discord webhook URL

```
pip install -r requirements.txt
```

---

## Setup

```bash
git clone https://github.com/YOUR_ORG/grafana-ai.git
cd grafana-ai
pip install -r requirements.txt
cp .env.example .env
$EDITOR .env          # fill in real values
```

---

## Configuration

All configuration lives in `.env` (never committed — see `.gitignore`).

| Variable | Required | Description |
|---|---|---|
| `GRAFANA_URL` | ✅ | Base URL of your Grafana instance, e.g. `http://localhost:4000` |
| `GRAFANA_TOKEN` | ✅ | Grafana service account token (Settings → Service Accounts) |
| `DATA_SOURCE_UID` | ✅ | UID of the Prometheus datasource in Grafana |
| `OPENAI_API_KEY` | ✅ | OpenAI API key |
| `REPORT_DIR` | ✅ | Directory where reports are saved, e.g. `/var/log/grafana_ai` |
| `DISCORD_WEBHOOK_URL` | ➖ | Discord webhook URL for post-run notifications |
| `DISCORD_EMOJI_HIGHEST` | ➖ | Guild emoji ID for HIGHEST severity |
| `DISCORD_EMOJI_HIGH` | ➖ | Guild emoji ID for HIGH severity |
| `DISCORD_EMOJI_MEDIUM` | ➖ | Guild emoji ID for MEDIUM severity |
| `DISCORD_EMOJI_LOW` | ➖ | Guild emoji ID for LOW severity |
| `DISCORD_EMOJI_LOWEST` | ➖ | Guild emoji ID for LOWEST severity |

To find a guild emoji ID, type `\:EmojiName:` in any Discord message — it
expands to `<:Name:123456789>` and the number is the ID.

---

## Config file

Persistent defaults can be set in `~/.grafana_ai.toml` so you don't need to
repeat common flags on every run. CLI flags always override config file values.

```toml
# ~/.grafana_ai.toml
window          = "24h"
model           = "gpt-4.1"
alert_threshold = "MEDIUM"
quiet           = false
no_discord      = false
```

Copy `grafana_ai.toml.example` from the repo as a starting point. Supported
keys: `window`, `model`, `alert_threshold`, `quiet`, `no_discord`.

---

## Usage

```
python3 grafana_ai.py [OPTIONS]
```

### Options

| Flag | Description |
|---|---|
| `--window WINDOW` | Time window to analyse (`6h`, `24h`, `7d`, …). Default: `24h` |
| `--model MODEL` | OpenAI model to use. Default: `gpt-4.1` |
| `--output FILE` | Write report to a specific path instead of the auto-named file in `REPORT_DIR` |
| `--since FILENAME` | Use a specific saved report as the previous-report context instead of the most recent |
| `--prompt-extra TEXT` | Append a free-form operator note to the AI prompt (e.g. known maintenance windows) |
| `--alert-threshold SEVERITY` | Only send Discord notification when highest severity ≥ `SEVERITY` (`HIGHEST`/`HIGH`/`MEDIUM`/`LOW`/`LOWEST`) |
| `--no-discord` | Skip the Discord webhook even when `DISCORD_WEBHOOK_URL` is set |
| `--quiet` | Suppress all progress output; print only the report and errors |
| `--dry-run` | Fetch and preview Grafana data without calling OpenAI |
| `--list-reports` | List saved reports with filename, window, and size |
| `--show-report [FILENAME]` | Print a saved report to stdout (omit `FILENAME` for the most recent) |

### Examples

```bash
# Standard 24-hour report, Discord notification only for MEDIUM+
python3 grafana_ai.py --alert-threshold MEDIUM

# 7-day report with a custom model, no Discord spam
python3 grafana_ai.py --window 7d --model gpt-4o --no-discord

# Compare against a known-good baseline from last week
python3 grafana_ai.py --since 2026-05-26_0600_24h.txt

# Annotate a run with context the AI should factor in
python3 grafana_ai.py --prompt-extra "Maintenance window ran 02:00-04:00 UTC, ignore the traffic drop"

# Quiet mode for cron — exit code reflects severity
python3 grafana_ai.py --quiet
echo "Exit: $?"   # 0=LOW/LOWEST  1=MEDIUM  2=HIGH/HIGHEST

# Verify the Grafana connection without spending any tokens
python3 grafana_ai.py --dry-run

# List recent reports, then view one
python3 grafana_ai.py --list-reports
python3 grafana_ai.py --show-report 2026-06-01_2109_24h.txt
```

---

## Report storage

Reports are saved as plain text to `REPORT_DIR` with filenames in the format:

```
YYYY-MM-DD_HHMM_<window>.txt
```

The previous report is automatically loaded and provided to the AI as context
on each run. Use `--since` to pin a specific baseline.

---

## File structure

```
grafana_ai/
├── grafana_ai.py        # Entry point, argument parsing, orchestration
├── config.py            # Loads .env, exposes all constants
├── metrics.py           # Grafana query definitions, fetch, pandas resampling
├── prompt.py            # System prompt and message builder
├── discord_notify.py    # Discord embed builder and webhook poster
├── requirements.txt
├── .env.example             # Template — copy to .env and fill in values
├── grafana_ai.toml.example  # Template — copy to ~/.grafana_ai.toml to set defaults
└── .gitignore
```
