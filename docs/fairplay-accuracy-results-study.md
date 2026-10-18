# Accuracy/results-only research ablation

This is an **offline research tool**, not a replacement for the deployed Fair
Play classifier. Production code does not import it; no Discord route or
configuration flag enables it. Its HIGH labels are experimental worklist
outputs, not misconduct verdicts or probabilities. It never creates VERY HIGH.

The experiment deliberately excludes engine matching, Maia, timing and account
metadata from classification. It uses only supplied Accuracy and results
relative to ratings. Supplied estimated Accuracy may itself have been computed
by an engine; the tool never runs that engine or any neural model.

## Two measurements

1. Expected points use the ordinary Elo logistic formula per opponent,
   `p = 1 / (1 + 10 ** ((opponent_elo - player_elo) / 400))`.
   A draw is half a point. Reported win fraction is separate from expected
   score fraction: Elo expected points are **not win probabilities**.
   Sum observed minus expected points; divide by `sqrt(sum(p*(1-p)))` for a
   descriptive standardized residual. This is not a calibrated significance
   test: ratings may lag strength and games/selected windows are correlated.
2. Accuracy is compared with actual supplied reference games at the same time
   class and exact base/increment, within 250 own Elo, 300 opponent Elo and
   250 rating-gap points. No invented universal formula such as "500 = 60%".
   Require 20 distinct reference games and, for cross-player comparisons, two
   other players. Give reference players equal total influence using their
   medians and 90th-percentile scores. A subject and the other side of that
   subject's physical games cannot enter its cross-player reference.

Both cross-player reference and strictly earlier personal reference are
reported. Reference selection never uses labels, closures, results, Accuracy
magnitude or engine quality. Personal comparison can be missing during rating
changes; unknown data must not be replaced by zero.

The latest 200 completed observations are split into Rapid/Blitz/Bullet
chronology. At widths 10/20/50/200, evaluate fixed contiguous blocks, latest
tails and the whole available class. Short peaks are not averaged away.
Overlapping windows never become independent replications. Missing Accuracy
requires coverage reporting, not a normal-play conclusion.
Overall and per-class result summaries also show the latest sample's win
fraction and expected point fraction as context; tiers still use periods.

## Prespecified scenarios and ablations

| Scenario | Median Accuracy excess | Fraction above reference 90th percentile | Result residual | Extra score fraction |
|---|---:|---:|---:|---:|
| Strict | 12 points | .70 | 3.0 | .20 |
| Balanced | 8 points | .60 | 2.5 | .15 |
| Permissive | 5 points | .50 | 1.5 | .10 |

Each period needs 80% score/reference coverage for the respective feature.
The joint model assigns HIGH only if both features pass in the **same games**;
one passing feature yields MODERATE. Missing necessary data can yield
INSUFFICIENT DATA. Separate `results_only` and `accuracy_only` ablations show
what happens if a single feature is allowed to set research HIGH.

Every scenario is reported; the tool does not choose thresholds based on
validation labels or repeatedly lower them until named accounts become HIGH.
Three sensitivity scenarios and many alternative windows are exploratory,
not independent tests or out-of-sample validation.

Run official and estimated Accuracy **separately**. Their biases and selection
mechanisms differ. Published scores are often missing non-randomly; estimated
scores are not asserted to reproduce Chess.com. A small selected cohort,
even with leave-one-player-out reference construction, is not a representative
human population or a new independent holdout. A smurf, legitimate improvement,
rating lag or a hot streak can produce elevated results without assistance.

## Input and private output

```json
{"players": [{"player_key": "anonymous-a", "games": [{
  "identity": "unique-physical-game", "ended": 1700000000,
  "rated": true, "rules": "chess", "time_class": "blitz", "time_control": "300+0",
  "player_elo": 1000, "opponent_elo": 1100, "score": 0.5,
  "official_accuracy": null, "estimated_accuracy": 72.4
}]}]}
```

Convert existing serialized `GameSample` fields as follows: `rating` →
`player_elo`, `opponent_rating` → `opponent_elo`, `accuracy` →
`official_accuracy`. Preserve `identity`, `ended`, `rated`, `time_class`,
`time_control` and `score`. Supply `estimated_accuracy` from the same unchanged
estimator for subjects and references, or leave it null. Do not substitute
missing official scores with estimates in the official experiment. Only rated
standard live games enter the study; variants, Daily and unrated games do not.

```sh
python scripts/fairplay_outcome_study.py \
  --input /private/study-input.json \
  --output /private/study-official.json \
  --csv /private/study-official.csv --source official_accuracy
```

Repeat with `--source estimated_accuracy` and different output paths. JSON
contains all periods, scenarios and single-feature ablations. CSV gives the
selected period for each player/scenario/mode. Output must be outside Git
checkouts, is written mode 0600, and routine output contains no player keys or
case measurements. Real case identities and labels never belong in public
fixtures, PR descriptions or CI logs. No nightly calibration or external jobs
are started. Tests use synthetic players and distributions only.

## Opt-in post-hoc development sensitivity

`--development-sensitivity` retains the three original scenarios and adds
`development_lower/middle/upper`: result residual 2.35/2.40/2.45 and score
excess .18/.20/.22. Accuracy requirements remain 8 points and .60. These
thresholds were selected AFTER examining the development cohort. They are
not an independent validation or a new production default. Research JSON
explicitly records this provenance. Adjacent settings measure sensitivity,
not generalization. Results-only HIGH does not establish abnormal gameplay;
underrating, real improvement and multiple-window selection remain explanations.
Use a new untouched cohort and frozen thresholds before considering deployment.
