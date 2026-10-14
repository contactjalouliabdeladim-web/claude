"""Cockpit 120K : progression vers l'objectif de bénéfice net, calculée sur les finances d'Atlas.

Aucune donnée en double : les montants viennent de ``finance_transactions`` (Vue business).
Les réglages (objectif, période, taux EUR→MAD, montant acquis avant le suivi) sont des
préférences modifiables ; l'« action du jour qui rapporte » est aussi une préférence datée.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

COCKPIT_DEFAULTS: dict[str, Any] = {
    "cockpit_goal_mad": 120000,
    "cockpit_start": "2026-09-29",
    "cockpit_end": "2027-01-31",
    "cockpit_eur_mad": 10.8,  # taux indicatif : à vérifier et ajuster dans les réglages
    "cockpit_prior_mad": 0,
}
LIGHT_DELAY_DAYS = 7  # au-delà de 7 jours de retard : « gros retard »


class CockpitError(ValueError):
    pass


def validate_setting(key: str, value: Any) -> Any:
    if key in ("cockpit_start", "cockpit_end"):
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError as exc:
            raise CockpitError("Date invalide (AAAA-MM-JJ attendu).") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CockpitError("Nombre attendu.")
    if key == "cockpit_goal_mad" and not 0 < value <= 1e10:
        raise CockpitError("L'objectif doit être positif.")
    if key == "cockpit_eur_mad" and not 0 < value <= 1000:
        raise CockpitError("Taux EUR→MAD invalide.")
    if key == "cockpit_prior_mad" and not -1e10 <= value <= 1e10:
        raise CockpitError("Montant invalide.")
    return value


def settings_from(prefs: dict[str, Any]) -> dict[str, Any]:
    return {k: prefs.get(k, v) for k, v in COCKPIT_DEFAULTS.items()}


def compute(totals: dict[str, dict[str, int]], settings: dict[str, Any], today: str) -> dict[str, Any]:
    """Calcul pur. ``totals`` : sortie de ``Database.finance_totals`` sur la période."""
    start, end, now = (date.fromisoformat(settings["cockpit_start"]), date.fromisoformat(settings["cockpit_end"]),
                       date.fromisoformat(today))
    if end < start:
        raise CockpitError("La date de fin précède la date de début.")
    goal = float(settings["cockpit_goal_mad"])
    rate = float(settings["cockpit_eur_mad"])
    prior = float(settings["cockpit_prior_mad"])
    net_mad = totals.get("MAD", {}).get("net_cents", 0) / 100
    net_eur = totals.get("EUR", {}).get("net_cents", 0) / 100
    tracked = net_mad + net_eur * rate          # bénéfice net enregistré dans la période
    acquired = tracked + prior                  # + ce qui était acquis avant le suivi

    total_days = (end - start).days + 1
    elapsed = min(max((now - start).days + 1, 0), total_days)   # aujourd'hui compte comme jour écoulé
    days_left = min(max((end - now).days + 1, 0), total_days)   # aujourd'hui compte aussi comme jour restant
    remaining = max(goal - acquired, 0.0)
    daily_goal = goal / total_days
    expected = daily_goal * elapsed
    delta_mad = acquired - expected
    delta_days = delta_mad / daily_goal
    required_week = remaining / (days_left / 7) if days_left else None
    actual_week = tracked / (elapsed / 7) if elapsed else None

    if remaining == 0:
        state = "atteint"
    elif delta_days > -1:
        state = "dans_les_temps"
    elif delta_days > -LIGHT_DELAY_DAYS:
        state = "leger_retard"
    else:
        state = "gros_retard"

    rounded_days = round(abs(delta_days))
    if remaining == 0:
        summary_gap = "objectif atteint"
    elif rounded_days == 0:
        summary_gap = "pile dans les temps"
    else:
        summary_gap = f"{'avance' if delta_days > 0 else 'retard'} de {rounded_days} jour{'s' if rounded_days > 1 else ''}"
    parts = [f"{fmt_dh(acquired)} / {fmt_dh(goal)} DH"]
    if required_week is not None and remaining:
        parts.append(f"il te faut {fmt_dh(required_week)} DH/semaine")
    parts.append(summary_gap)
    return {
        "objectif_mad": round(goal, 2), "debut": start.isoformat(), "fin": end.isoformat(), "aujourdhui": today,
        "taux_eur_mad": rate, "acquis_avant_suivi_mad": round(prior, 2),
        "net_mad": round(net_mad, 2), "net_eur": round(net_eur, 2),
        "cumul_net_mad": round(acquired, 2), "reste_mad": round(remaining, 2),
        "pourcentage": round(min(acquired / goal, 10) * 100, 1) if goal else 0.0,
        "jours_total": total_days, "jours_ecoules": elapsed, "jours_restants": days_left,
        "rythme_necessaire_semaine_mad": round(required_week, 2) if required_week is not None else None,
        "rythme_reel_semaine_mad": round(actual_week, 2) if actual_week is not None else None,
        "attendu_a_date_mad": round(expected, 2),
        "ecart_mad": round(delta_mad, 2), "ecart_jours": round(delta_days, 1),
        "etat": state, "resume": " · ".join(parts),
    }


def fmt_dh(value: float) -> str:
    """34200.4 → « 34 200 » (espace fine insécable comme séparateur de milliers)."""
    return f"{round(value):,}".replace(",", " ")


def money_action_today(prefs: dict[str, Any], today: str) -> dict[str, Any] | None:
    action = prefs.get("money_action")
    return action if isinstance(action, dict) and action.get("jour") == today else None


def cockpit_for(db: Any, prefs: dict[str, Any], today: str) -> dict[str, Any]:
    settings = settings_from(prefs)
    totals = db.finance_totals(start=settings["cockpit_start"], end=settings["cockpit_end"])
    data = compute(totals, settings, today)
    data["action_du_jour"] = money_action_today(prefs, today)
    return data
