import requests
import pandas as pd


def _window_params(window: str) -> tuple[str, str, str]:
    """Return (grafana_step_seconds, pandas_resample_rule, period_label) for a window string."""
    unit = window[-1].lower() if window else "h"
    try:
        n = int(window[:-1])
    except (ValueError, IndexError):
        n, unit = 24, "h"
    secs = n * {"h": 3_600, "d": 86_400, "w": 604_800}.get(unit, 3_600)

    if secs <= 3_600:       return "60",   "5min",  "5-min (UTC)"
    if secs <= 21_600:      return "300",  "30min", "30-min (UTC)"
    if secs <= 86_400:      return "600",  "1h",    "Hour (UTC)"
    if secs <= 604_800:     return "1800", "6h",    "6-Hour (UTC)"
    return                         "3600", "12h",   "12-Hour (UTC)"


def _queries(datasource_uid: str, step: str = "600") -> list[dict]:
    def q(ref_id: str, expr: str) -> dict:
        return {"refId": ref_id, "datasource": {"uid": datasource_uid}, "expr": expr, "interval": step}

    return [
        # Baseline
        q("CPU",          '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'),
        q("RAM",          '100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))'),
        q("Disk_Usage",   '100 * (1 - (node_filesystem_free_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}))'),
        # Memory breakdown
        q("Memory_Active_MB",  "node_memory_Active_bytes / 1024 / 1024"),
        q("Memory_Cached_MB",  "node_memory_Cached_bytes / 1024 / 1024"),
        q("Memory_Buffers_MB", "node_memory_Buffers_bytes / 1024 / 1024"),
        # Network
        q("Network_Receive_MB",      "sum by (instance) (rate(node_network_receive_bytes_total[5m])) / 1024 / 1024"),
        q("Network_Transmit_MB",     "sum by (instance) (rate(node_network_transmit_bytes_total[5m])) / 1024 / 1024"),
        q("Network_Receive_Errors",  "sum by (instance) (rate(node_network_receive_errs_total[5m]))"),
        q("Network_Transmit_Errors", "sum by (instance) (rate(node_network_transmit_errs_total[5m]))"),
        q("Network_Drops",           "sum by (instance) (rate(node_network_receive_drop_total[5m]) + rate(node_network_transmit_drop_total[5m]))"),
        # Repo sync — AlmaLinux Main
        q("Main_Sync_Success",      "almalinux_main_sync_success"),
        q("Main_Sync_Duration_Sec", "almalinux_main_sync_duration_seconds"),
        q("Main_Repo_Size_GB",      "almalinux_main_repo_size_bytes / 1024 / 1024 / 1024"),
        q("Main_Staleness_Sec",     "time() - almalinux_main_last_success_timestamp"),
        # Repo sync — AlmaLinux Kitten
        q("Kitten_Sync_Success",      "almalinux_kitten_sync_success"),
        q("Kitten_Sync_Duration_Sec", "almalinux_kitten_sync_duration_seconds"),
        q("Kitten_Repo_Size_GB",      "almalinux_kitten_repo_size_bytes / 1024 / 1024 / 1024"),
        q("Kitten_Staleness_Sec",     "time() - almalinux_kitten_last_success_timestamp"),
    ]


def fetch_and_process(window: str, grafana_url: str, grafana_token: str, datasource_uid: str) -> str:
    step, resample_rule, period_label = _window_params(window)
    payload = {
        "queries": _queries(datasource_uid, step),
        "from": f"now-{window}",
        "to": "now",
    }
    headers = {"Authorization": f"Bearer {grafana_token}", "Content-Type": "application/json"}

    print(f"Fetching {window} of infrastructure metrics...")
    try:
        resp = requests.post(f"{grafana_url}/api/ds/query", json=payload, headers=headers)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise SystemExit(f"Grafana connection failed: {e}")

    try:
        data = resp.json()
    except ValueError as e:
        raise SystemExit(f"Grafana returned non-JSON response: {e}\n{resp.text[:200]}")

    report_strings: list[str] = []
    for metric_name, metric_payload in data.get("results", {}).items():
        for frame in metric_payload.get("frames", []):
            labels = frame.get("schema", {}).get("meta", {}).get("labels") or {}
            if not labels:
                for f in frame.get("schema", {}).get("fields", []):
                    if f.get("labels"):
                        labels = f.get("labels")
                        break
            instance_name = labels.get("instance", "unknown-host")
            fields = frame.get("data", {}).get("values", [])
            if len(fields) < 2:
                continue
            timestamps, values = fields[0], fields[1]
            df = pd.DataFrame({"Time": pd.to_datetime(timestamps, unit="ms"), metric_name: values})
            df = df.set_index("Time")
            df_hourly = df.resample(resample_rule).agg(
                Min=(metric_name, "min"),
                Mean=(metric_name, "mean"),
                Max=(metric_name, "max"),
            ).dropna().round(2)
            df_hourly.index = df_hourly.index.strftime("%Y-%m-%d %H:%M")
            df_hourly.index.name = period_label
            if not df_hourly.empty:
                report_strings.append(
                    f"Metric: {metric_name} | Host: {instance_name}\n{df_hourly.to_markdown()}"
                )

    ai_ready_data = "\n\n".join(report_strings)
    if len(ai_ready_data) > 40000:
        ai_ready_data = ai_ready_data[:40000] + "\n...[Data Truncated for Token Optimization]..."

    print(f"Dataset size: {len(ai_ready_data):,} chars across {len(report_strings)} metric series")
    return ai_ready_data
