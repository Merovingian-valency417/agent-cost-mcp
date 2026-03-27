"""
Agent Cost MCP Server — Track AI agent spending in real time.

Provides tools for logging token usage, calculating costs, setting budgets,
and generating spending reports. Works with any MCP client (Claude Code,
Cursor, Windsurf, etc.)
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Pricing per million tokens (as of March 2026)
MODEL_PRICING = {
    # Anthropic
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    # OpenAI
    "gpt-5.4": {"input": 2.50, "output": 10.00},
    "gpt-5.2": {"input": 1.50, "output": 6.00},
    "gpt-5.1": {"input": 0.60, "output": 2.40},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # DeepSeek
    "deepseek-v3": {"input": 0.27, "output": 1.10},
    "deepseek-r1": {"input": 0.55, "output": 2.19},
    # Google
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    # Meta (via API providers)
    "llama-4-maverick": {"input": 0.20, "output": 0.60},
}

# Data file location
DATA_DIR = Path.home() / ".agent-cost-mcp"
DATA_FILE = DATA_DIR / "cost-log.json"


def _load_data() -> dict:
    """Load cost data from disk."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "entries": [],
        "budget": {"daily_limit": 10.00, "monthly_limit": 100.00, "alert_threshold": 0.80},
    }


def _save_data(data: dict) -> None:
    """Save cost data to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Calculate cost for a given model and token count."""
    # Try exact match first, then partial match
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        for key, val in MODEL_PRICING.items():
            if key in model.lower() or model.lower() in key:
                pricing = val
                break
    if not pricing:
        # Default to GPT-4o pricing as a reasonable estimate
        pricing = {"input": 2.50, "output": 10.00}

    cost_in = (tokens_in / 1_000_000) * pricing["input"]
    cost_out = (tokens_out / 1_000_000) * pricing["output"]
    return round(cost_in + cost_out, 6)


def _get_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_today_entries(data: dict) -> list:
    today = _get_today_str()
    return [e for e in data["entries"] if e["timestamp"].startswith(today)]


def _get_week_entries(data: dict) -> list:
    now = datetime.now(timezone.utc)
    week_ago = (now.timestamp() - 7 * 86400)
    results = []
    for e in data["entries"]:
        try:
            ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
            if ts.timestamp() >= week_ago:
                results.append(e)
        except (ValueError, KeyError):
            pass
    return results


def _get_month_entries(data: dict) -> list:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    return [e for e in data["entries"] if e["timestamp"].startswith(month)]


# Create MCP server
mcp = FastMCP(
    "Agent Cost Tracker",
    instructions="Track AI agent token usage and spending. Budget alerts, per-task cost, daily/weekly/monthly reports.",
)


@mcp.tool()
def log_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    task: str = "",
) -> str:
    """Log token usage and cost for a task. Call this after each AI interaction.

    Args:
        model: Model name (e.g., 'claude-sonnet-4-6', 'gpt-5.4')
        tokens_in: Number of input tokens
        tokens_out: Number of output tokens
        task: Description of what the task was
    """
    cost = _calculate_cost(model, tokens_in, tokens_out)
    data = _load_data()

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "task": task,
    }
    data["entries"].append(entry)
    _save_data(data)

    # Check budget
    today_cost = sum(e["cost_usd"] for e in _get_today_entries(data))
    budget = data["budget"]
    alert = ""
    if today_cost > budget["daily_limit"]:
        alert = f"\n⚠️ OVER BUDGET: ${today_cost:.2f} / ${budget['daily_limit']:.2f} daily limit"
    elif today_cost > budget["daily_limit"] * budget["alert_threshold"]:
        pct = (today_cost / budget["daily_limit"]) * 100
        alert = f"\n⚠️ Budget alert: ${today_cost:.2f} / ${budget['daily_limit']:.2f} ({pct:.0f}%)"

    return f"Logged: ${cost:.4f} ({tokens_in:,} in / {tokens_out:,} out, {model}){alert}"


