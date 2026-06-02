import os
import re
import pathlib
import requests
from datetime import datetime

SEVERITY_ORDER = ["HIGHEST", "HIGH", "MEDIUM", "LOW", "LOWEST"]

_BASE = "https://raw.githubusercontent.com/AlexanderBartash/JIRA-Priority-Icons/master/"

SEVERITY_COLORS = {
    "HIGHEST": 0xFF0000,
    "HIGH":    0xFF6B00,
    "MEDIUM":  0xFFD700,
    "LOW":     0x2ECC71,
    "LOWEST":  0x95A5A6,
}

SEVERITY_ICONS = {s: f"{_BASE}{s.capitalize()}.png" for s in SEVERITY_ORDER}


def _fetch_guild_emojis() -> dict[str, str]:
    """Return {SEVERITY: '<:Name:id>'} from DISCORD_EMOJI_* env vars.

    Get IDs by typing \\:EmojiName: in Discord, then set in .env:
      DISCORD_EMOJI_HIGHEST=123456789  ...etc
    Falls back to {} — callers degrade to [SEVERITY] text labels.
    """
    result = {}
    for name in SEVERITY_ORDER:
        eid = os.environ.get(f"DISCORD_EMOJI_{name}", "").strip()
        if eid:
            result[name] = f"<:{name.capitalize()}:{eid}>"
    return result


def _parse_findings(report_text: str, glyphs: dict[str, str]) -> tuple[dict[str, list[str]], list[str]]:
    """Parse area findings and severities from the report.

    Tries the summary table first; falls back to inline [SEVERITY] bullets.
    Returns (area_findings, severities_found).
    """
    area_findings: dict[str, list[str]] = {}
    severities_found: list[str] = []
    area_col, severity_col, finding_col = 0, 1, 2

    for line in report_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        upper = [c.upper() for c in cells]

        if "SEVERITY" in upper:
            severity_col = upper.index("SEVERITY")
            area_col = next((i for i, c in enumerate(upper) if c == "AREA"), 0)
            finding_col = next(
                (i for i, c in enumerate(upper) if "RECOMMEND" in c or "FINDING" in c or "ACTION" in c),
                len(cells) - 1,
            )
            continue

        if any("---" in c for c in cells):
            continue
        if len(cells) <= severity_col:
            continue

        area = cells[area_col]
        severity = cells[severity_col].upper()
        if not area or severity not in SEVERITY_ORDER:
            continue

        finding = cells[finding_col] if finding_col < len(cells) else cells[-1]
        area_findings.setdefault(area, []).append(f"{glyphs.get(severity, f'[{severity}]')} {finding}")
        severities_found.append(severity)

    # Fallback: scan inline bullet labels grouped by section heading
    if not area_findings:
        section = None
        section_map = {"RESOURCE": "Resource Utilization", "NETWORK": "Network Utilization", "REPO": "Repo Sync Health"}
        for line in report_text.splitlines():
            for key, label in section_map.items():
                if key in line.upper() and line.strip().startswith("#"):
                    section = label
            if not section:
                continue
            m = re.match(r"-\s*\[(HIGHEST|HIGH|MEDIUM|LOW|LOWEST)\]\s+\*?\*?(.+?)\*?\*?:?\s*$", line.strip())
            if m:
                sev, finding = m.group(1), m.group(2).strip()
                area_findings.setdefault(section, []).append(f"{glyphs.get(sev, f'[{sev}]')} {finding}")
                severities_found.append(sev)

    return area_findings, severities_found


def send_discord_summary(
    webhook_url: str,
    report_text: str,
    report_path: pathlib.Path,
    usage,
    window: str,
    now_utc: datetime,
) -> None:
    """Post a colour-coded summary embed to a Discord webhook."""
    guild_emojis = _fetch_guild_emojis()
    glyphs = {s: guild_emojis.get(s, f"[{s}]") for s in SEVERITY_ORDER}

    area_findings, severities_found = _parse_findings(report_text, glyphs)

    top = next((s for s in SEVERITY_ORDER if s in severities_found), "LOWEST")
    color = SEVERITY_COLORS[top]

    fields = [
        {"name": area, "value": "\n".join(lines)[:1024], "inline": False}
        for area, lines in area_findings.items()
    ]

    description = next(
        (ln.strip().strip("*") for ln in reversed(report_text.splitlines())
         if ln.strip().startswith("**") and ln.strip().endswith("**")
         and not ln.strip().strip("*").endswith(":")
         and len(ln.strip().strip("*")) > 20
         and not ln.strip().strip("*").lower().startswith("if you")
         and not ln.strip().strip("*").lower().startswith("note")),
        "",
    )

    cost_str = ""
    if usage:
        cost = (usage.prompt_tokens * 2 + usage.completion_tokens * 8) / 1_000_000
        cost_str = f" | {usage.prompt_tokens:,} in / {usage.completion_tokens:,} out — ${cost:.4f}"

    payload = {
        "embeds": [{
            "title": f"AlmaLinux Mirror Report — {now_utc.strftime('%Y-%m-%d %H:%M UTC')}",
            "description": description,
            "color": color,
            "thumbnail": {"url": SEVERITY_ICONS[top]},
            "fields": fields,
            "footer": {"text": f"Window: {window}{cost_str} | {report_path.name}"},
        }]
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 204:
            print("Discord notification sent.")
        else:
            print(f"Discord webhook failed: {resp.status_code} — {resp.text[:200]}")
    except requests.exceptions.RequestException as e:
        print(f"Discord webhook error: {e}")
