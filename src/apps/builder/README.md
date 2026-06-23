# Medagg Dataset Builder Foundation Model Card

## Purpose

The builder foundation uses three small supervised text models:

1. a multilabel dataset-metadata classifier;
2. a multilabel user-request classifier;
3. a multiclass field-semantic classifier.

The models support schema discovery, dataset tagging, candidate ranking, and
semantic mapping. They do **not** decide whether record linkage is legally or
ethically permissible. Deterministic authorization and privacy rules remain
authoritative.

## Model family

- Word TF-IDF with one-vs-rest logistic regression for metadata tags.
- Word TF-IDF with one-vs-rest logistic regression for request tags.
- Word and character TF-IDF features with logistic regression for field
  semantics.

The implementation is CPU-only and deterministic for the bundled corpus.
Model artifacts are versioned by their training-data SHA-256 checksum and are
validated by an artifact SHA-256 checksum before loading.

## Bootstrap training data

The bundled bootstrap corpus contains synthetic and hand-authored bilingual
English/Russian examples covering:

- anatomical areas;
- modalities;
- machine-learning tasks;
- medical topics;
- common pseudonymous identifiers;
- direct and quasi-identifiers;
- clinical target and measurement columns.

It is intended to make the first local prototype useful and testable. It is
not a representative clinical benchmark and must be expanded with reviewed,
project-specific examples before production use.

## Evaluation

`python manage.py train_builder_models` records deterministic holdout metrics
in `BuilderModelRelease.metrics`. These measurements only describe the bundled
bootstrap split. They must not be interpreted as real-world clinical accuracy.

## Safety boundaries

The learned models cannot authorize a dataset or field. The following rules
are deterministic and override model output:

- only datasets in the requesting user's `DatasetMembership` library may be
  selected;
- direct identifiers are excluded from output;
- age, sex, city, location, and timestamps are never automatic linkage keys;
- automatic joins require a high-confidence pseudonymous patient, subject,
  study, or encounter identifier with sufficient uniqueness;
- the raw linkage key is omitted from the derived output;
- authorization, schema fingerprints, plan checksums, and privacy approval are
  revalidated immediately before execution;
- no raw sample values are persisted by schema profiling.

Pseudonymized medical records can still be personal and sensitive data. A
successful automated privacy gate is not a legal basis for processing.

## Known limitations

- Dataset compatibility is currently limited to exact inner joins.
- CSV, TSV, Parquet, JSON, and JSONL are supported; imaging archives are tagged
  from metadata but are not transformed directly.
- The model does not infer probabilistic person matches.
- Semantic coverage is intentionally narrow.
- Output licensing is inherited from all inputs and is not resolved into a new
  legal license automatically.
- Bootstrap metrics can be optimistic because examples are synthetic.

## Retraining and release

Retrain and activate the deterministic bundled release with:

```bash
python manage.py train_builder_models --force
```

To improve the model, extend `training_data.py`, review the examples, run the
focused builder tests, retrain, and inspect the recorded metrics and resulting
plan behavior. Changing the corpus changes the release version because the
training-data checksum changes.