@mcp.tool()
def cost_report(period: str = "today") -> str:
    """Get a spending report.

    Args:
        period: 'today', 'week', 'month', or 'all'
    """
    data = _load_data()

    if period == "today":
        entries = _get_today_entries(data)
        label = f"Today ({_get_today_str()})"
    elif period == "week":
        entries = _get_week_entries(data)
        label = "This Week (last 7 days)"
    elif period == "month":
        entries = _get_month_entries(data)
        label = f"This Month ({datetime.now(timezone.utc).strftime('%B %Y')})"
    else:
        entries = data["entries"]
        label = "All Time"

    if not entries:
        return f"No spending data for {label}."

    total_cost = sum(e["cost_usd"] for e in entries)
    total_in = sum(e["tokens_in"] for e in entries)
    total_out = sum(e["tokens_out"] for e in entries)
    num_messages = len(entries)

    # By model breakdown
    model_costs = {}
    for e in entries:
        m = e["model"]
        if m not in model_costs:
            model_costs[m] = 0
        model_costs[m] += e["cost_usd"]

    model_lines = []
    for m, c in sorted(model_costs.items(), key=lambda x: -x[1]):
        pct = (c / total_cost * 100) if total_cost > 0 else 0
        model_lines.append(f"  {m}: ${c:.4f} ({pct:.0f}%)")

    # Budget status
    budget = data["budget"]
    today_cost = sum(e["cost_usd"] for e in _get_today_entries(data))
    month_cost = sum(e["cost_usd"] for e in _get_month_entries(data))

    report = f"""# Cost Report — {label}

## Summary
- Messages: {num_messages:,}
- Tokens: {total_in + total_out:,} ({total_in:,} in / {total_out:,} out)
- Total cost: ${total_cost:.4f}
- Avg cost/message: ${total_cost / num_messages:.4f}

## By Model
{chr(10).join(model_lines)}

## Budget
- Daily: ${today_cost:.2f} / ${budget['daily_limit']:.2f} ({today_cost / budget['daily_limit'] * 100:.0f}%)
- Monthly: ${month_cost:.2f} / ${budget['monthly_limit']:.2f} ({month_cost / budget['monthly_limit'] * 100:.0f}%)
"""

    # Most expensive task
    if entries:
        most_expensive = max(entries, key=lambda e: e["cost_usd"])
        if most_expensive["task"]:
            report += f"\n## Most Expensive Task\n{most_expensive['task']}: ${most_expensive['cost_usd']:.4f}\n"

    return report


@mcp.tool()
def set_budget(daily_limit: float = 0, monthly_limit: float = 0) -> str:
    """Set daily and/or monthly budget limits.

    Args:
        daily_limit: Maximum daily spend in USD (0 = don't change)
        monthly_limit: Maximum monthly spend in USD (0 = don't change)
    """
    data = _load_data()
    if daily_limit > 0:
        data["budget"]["daily_limit"] = daily_limit
    if monthly_limit > 0:
        data["budget"]["monthly_limit"] = monthly_limit
    _save_data(data)
    return f"Budget set: ${data['budget']['daily_limit']:.2f}/day, ${data['budget']['monthly_limit']:.2f}/month"


@mcp.tool()
def cost_trend(days: int = 7) -> str:
    """Show daily spending trend as a text chart.

    Args:
        days: Number of days to show (default: 7)
    """
    data = _load_data()
    now = datetime.now(timezone.utc)

    daily = {}
    for i in range(days):
        d = (now.timestamp() - i * 86400)
        day_str = datetime.fromtimestamp(d, tz=timezone.utc).strftime("%Y-%m-%d")
        daily[day_str] = 0

    for e in data["entries"]:
        day = e["timestamp"][:10]
        if day in daily:
            daily[day] += e["cost_usd"]

    if not any(v > 0 for v in daily.values()):
        return "No spending data in the last {days} days."

    max_cost = max(daily.values()) if max(daily.values()) > 0 else 1
    lines = []
    for day in sorted(daily.keys()):
        cost = daily[day]
        bar_len = int((cost / max_cost) * 30) if max_cost > 0 else 0
        bar = "█" * bar_len
        lines.append(f"{day[5:]}: ${cost:.2f} {bar}")

    return "# Daily Spending Trend\n```\n" + "\n".join(lines) + "\n```"


@mcp.tool()
def supported_models() -> str:
    """List all supported models and their pricing."""
    lines = ["# Supported Models\n", "| Model | Input ($/1M) | Output ($/1M) |", "|-------|-------------|--------------|"]
    for model, pricing in sorted(MODEL_PRICING.items()):
        lines.append(f"| {model} | ${pricing['input']:.2f} | ${pricing['output']:.2f} |")
    return "\n".join(lines)


@mcp.tool()
def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> str:
    """Estimate cost without logging it. Use for planning.

    Args:
        model: Model name
        tokens_in: Expected input tokens
        tokens_out: Expected output tokens
    """
    cost = _calculate_cost(model, tokens_in, tokens_out)
    return f"Estimated cost: ${cost:.4f} ({tokens_in:,} in / {tokens_out:,} out, {model})"


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
