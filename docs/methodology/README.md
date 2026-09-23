# Methodology notes

One note per engine module. Each states what the module computes, the formula
in plain notation, where it is specified in the international standard, the
assumptions it makes, and the biases it is known to carry. The standard cited
throughout is the *Consumer Price Index Manual: Concepts and Methods*, IMF,
ILO, OECD, Eurostat, UNECE and World Bank, 2020 edition ("CPI Manual 2020"),
with its 2004 predecessor and the *Consumer Price Index Theory* companion
volume (2025) named where the 2020 edition defers to them.

Where two defensible methods exist the engine implements both, the interface
makes the choice explicit, the run registry records it, and the note says which
the standard recommends and why.

| Module | Note | What it covers |
|---|---|---|
| `engine/quality.py` | [data_quality.md](data_quality.md) | Sentinel recoding, missingness mechanism, unit-of-measurement fault repair |
| `engine/imputation.py` | [imputation.md](imputation.md) | Carry forward, class mean, seasonal hold, targeted mean, overall mean; response rates |
| `engine/index.py` | [index.md](index.md) | Matched-model elementary index, chained and fixed base; the three reference periods; rebasing |
| `engine/elementary.py` | [elementary.md](elementary.md) | Jevons, Dutot, Carli, harmonic mean, CSWD, unit value |
| `engine/bilateral.py` | [bilateral.md](bilateral.md) | Laspeyres, Paasche, Fisher, Törnqvist, Walsh, Marshall-Edgeworth, Lowe, Young, geometric forms; price updating |
| `engine/aggregation.py` | [aggregation.md](aggregation.md) | Weighted roll-up through a classification tree; additive contributions |
| `engine/splicing.py` | [splicing.md](splicing.md) | Rebasing, link factors, splicing, chaining, chain drift |
| `engine/multilateral.py` | [multilateral.md](multilateral.md) | GEKS-Fisher and GEKS-Törnqvist, time product dummy and its weighted form, time dummy hedonic, Geary-Khamis; window extension by movement, window, half and mean splice, FBEW and FBMW |
| `engine/seasonal.py` | [seasonal.md](seasonal.md) | Strictly seasonal items; class confinement and weight update; the Rothwell index; counter-seasonal estimation; seasonal adjustment by X-13ARIMA-SEATS or STL, with the engine named |
| `engine/outliers.py` | [outliers.md](outliers.md) | Tukey fences, the quartile method, Hidiroglou-Berthelot, ratio screening; the review queue and the exclusion report |
| `engine/revision.py` | [revision.md](revision.md) | Revision triangles, mean and mean absolute revision, the bias test, published against current |
| `engine/decomposition.py` | [decomposition.md](decomposition.md) | Rates of change; contributions at every level of the tree, reconciled to eight decimals; exclusion, trimmed mean, weighted median, variance-weighted and sticky-price core measures; base effects; diffusion and dispersion |
| `engine/deflation.py` | [deflation.md](deflation.md) | Deflation with explicit alignment; real wages and income; constant prices and volume indices; PPP conversion and price level indices |
| `engine/spatial.py` | [spatial.md](spatial.md) | Country product dummy and Geary-Khamis parities; matched products per region pair; thin overlap withheld; conversion and price level indices |
| `engine/trade.py` | [trade.md](trade.md) | Import and export price indices, unit value indices and their bias, terms of trade |
| `engine/construction.py` | [construction.md](construction.md) | Construction input cost and output price indices, and the gap between them |
| `engine/escalation.py` | [escalation.md](escalation.md) | Contract indexation: lags, averaging, dead bands, triggers, indexed share, caps and collars; the plain-language clause summary |
| `engine/asset.py` | [asset.md](asset.md) | Stratified median, mix-adjusted mean, repeat sales (Bailey-Muth-Nourse and Case-Shiller), SPAR and hedonic property price indices; why they differ; repeat sales revisions; stratum suppression |
| `engine/housing.py` | [housing.md](housing.md) | Rental price index; rental equivalence, net acquisitions, user cost and payments as four questions |
| `engine/quality_adjustment.py` | [quality_adjustment.md](quality_adjustment.md) | Overlap, direct comparison, quantity, option cost, class/targeted/overall mean; the ledger; the impact report |
| `engine/hedonic.py` | [hedonic.md](hedonic.md) | Time dummy, characteristics price and imputation hedonics; functional forms; diagnostics |
| `engine/custom.py` | [custom.md](custom.md) | Analyst-defined formulae through the restricted evaluator; the non-standard mark |
| `engine/diagnostics.py` | [diagnostics.md](diagnostics.md) | Method sensitivity, chain drift, unmatched comparison, seasonality, churn |
| `reporting/exports.py` | [disclosure_control.md](disclosure_control.md) | Primary and secondary suppression on every published table |

Every number the engine publishes can be traced to a run registered in
`core/registry.py`; see the [user guide](../user_guide.md) for the auditor's
path and the [administrator guide](../admin_guide.md) for how the registry and
the Parquet store are kept.
