import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from .models import Provider, Route, SmsRouteDecision


@dataclass(frozen=True)
class RouteSelection:
    provider: Provider
    route: Route
    decision: SmsRouteDecision


def choose_route(db, message, country="ZZ", excluded_provider_ids=()):
    rows = db.execute(
        select(Route, Provider)
        .join(Provider, Provider.id == Route.provider_id)
        .where(Route.enabled == True, Route.prefix != "")
    ).all()
    candidates = []
    for route, provider in rows:
        reasons = []
        if not message.destination.startswith(route.prefix):
            reasons.append("prefix_mismatch")
        if route.tenant_id not in (None, message.tenant_id):
            reasons.append("tenant_denied")
        if provider.id in excluded_provider_ids:
            reasons.append("previous_attempt")
        if provider.state != "enabled" or not provider.routing_enabled:
            reasons.append("provider_disabled")
        if provider.circuit_state != "closed":
            reasons.append("circuit_open")
        if provider.adapter_type != "jasmin_http":
            reasons.append("unsupported_adapter")
        if message.encoding == "UCS-2" and provider.capabilities.get("unicode") is False:
            reasons.append("unicode_unsupported")
        try:
            if float(message.sell_rate_snapshot["amount"]) < float(
                message.provider_rate_snapshot["amount"]
            ):
                reasons.append("negative_margin")
        except (KeyError, TypeError, ValueError):
            reasons.append("rate_snapshot_invalid")
        candidates.append((route, provider, reasons))
    candidates.sort(
        key=lambda x: (len(x[0].prefix), x[0].priority, x[1].health_score, x[1].id), reverse=True
    )
    selected = next(((r, p) for r, p, reasons in candidates if not reasons), None)
    summary = [
        {
            "route_id": r.id,
            "provider_id": p.id,
            "eligible": not reasons,
            "reasons": reasons,
            "priority": r.priority,
            "prefix": r.prefix,
        }
        for r, p, reasons in candidates
    ]
    evidence = {
        "message_id": message.id,
        "candidates": summary,
        "selected": selected[0].id if selected else None,
    }
    decision = SmsRouteDecision(
        message_id=message.id,
        tenant_id=message.tenant_id,
        destination_prefix=selected[0].prefix if selected else "",
        country=country,
        selected_provider_id=selected[1].id if selected else None,
        selected_route_id=selected[0].id if selected else None,
        route_version=selected[0].version if selected else None,
        candidate_summary=summary,
        provider_rate_snapshot=message.provider_rate_snapshot,
        sell_rate_snapshot=message.sell_rate_snapshot,
        decision_hash=hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
    )
    db.add(decision)
    db.flush()
    return RouteSelection(selected[1], selected[0], decision) if selected else None
