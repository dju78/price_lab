"""The validation engine: completeness, validity, consistency, uniqueness,
timeliness, coverage, conformity to a classification, and plausibility of
price levels and relatives.

Reuses rather than reimplements: validity, consistency, uniqueness and
coverage findings are read from `data.upload.validate` (the structural
checks that already exist), and the missingness-mechanism aspect of
plausibility is
read from `engine.quality.classify_missing`, which is the missingness
diagnosis this codebase already has. This module's own new logic is the
severity classification, the timeliness and conformity dimensions (neither
of which existed anywhere yet), and a light price-level plausibility check
that is deliberately coarser than -- and does not replace -- the precise
scale-error detection `engine.quality.detect_scale_errors` performs later,
after a critical finding here has been accepted, excluded, corrected or
justified.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

import numpy as np
import pandas as pd
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..core.config import QualityConfig
from ..core.db import Base
from ..engine.quality import FLAG_MISSING, classify_missing, recode_missing
from .upload import ValidationReport, validate

OverrideDecisionKind = Literal["accept", "exclude", "correct", "justify"]


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ValidationFinding:
    dimension: str
    """completeness | validity | consistency | uniqueness | timeliness |
    coverage | conformity | plausibility"""
    severity: Severity
    message: str
    count: int = 0
    overridden: bool = False


@dataclass
class DataQualityAssessment:
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """True while any critical finding has not been overridden. A
        caller should refuse to run the calculation while this is true."""
        return any(f.severity == Severity.CRITICAL and not f.overridden for f in self.findings)

    def by_severity(self, severity: Severity) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == severity]

    def override(self, dimension: str) -> None:
        """Mark every critical finding in `dimension` as overridden. Does
        not itself record who did this or why -- see `record_override`,
        which persists that alongside the audit log; call both together."""
        for f in self.findings:
            if f.dimension == dimension and f.severity == Severity.CRITICAL:
                f.overridden = True


def assess(
    df: pd.DataFrame,
    classification_codes: Iterable[str] | None = None,
    reference_date: pd.Timestamp | None = None,
    stale_after_periods: int = 3,
) -> DataQualityAssessment:
    """Run every validation dimension over a standardised price panel.

    `classification_codes`, if given, are the valid category codes for
    conformity checking (e.g. every `code` in a loaded COICOP tree at the
    level categories are expected to map to). `reference_date` is "now"
    for the timeliness check, defaulting to the actual current time;
    passed explicitly in tests so they do not depend on when they run.
    """
    findings: list[ValidationFinding] = []
    structural = validate(df)
    findings.extend(_from_structural_report(structural))

    # Gated on the columns existing, not on `structural.passed`: a
    # duplicate key or a negative price still leaves every column present
    # and typed, so the rest of the panel is still worth assessing in the
    # same pass rather than making the analyst fix one critical finding at
    # a time to even see the next one. Only columns genuinely absent after
    # mapping make these checks unsafe to run at all.
    required = {"period", "category", "item_id", "price_reported"}
    if required <= set(df.columns):
        findings.extend(_completeness(df))
        findings.extend(_timeliness(df, reference_date, stale_after_periods))
        findings.extend(_conformity(df, classification_codes))
        findings.extend(_plausibility(df))

    return DataQualityAssessment(findings=findings)


def _from_structural_report(report: ValidationReport) -> list[ValidationFinding]:
    """Consistency, uniqueness and validity findings, read from the
    existing structural validator rather than re-checked here. Every
    `errors` entry is a hard structural problem -- critical, since the
    index is undefined without a fix. Every `warnings` entry (thin
    coverage, irregular spacing, an item_id with more than one name) is a
    coverage or uniqueness concern worth a human look, not a blocker.
    """
    out = []
    for message in report.errors:
        if "duplicate" in message:
            dimension = "uniqueness"
        elif "negative price" in message:
            dimension = "validity"
        else:
            dimension = "consistency"
        out.append(ValidationFinding(dimension=dimension, severity=Severity.CRITICAL,
                                     message=message))
    for message in report.warnings:
        dimension = "coverage" if "category-period" in message else "uniqueness"
        out.append(ValidationFinding(dimension=dimension, severity=Severity.MEDIUM,
                                     message=message))
    return out


def _completeness(df: pd.DataFrame) -> list[ValidationFinding]:
    findings = []
    required = ["period", "category", "item_id", "price_reported"]
    for col in required:
        if col not in df.columns:
            continue
        share_null = float(df[col].isna().mean())
        if share_null == 0:
            continue
        severity = Severity.CRITICAL if share_null > 0.5 else Severity.HIGH
        findings.append(ValidationFinding(
            dimension="completeness", severity=severity,
            message=f"'{col}' is {share_null:.1%} null", count=int(df[col].isna().sum())))
    return findings


def _timeliness(
    df: pd.DataFrame, reference_date: pd.Timestamp | None, stale_after_periods: int
) -> list[ValidationFinding]:
    if "period" not in df.columns or df["period"].isna().all():
        return []
    reference = reference_date if reference_date is not None else pd.Timestamp.now(tz=None)
    periods = df["period"].drop_duplicates().sort_values()
    if len(periods) < 2:
        return []
    step_days = periods.diff().dropna().dt.days.median()
    if not step_days or step_days <= 0:
        return []
    periods_behind = (reference - periods.iloc[-1]).days / step_days
    if periods_behind > stale_after_periods:
        return [ValidationFinding(
            dimension="timeliness", severity=Severity.MEDIUM,
            message=(f"latest period {periods.iloc[-1].date()} is about "
                    f"{periods_behind:.1f} collection periods behind {reference.date()}"))]
    return []


def _conformity(
    df: pd.DataFrame, classification_codes: Iterable[str] | None
) -> list[ValidationFinding]:
    if classification_codes is None or "category" not in df.columns:
        return []
    valid = set(classification_codes)
    used = set(df["category"].dropna().unique())
    unknown = used - valid
    if not unknown:
        return []
    return [ValidationFinding(
        dimension="conformity", severity=Severity.HIGH,
        message=f"{len(unknown)} category value(s) not in the classification: "
                f"{sorted(unknown)[:10]}",
        count=len(unknown))]


def _plausibility(df: pd.DataFrame) -> list[ValidationFinding]:
    """Two coarse, pre-repair checks: gaps whose mechanism looks systemic
    (read from `engine.quality`'s own diagnosis, not reimplemented), and
    prices sitting implausibly far from their own item's level. The
    precise, repairable order-of-100 case is `engine.quality.
    detect_scale_errors`'s job later; this only flags what is left over --
    a deviation too large to be a plausible unit-of-measurement fault --
    as something for a human to look at before the run proceeds.
    """
    findings: list[ValidationFinding] = []
    if not {"period", "category", "price_reported"} <= set(df.columns):
        return findings

    probe = recode_missing(df, QualityConfig())
    mechanisms = classify_missing(probe)
    # classify_missing returns a columnless empty frame, not one with a
    # "mechanism" column holding no rows, when nothing is missing at all --
    # the common case for a clean collection -- so filtering on that column
    # unconditionally would crash on exactly the data that needs no finding.
    systemic = (mechanisms[mechanisms["mechanism"].isin(["seasonal", "collection"])]
                if "mechanism" in mechanisms.columns else mechanisms)
    for row in systemic.to_dict(orient="records"):
        severity = Severity.LOW if row["mechanism"] == "seasonal" else Severity.MEDIUM
        findings.append(ValidationFinding(
            dimension="plausibility", severity=severity,
            message=f"{row['category']}: {row['gaps']} gaps read as "
                    f"{row['mechanism']} unavailability",
            count=int(row["gaps"])))

    if "item_id" in df.columns:
        priced = probe.loc[probe["flag"] != FLAG_MISSING, "price"]
        by_item_median = probe.loc[probe["flag"] != FLAG_MISSING].groupby("item_id")["price"].transform("median")
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = priced / by_item_median
        implausible = int(((ratio > 1000) | (ratio < 0.001)).sum())
        if implausible:
            findings.append(ValidationFinding(
                dimension="plausibility", severity=Severity.HIGH,
                message=(f"{implausible} price(s) sit more than 1000x from their own item's "
                        "median -- too large a deviation to be a plausible unit-of-measurement "
                        "fault; check the source record rather than assuming a scale error"),
                count=implausible))
    return findings


class ValidationOverrideORM(Base):
    __tablename__ = "validation_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


def record_override(
    session: Session,
    actor: str,
    content_hash: str,
    dimension: str,
    decision: OverrideDecisionKind,
    reason: str,
) -> ValidationOverrideORM:
    """Persist an override decision: who, when, and why a critical finding
    was accepted, excluded, corrected or justified rather than fixed.
    Callers with an audit session should also log
    `core.audit.VALIDATION_OVERRIDE`; this function only handles the
    override record itself, so it stays usable from a script or test with
    no audit context at all.
    """
    if not reason or not reason.strip():
        raise ValueError("an override must state a reason")
    record = ValidationOverrideORM(
        content_hash=content_hash, dimension=dimension, decision=decision, reason=reason,
        actor=actor, created_at=datetime.now(UTC).isoformat())
    session.add(record)
    session.flush()
    return record
