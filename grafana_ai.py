import argparse
import pathlib
import re
import sys
from datetime import datetime, timezone

from openai import OpenAI

from config import (
    DATA_SOURCE_UID,
    DISCORD_WEBHOOK_URL,
    GRAFANA_TOKEN,
    GRAFANA_URL,
    OPENAI_API_KEY,
    REPORT_DIR,
)
from discord_notify import send_discord_summary
from metrics import fetch_and_process
from prompt import build_messages

# (input $/M tokens, output $/M tokens) — update when OpenAI changes pricing
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4.1":       (2.00,  8.00),
    "gpt-4.1-mini":  (0.40,  1.60),
    "gpt-4.1-nano":  (0.10,  0.40),
    "gpt-4o":        (2.50, 10.00),
    "gpt-4o-mini":   (0.15,  0.60),
    "o4-mini":       (1.10,  4.40),
}


_SEVERITY_RANK: dict[str, int] = {
    "HIGHEST": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "LOWEST": 1,
}


def _max_severity(report_text: str) -> str | None:
    """Return the highest severity label found in report_text, or None."""
    found = set(re.findall(r'\[(HIGHEST|HIGH|MEDIUM|LOW|LOWEST)\]', report_text))
    return max(found, key=lambda s: _SEVERITY_RANK[s], default=None)


def _severity_exit_code(report_text: str) -> int:
    """Return 2 for HIGH/HIGHEST, 1 for MEDIUM, 0 for LOW/LOWEST or nothing found."""
    sev = _max_severity(report_text)
    if sev in ("HIGH", "HIGHEST"):
        return 2
    if sev == "MEDIUM":
        return 1
    return 0


def _cmd_list_reports() -> None:
    """Print a table of saved reports and exit."""
    if not REPORT_DIR.exists():
        raise SystemExit(f"Report directory does not exist: {REPORT_DIR}")
    reports = sorted(REPORT_DIR.glob("*.txt"), reverse=True)
    if not reports:
        print(f"No reports found in {REPORT_DIR}")
        return
    # Column widths
    name_w   = max(len(r.name) for r in reports)
    window_w = len("Window")
    print(f"{'Filename':<{name_w}}  {'Window':>{window_w}}  {'Size':>8}  Path")
    print("-" * (name_w + window_w + 22))
    for r in reports:
        # Filename format: YYYY-MM-DD_HHMM_<window>.txt
        parts = r.stem.split("_")
        window = parts[-1] if len(parts) >= 3 else "—"
        size_kb = r.stat().st_size / 1024
        print(f"{r.name:<{name_w}}  {window:>{window_w}}  {size_kb:>6.1f}KB  {r}")


def _cmd_show_report(filename: str) -> None:
    """Print a saved report to stdout. Pass 'LATEST' to show the most recent."""
    if not REPORT_DIR.exists():
        raise SystemExit(f"Report directory does not exist: {REPORT_DIR}")
    if filename == "LATEST":
        reports = sorted(REPORT_DIR.glob("*.txt"), reverse=True)
        if not reports:
            raise SystemExit(f"No reports found in {REPORT_DIR}")
        path = reports[0]
    else:
        path = REPORT_DIR / filename
        if not path.exists():
            path = REPORT_DIR / (filename + ".txt")
        if not path.exists():
            raise SystemExit(f"Report not found: {filename}")
    print(f"=== {path.name} ===")
    print(path.read_text(encoding="utf-8"))


def _cmd_dry_run(window: str) -> None:
    """Fetch and process Grafana data, print stats, then exit without calling OpenAI."""
    print(f"[dry-run] Fetching Grafana data for window={window} ...")
    ai_ready_data = fetch_and_process(window, GRAFANA_URL, GRAFANA_TOKEN, DATA_SOURCE_UID)
    char_count = len(ai_ready_data)
    print(f"[dry-run] Dataset ready — {char_count:,} characters")
    print(f"\n[dry-run] First 500 characters:\n{'-' * 40}")
    print(ai_ready_data[:500])


