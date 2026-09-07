import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from .engine import resolve_rate
from .models import CountryPolicy, Provider, Route, SmsRouteDecision


@dataclass(frozen=True)
class AuthorizedRoute:
    provider: Provider
    route: Route
    policy: CountryPolicy
    provider_rate: object
    sell_rate: object
    candidates: list


def authorize_route(db, tenant_id, destination, sender, category, plan_id, encoding, when=None):
    when = when or datetime.now(timezone.utc)
    if sender.status != "approved":
        raise ValueError("sender_not_approved")
    rows = db.execute(
        select(Route, Provider)
        .join(Provider, Provider.id == Route.provider_id)
        .where(Route.enabled == True, Route.prefix != "")
    ).all()
    candidates = []
    eligible = []
    for route, provider in rows:
        reasons = []
        if not destination.startswith(route.prefix):
            reasons.append("prefix_mismatch")
        if route.tenant_id not in (None, tenant_id):
            reasons.append("tenant_denied")
        if route.sender_type and route.sender_type != sender.type:
            reasons.append("sender_type_mismatch")
        if provider.state != "enabled" or not provider.routing_enabled:
            reasons.append("provider_disabled")
        if provider.circuit_state != "closed":
            reasons.append("circuit_open")
        if provider.adapter_type != "jasmin_http":
            reasons.append("unsupported_adapter")
        if encoding == "UCS-2" and provider.capabilities.get("unicode") is False:
            reasons.append("unicode_unsupported")
        if (
            provider.capabilities.get("categories")
            and category not in provider.capabilities["categories"]
        ):
            reasons.append("category_unsupported")
        summary = {
            "route_id": route.id,
            "provider_id": provider.id,
            "country": route.country,
            "network": route.config.get("network"),
            "prefix": route.prefix,
            "priority": route.priority,
            "transport_eligible": not reasons,
            "reasons": reasons,
        }
        candidates.append(summary)
        if not reasons:
            eligible.append((route, provider, summary))
    eligible.sort(
        key=lambda x: (
            x[0].tenant_id == tenant_id,
            len(x[0].prefix),
            x[0].priority,
            x[1].health_score,
            x[1].id,
        ),
        reverse=True,
    )
    if not eligible:
        raise ValueError("no_eligible_route")
    route, provider, summary = eligible[0]
    policy = db.scalar(
        select(CountryPolicy).where(
            CountryPolicy.country == route.country,
            CountryPolicy.category == category,
            CountryPolicy.enabled == True,
        )
    )
    if not policy:
        raise ValueError("selected_route_country_policy_denied")
    if (
        policy.config.get("allowed_sender_types")
        and sender.type not in policy.config["allowed_sender_types"]
    ):
        raise ValueError("selected_route_sender_type_denied")
    if sender.countries and route.country not in sender.countries:
        raise ValueError("selected_route_sender_country_denied")
    provider_rate = resolve_rate(
        db,
        "provider",
        tenant_id,
        route.country,
        destination,
        plan_id=plan_id,
        when=when,
        provider=provider.name,
        connector=provider.connector,
        network=route.config.get("network"),
    )
    sell_rate = resolve_rate(
        db,
        "sell",
        tenant_id,
        route.country,
        destination,
        plan_id=plan_id,
        when=when,
        network=route.config.get("network"),
    )
    if Decimal(sell_rate.amount) < Decimal(provider_rate.amount):
        raise ValueError("selected_route_negative_margin")
    summary.update(
        {
            "authorized": True,
            "policy_id": policy.id,
            "provider_rate_id": provider_rate.id,
            "sell_rate_id": sell_rate.id,
            "route_version": route.version,
        }
    )
    return AuthorizedRoute(provider, route, policy, provider_rate, sell_rate, candidates)


def persist_decision(db, message, sender, authorized):
    provider_rate, sell_rate = authorized.provider_rate, authorized.sell_rate
    route, provider = authorized.route, authorized.provider
    evidence = {
        "message_id": message.id,
        "route_id": route.id,
        "route_version": route.version,
        "provider_id": provider.id,
        "country": route.country,
        "network": route.config.get("network"),
        "sender_id": sender.id,
        "country_policy_id": authorized.policy.id,
        "provider_rate_id": provider_rate.id,
        "sell_rate_id": sell_rate.id,
        "candidates": authorized.candidates,
    }
    decision = SmsRouteDecision(
        message_id=message.id,
        tenant_id=message.tenant_id,
        destination_prefix=route.prefix,
        country=route.country,
        network=route.config.get("network"),
        sender_id=sender.id,
        country_policy_id=authorized.policy.id,
        selected_provider_id=provider.id,
        selected_route_id=route.id,
        route_version=route.version,
        candidate_summary=authorized.candidates,
        provider_rate_snapshot={
            "id": provider_rate.id,
            "amount": str(provider_rate.amount),
            "country": provider_rate.country,
            "provider": provider.name,
            "connector": provider.connector,
            "network": provider_rate.network,
            "effective_from": provider_rate.effective_from.isoformat(),
        },
        sell_rate_snapshot={
            "id": sell_rate.id,
            "amount": str(sell_rate.amount),
            "country": sell_rate.country,
            "tenant_id": sell_rate.tenant_id,
            "plan_id": sell_rate.plan_id,
            "network": sell_rate.network,
            "effective_from": sell_rate.effective_from.isoformat(),
        },
        decision_hash=hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
    )
    db.add(decision)
    db.flush()
    return decision
