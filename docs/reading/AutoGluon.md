## 1. What is AutoGluon

AutoGluon is an AutoML library that automates the end-to-end machine learning process.

Instead of manually selecting algorithms, tuning hyperparameters, and comparing results, AutoGluon performs these steps automatically and returns the best-performing model.

A useful mental model:

AutoGluon behaves like an experienced ML engineer that:

- Tries multiple algorithms
- Tunes each one
- Compares results
- Combines models when beneficial
- Returns the best final model

---

## 2. How AutoML Works (Step-by-Step)

### Input Data

You provide a dataset with features and a label.

Example:

```json
{
  "feature_1": 0.82,
  "feature_2": 14,
  "feature_3": "udp",
  "label": "authentication_failure"
}
```

---

### Step 1: Data Preprocessing

AutoGluon automatically:

- Handles missing values
- Encodes categorical variables
- Normalizes numerical data when needed

You do not need to write preprocessing pipelines manually.

---

### Step 2: Train Multiple Models

AutoGluon trains many different algorithms in parallel, such as:

- Random Forest
- XGBoost
- Neural Networks
- k-Nearest Neighbors
- LightGBM

Instead of choosing one model, it explores a wide search space.

---

### Step 3: Hyperparameter Tuning

Each model is trained multiple times with different configurations:

- Tree depth
- Learning rate
- Regularization parameters

This is fully automated.

---

### Step 4: Validation and Scoring

Each model is evaluated using metrics such as:

- Accuracy
- F1 score
- ROC-AUC

AutoGluon maintains a leaderboard of model performance.

---

### Step 5: Ensembling

AutoGluon does not necessarily select a single model.

It often creates a weighted ensemble of top-performing models.

Example:

```
Final Prediction =
  40% XGBoost +
  30% Neural Network +
  30% Random Forest
```

This typically improves overall performance.

---

### Step 6: Final Model Selection

The output includes:

- The best model (often an ensemble)
- Model artifacts
- Feature importance
- Leaderboard

---

## 3. Key Concept

AutoML is not a single model.

It is a system that performs:

- Model search
- Hyperparameter optimization
- Model comparison
- Ensembling

---

## 4. How AutoGluon Fits Into IMS Pipeline

### Phase 1: Feature Engineering

- SIPp generates traffic
- Features are computed and stored (e.g., via Feast)

### Phase 2: Training with AutoGluon

Example:

```python
from autogluon.tabular import TabularPredictor

predictor = TabularPredictor(label="anomaly_type")
predictor.fit(train_data)
```

AutoGluon:

- Trains multiple models
- Tunes them
- Builds an ensemble

---

### Output

Artifacts produced:

- Trained model files
- Leaderboard
- Feature importance

---

### Phase 3: Model Serving

The trained model is deployed using serving infrastructure such as:

- KServe
- Triton Inference Server

---

### Phase 4: Inference

New traffic is processed into features and passed to the model.

Example output:

```json
{
  "prediction": "authentication_failure",
  "confidence": 0.97
}
```

---

## 5. How to Inspect the Final Model

To see which models were used:

```python
predictor.leaderboard()
```

Example:

```
model                score
WeightedEnsemble     0.98
LightGBM             0.96
XGBoost              0.95
NeuralNet            0.94
```

The final model is typically a weighted ensemble.

Additional inspection:

```python
predictor.info()
predictor.get_model_names()
```

---

## 6. Strengths and Limitations

### Strengths

- Eliminates manual model selection
- Produces strong baseline performance quickly
- Ideal for demos and rapid development

### Limitations

- Less control over model internals
- Harder to debug deeply
- Not suitable for specialized architectures such as graph-based models

---

## 7. When to Use AutoGluon

Use it for:

- Rapid prototyping
- Anomaly detection baselines
- Demonstrations
- General tabular data problems

Avoid it for:

- Custom model architectures
- Advanced root cause analysis logic
- Systems requiring full control over training

---

## 8. Presentation Positioning

A concise way to explain it:

AutoGluon allows us to automatically explore multiple machine learning algorithms, tune them, and build an ensemble model. This lets us focus on data quality and feature engineering rather than manual model selection.

---

## 9. Final Mental Model

- Feature Store: structured feature data source
- AutoGluon: automated model search and optimization engine
- Final Model: optimized and possibly ensembled predictor used in production