def main() -> None:
    parser = argparse.ArgumentParser(description="AlmaLinux Mirror AI Health Report")
    parser.add_argument(
        "--list-reports", action="store_true",
        help="List saved reports and exit.",
    )
    parser.add_argument(
        "--show-report", nargs="?", const="LATEST", metavar="FILENAME",
        help="Print a saved report to stdout. Omit FILENAME for the most recent.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch Grafana data and print a sample; skip OpenAI and Discord.",
    )
    parser.add_argument(
        "--no-discord", action="store_true",
        help="Skip the Discord webhook even when DISCORD_WEBHOOK_URL is configured.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output; print only the report and errors.",
    )
    parser.add_argument(
        "--alert-threshold",
        choices=["HIGHEST", "HIGH", "MEDIUM", "LOW", "LOWEST"],
        metavar="SEVERITY",
        help="Only send Discord when highest severity >= SEVERITY (HIGHEST/HIGH/MEDIUM/LOW/LOWEST).",
    )
    parser.add_argument(
        "--window", default="24h",
        help="Grafana time window to analyse (e.g. 6h, 24h, 7d). Default: 24h",
    )
    parser.add_argument(
        "--model", default="gpt-4.1",
        help="OpenAI model to use. Default: gpt-4.1",
    )
    parser.add_argument(
        "--prompt-extra", metavar="TEXT",
        help="Append a free-form operator note to the AI user message (e.g. known maintenance windows).",
    )
    parser.add_argument(
        "--since", metavar="FILENAME",
        help="Use a specific saved report as the previous-report context instead of the most recent.",
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Write the report to this path instead of the auto-named file in REPORT_DIR.",
    )
    args = parser.parse_args()

    if args.list_reports:
        _cmd_list_reports()
        return

    if args.show_report is not None:
        _cmd_show_report(args.show_report)
        return

    if args.dry_run:
        _cmd_dry_run(args.window)
        return

    def log(*a, **kw):
        if not args.quiet:
            print(*a, **kw)

    now_utc = datetime.now(timezone.utc)

    # Fetch and process Grafana metrics
    ai_ready_data = fetch_and_process(args.window, GRAFANA_URL, GRAFANA_TOKEN, DATA_SOURCE_UID)

    # Load previous report for historical comparison
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if args.since:
        prev_path = REPORT_DIR / args.since
        if not prev_path.exists():
            prev_path = REPORT_DIR / (args.since + ".txt")
        if not prev_path.exists():
            raise SystemExit(f"--since: report not found: {args.since}")
    else:
        prior_reports = sorted(REPORT_DIR.glob("*.txt"), reverse=True)
        prev_path = prior_reports[0] if prior_reports else None
    prev_report_msg = None
    if prev_path:
        prev_text = prev_path.read_text(encoding="utf-8")[:3000]
        prev_parts = prev_path.stem.split("_")
        prev_window = prev_parts[-1] if len(prev_parts) >= 3 else None
        mismatch_note = ""
        if prev_window and prev_window != args.window:
            mismatch_note = (
                f"\n\n⚠ Window mismatch: this previous report used --window={prev_window}"
                f" but the current run uses --window={args.window}."
                " Treat metric comparisons with caution — differences may reflect the"
                " analysis period rather than genuine changes."
            )
            log(
                f"Warning: previous report used window={prev_window},"
                f" current window={args.window} — mismatch noted in AI context."
            )
        prev_report_msg = {
            "role": "assistant",
            "content": f"[Previous report — {prev_path.stem}]\n{prev_text}{mismatch_note}",
        }
        log(f"Loaded previous report: {prev_path.name}")

    messages = build_messages(ai_ready_data, args.window, now_utc, prev_report_msg, args.prompt_extra)

    log("Forwarding dataset to OpenAI for analysis...")
    client = OpenAI(api_key=OPENAI_API_KEY)

    completion = client.chat.completions.create(
        model=args.model,
        temperature=0.2,
        seed=42,
        stream=True,
        stream_options={"include_usage": True},
        messages=messages,
    )

    log("\n=== OpenAI Targeted Recommendation Report ===")
    full_report_parts: list[str] = []
    usage = None
    for chunk in completion:
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta.content:
            content = chunk.choices[0].delta.content
            print(content, end="", flush=True)
            full_report_parts.append(content)
    print()

    report_text = "".join(full_report_parts)
    if not report_text.strip():
        raise SystemExit("OpenAI returned an empty response.")

    report_path = pathlib.Path(args.output) if args.output else REPORT_DIR / f"{now_utc.strftime('%Y-%m-%d_%H%M')}_{args.window}.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    log(f"Report saved: {report_path}")

    discord_ok = bool(DISCORD_WEBHOOK_URL) and not args.no_discord
    if discord_ok and args.alert_threshold:
        max_sev = _max_severity(report_text)
        if not max_sev or _SEVERITY_RANK.get(max_sev, 0) < _SEVERITY_RANK[args.alert_threshold]:
            log(
                f"Discord skipped: highest severity {max_sev or 'none'}"
                f" is below threshold {args.alert_threshold}."
            )
            discord_ok = False
    if discord_ok:
        send_discord_summary(DISCORD_WEBHOOK_URL, report_text, report_path, usage, args.window, now_utc)

    # Token / cost tracking
    if usage:
        in_price, out_price = _MODEL_PRICING.get(args.model, (0.0, 0.0))
        cost = (usage.prompt_tokens * in_price + usage.completion_tokens * out_price) / 1_000_000
        unknown = "" if args.model in _MODEL_PRICING else " (model not in pricing table)"
        log(
            f"[{args.model}] {usage.prompt_tokens:,} in / {usage.completion_tokens:,} out"
            f" — est. ${cost:.4f}{unknown}"
        )

    exit_code = _severity_exit_code(report_text)
    severity_label = ["LOW/LOWEST", "MEDIUM", "HIGH/HIGHEST"][exit_code]
    log(f"Highest severity: {severity_label} (exit {exit_code})")
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
