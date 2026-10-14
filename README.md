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
- **Fertilizer doses** — urea / DAP / MOP in kg per hectare, kg per acre and total for your field
  size, split across basal, tillering and tablet stages, plus MSP reference and the local crop
  calendar (sowing, irrigation, pest windows, harvest).
- **Farmer account** — save multiple farms, each with its own soil readings, recommendation and a
  per-farm task list you can tick off as you go.
- **AI chat assistant** — Gemini-powered advisor in English or Marathi that answers questions with
  concrete numbers and can schedule fertilizer / pesticide / irrigation tasks straight onto your
  farms. Works without a key too: it falls back to the locally generated calendar and doses.
- **English / Marathi** — switch the whole interface from the nav bar.

## Setup

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Create a `.env` (see `.env.example`) if you want the AI chat:

```
GEMINI_API_KEY=your-key-from-https://aistudio.google.com/apikey
```

## Run

```bash
python app.py
```

The database is created automatically on first start (`instance/app.db`), or run
`flask --app app init-db` to create it explicitly. Open http://127.0.0.1:5000

## Tests

```bash
python -m pytest tests -q
```

The suite covers the resolver, prediction, fertilizer maths, scheduler, auth, farms and the chat
endpoint (Gemini is mocked). For a quick live check against the real network APIs:

```bash
python scripts/t_resolve.py      # district resolution + rainfall normals
python scripts/t_smoke.py        # full register → predict → save farm flow
```

## Retraining the model

```bash
python train.py
```

Compares six classifiers with 5-fold cross-validation, writes the winner to `model.pkl` and the
scores to `model_report.json`. The previous model is kept as `model_gaussianNB_v1.pkl`.
