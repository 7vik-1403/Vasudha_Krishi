# Youtube link for the demonstration video of Project :-
https://youtu.be/bVx8CFR5vHg
# Drive link for the demonstration video :-
https://drive.google.com/file/d/11ZjEZmacig_mYpmMGFGWz7kNoefZSkw2/view?usp=sharing

# Crop-Recommendation-System-Using-ML

Agriculture is the backbone of many economies worldwide, and its success relies heavily on the decisions made by farmers and agricultural professionals. The choice of crops to cultivate is a critical decision that affects yield, profit, and overall agricultural sustainability. To assist in this decision-making process, we present the Crop Recommendation System—a powerful tool designed to empower farmers with data-driven insights and recommendations for optimal crop selection.

The Crop Recommendation System leverages cutting-edge technology, including machine learning and data analysis, to provide tailored crop recommendations based on a wide range of factors. By analyzing geographical location, soil type, climate conditions, and historical crop performance data, this system aims to equip farmers with the knowledge they need to make informed choices. In doing so, it not only enhances the productivity and profitability of agricultural endeavors but also promotes sustainable farming practices.

## What the app does

- **Map-first location picking** — drop a pin anywhere in India (or search a district) and the app
  reverse-geocodes it to district + state, then fetches soil and climate data for that exact point:
  - soil pH, organic carbon, nitrogen and texture from [SoilGrids 250 m](https://soilgrids.org/)
    (with bundled national defaults where the grid has no coverage),
  - available N-P-K from your own Soil Health Card readings (entered per field),
  - temperature and humidity from Open-Meteo archive (multi-year monthly means),
  - rainfall from IMD district monthly normals (state-average fallback for new districts).
- **Crop recommendation** — retrained scikit-learn model (Gaussian Naive Bayes, see
  `model_report.json`) over N, P, K, temperature, humidity, pH and rainfall, returning the crop,
  top-3 alternatives and confidence.
- **7-day weather & advisories** — live Open-Meteo forecast for the pinned field with bilingual
  irrigation / spray / heat-stress advisories (`cropapp/services/weather.py`, `/api/forecast`). No
  key required; the panel hides itself gracefully when offline.
- **Leaf-photo disease scan** — upload a leaf photo and Gemini vision returns the likely
  disease/pest/deficiency with an organic + chemical action plan (`cropapp/services/disease.py`,
  `/api/disease`). Needs `GEMINI_API_KEY`; without it you get a friendly "switched off" message.
  A successful scan is saved to the field as a note (`source="disease"`).
- **Farm analytics** — an aggregate dashboard across all your fields: KPIs, expenses by category and
  by month, crops by area, task completion and soil N-P-K, each chart theme-aware
  (`cropapp/services/analytics.py`, `/analytics`).
- **Model & data transparency page** — a public `/model` page that renders `model_report.json`: the
  candidate leaderboard, confusion matrix, feature list and the probability-calibration experiment.
- **Shareable field plan** — generate a read-only public link (`/share/<token>`) that shows a field's
  crop plan, fertilizer dose, water plan, market estimate and schedule — without exposing private
  notes or expenses. Revoke it any time from the field header.
- **One-click demo** — a shared `demo@vasudha.local` account pre-seeded with two farms (Punjab canal
  field, Maharashtra borewell field), full recommendations, tasks and field history, so the app can be
  toured without network or setup (`/account/demo`, `cropapp/services/demo.py`).
- **Fertilizer doses** — urea / DAP / MOP in kg per hectare, kg per acre and total for your field
  size, split across basal, tillering and tablet stages, plus MSP reference and the local crop
  calendar (sowing, irrigation, pest windows, harvest).
- **Profitability estimate** — revenue (MSP × reference yield), itemised costs (seed, fertilizer,
  spray, irrigation, labour, other) and net profit per hectare / acre / field, shown for worst /
  expected / best cases and compared across the crop shortlist. Costs come from editable planning
  assumptions in `data/crop_economics.json`; every panel is labelled an estimate, not a market quote.
- **Explainable ranking** — each shortlisted crop shows its model probability, agronomic fit score
  (temperature / rainfall / pH windows) and water penalty as separate factors, so the combined score
  can be explained rather than just asserted. The model score is shown as an uncalibrated
  classifier probability.
- **Climate-risk notes** — rainfall deficit/excess, dry spells, heat/cold stress and crop-specific
  window mismatches, tagged by basis (IMD monthly normal, most recent observed month, or derived).
  There is no live forecast in this deployment and the panel says so.
- **Irrigation planner** — seasonal water need, an indicative number of irrigation events and rough
  cost from the crop's water data and your declared source.
- **Field history & actual-vs-predicted** — log dated soil tests, expenses, harvests, irrigation and
  observations per farm; the app charts the soil trend, totals expenses and harvests, and compares a
  recorded harvest with the estimate stored when the recommendation was saved.
- **Farm workspace** — every farm opens as a tabbed workspace (Overview, Crops, Soil & nutrition,
  Irrigation, Calendar & tasks, Expenses, AI assistant). The active tab is kept in the `?tab=` URL so
  links and refreshes stay put, and each tab groups only related content instead of one long column.
- **Farm expense manager** — record categorised expenses (seeds, fertilizers, pesticides, labour,
  irrigation, energy, equipment, land prep, transport, other) with date, crop, season, vendor and
  payment method; edit or delete them inline. The summary breaks spend down by category, crop, season
  and month, and per-crop / per-season / whole-farm budgets warn as you approach and exceed them.
  Recorded spend replaces the matching line in the crop profitability estimate (without double
  counting) to show projected and — once a harvest is logged — true net profit.
- **What-if simulator** — re-estimate the saved crop under changed rainfall, irrigation days, price
  factor, yield factor and fertilizer-cost factor, side by side with the saved baseline, without
  altering the saved recommendation.
- **Farmer account** — save multiple farms, each with its own soil readings, recommendation and a
  per-farm task list you can tick off as you go.
- **AI chat assistant** — Gemini-powered advisor in English or Marathi that answers questions with
  concrete numbers and can schedule fertilizer / pesticide / irrigation tasks straight onto your
  farms. Its context now includes the profit estimate, climate risks and your logged field history.
  Retries transient errors and falls through to backup models when the primary one is overloaded, and
  rate-limits per user/IP. Works without a key too: it falls back to the locally generated calendar
  and doses.
- **English / Marathi** — switch the whole interface from the nav bar.

## Setup

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Copy `.env.example` to `.env` (`.env` is gitignored):

```
SECRET_KEY=<random hex>        # signs session cookies; set this in production
GEMINI_API_KEY=your-key-from-https://aistudio.google.com/apikey   # optional, enables AI chat
```

If `SECRET_KEY` is left unset the app falls back to a built-in development value and logs a
warning on start. Other optional settings (model fallbacks, chat rate limit, dev-server host/port)
are documented in `.env.example`.

## Run

```bash
python app.py
```

The database is created automatically on first start (`instance/app.db`), or run
`flask --app app init-db` to create it explicitly. Open http://127.0.0.1:5000

The development server starts with debug mode **off**; set `FLASK_DEBUG=1` in `.env` to turn it on
locally. Override the bind address with `FLASK_HOST` / `FLASK_PORT`.

## Tests

```bash
python -m pytest tests -q
```

The suite covers the resolver, prediction, fertilizer maths, scheduler, auth, farms, the chat
endpoint (Gemini is mocked), the new `/api/forecast` and `/api/disease` endpoints, the demo account,
the analytics and model pages, and the public share links. For a quick live check against the real
network APIs:

```bash
python scripts/t_resolve.py      # district resolution + rainfall normals
python scripts/t_smoke.py        # register → predict → save farm → share → demo/analytics → model
```

## Retraining the model

```bash
python train.py                        # evaluate all candidates, ship the best
python train.py --model rf             # force one candidate
python train.py --calibration isotonic # calibration method (default sigmoid)
python train.py --no-calibrate         # ship the raw estimator
```

Compares seven classifiers (Gaussian Naive Bayes, Decision Tree, Random Forest, Histogram Gradient
Boosting, RBF SVM, KNN, Logistic Regression) with stratified 5-fold cross-validation and reports
hold-out accuracy, macro precision / recall / F1, balanced accuracy and top-3 accuracy for each. It
drops exact duplicate rows before splitting, then attempts probability calibration on the winner and
**ships the calibrated model only when it improves log-loss and Brier score** — on the well-separated
public dataset calibration does not help, so the raw estimator is kept and the before/after numbers
are recorded in `model_report.json` as an auditable experiment. The winner is written to `model.pkl`
and all metrics to `model_report.json`, which the `/model` page renders.

## Configuration flags

Beyond `.env`, behaviour can be toggled with `DISEASE_DETECTION_ENABLED`, `DISEASE_MAX_IMAGE_BYTES`,
`DEMO_MODE_ENABLED` and `SEED_DEMO_ON_START` (see `cropapp/config.py`).
