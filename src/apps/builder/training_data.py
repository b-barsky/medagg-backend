from __future__ import annotations



LABEL_CATALOG: dict[str, dict[str, str]] = {
    "area:lung": {"namespace": "anatomical_area", "value": "Lung"},
    "area:brain": {"namespace": "anatomical_area", "value": "Brain"},
    "area:heart": {"namespace": "anatomical_area", "value": "Heart"},
    "area:breast": {"namespace": "anatomical_area", "value": "Breast"},
    "area:abdomen": {"namespace": "anatomical_area", "value": "Abdomen"},
    "area:chest": {"namespace": "anatomical_area", "value": "Chest"},
    "modality:ct": {"namespace": "modality", "value": "CT"},
    "modality:mri": {"namespace": "modality", "value": "MRI"},
    "modality:xray": {"namespace": "modality", "value": "X-ray"},
    "modality:ultrasound": {
        "namespace": "modality",
        "value": "Ultrasound",
    },
    "modality:pathology": {
        "namespace": "modality",
        "value": "Pathology",
    },
    "modality:clinical": {
        "namespace": "modality",
        "value": "Clinical tabular",
    },
    "ml_task:classification": {
        "namespace": "ml_task",
        "value": "Classification",
    },
    "ml_task:segmentation": {
        "namespace": "ml_task",
        "value": "Segmentation",
    },
    "ml_task:detection": {
        "namespace": "ml_task",
        "value": "Object detection",
    },
    "ml_task:regression": {
        "namespace": "ml_task",
        "value": "Regression",
    },
    "ml_task:survival": {
        "namespace": "ml_task",
        "value": "Survival analysis",
    },
    "tag:oncology": {"namespace": "tag", "value": "Oncology"},
    "tag:smoking": {"namespace": "tag", "value": "Smoking"},
    "tag:covid": {"namespace": "tag", "value": "COVID-19"},
    "tag:neurology": {"namespace": "tag", "value": "Neurology"},
    "tag:cardiology": {"namespace": "tag", "value": "Cardiology"},
    "tag:demographics": {
        "namespace": "tag",
        "value": "Demographics",
    },
}


_AREA_TERMS = {
    "area:lung": ("lung", "pulmonary", "respiratory", "лёгких", "легких"),
    "area:brain": ("brain", "cerebral", "neuro", "головного мозга"),
    "area:heart": ("heart", "cardiac", "cardiovascular", "сердца"),
    "area:breast": ("breast", "mammary", "молочной железы"),
    "area:abdomen": ("abdomen", "abdominal", "брюшной полости"),
    "area:chest": ("chest", "thoracic", "грудной клетки"),
}

_MODALITY_TERMS = {
    "modality:ct": ("CT", "computed tomography", "КТ"),
    "modality:mri": ("MRI", "magnetic resonance", "МРТ"),
    "modality:xray": ("X-ray", "radiograph", "рентген"),
    "modality:ultrasound": ("ultrasound", "sonography", "УЗИ"),
    "modality:pathology": ("histopathology", "whole slide", "биопсия"),
    "modality:clinical": ("clinical records", "tabular cohort", "анкеты"),
}

_TASK_TERMS = {
    "ml_task:classification": ("classification", "class label", "классификация"),
    "ml_task:segmentation": ("segmentation masks", "contours", "сегментация"),
    "ml_task:detection": ("lesion detection", "bounding boxes", "детекция"),
    "ml_task:regression": ("regression target", "continuous outcome", "регрессия"),
    "ml_task:survival": ("survival time", "time to event", "выживаемость"),
}

_TAG_TERMS = {
    "tag:oncology": ("cancer", "tumor", "oncology", "рак", "опухоль"),
    "tag:smoking": ("smoking", "tobacco", "smoker", "курение"),
    "tag:covid": ("COVID-19", "SARS-CoV-2", "ковид"),
    "tag:neurology": ("neurology", "stroke", "dementia", "неврология"),
    "tag:cardiology": ("cardiology", "arrhythmia", "infarction", "кардиология"),
    "tag:demographics": ("age sex demographics", "population cohort", "демография"),
}


def _first(values: tuple[str, ...], offset: int) -> str:
    return values[offset % len(values)]


def dataset_training_examples() -> list[tuple[str, tuple[str, ...]]]:
    examples: list[tuple[str, tuple[str, ...]]] = []
    templates = (
        "{modality} dataset of {area} patients for {task}. {tag} cohort.",
        "Medical {modality} images covering {area}; annotations support {task}. {tag}.",
        "Набор данных: {modality}, область {area}, задача {task}. Тематика: {tag}.",
        "Cohort metadata and {modality} observations for {area}. Intended for {task}; {tag}.",
    )
    areas = list(_AREA_TERMS)
    modalities = list(_MODALITY_TERMS)
    tasks = list(_TASK_TERMS)
    tags = list(_TAG_TERMS)
    for index in range(180):
        area_label = areas[index % len(areas)]
        modality_label = modalities[(index * 3 + 1) % len(modalities)]
        task_label = tasks[(index * 5 + 2) % len(tasks)]
        tag_label = tags[(index * 7 + 3) % len(tags)]
        text = templates[index % len(templates)].format(
            area=_first(_AREA_TERMS[area_label], index),
            modality=_first(_MODALITY_TERMS[modality_label], index + 1),
            task=_first(_TASK_TERMS[task_label], index + 2),
            tag=_first(_TAG_TERMS[tag_label], index + 3),
        )
        labels = (area_label, modality_label, task_label, tag_label)
        examples.append((text, labels))

    examples.extend(
        [
            (
                "Smoking history, lung cancer diagnosis, patient_id and Moscow cohort",
                ("area:lung", "modality:clinical", "tag:smoking", "tag:oncology"),
            ),
            (
                "КТ лёгких пациентов с COVID-19, маски поражений",
                ("area:lung", "modality:ct", "ml_task:segmentation", "tag:covid"),
            ),
            (
                "Brain MRI for tumor classification and segmentation",
                ("area:brain", "modality:mri", "ml_task:classification", "tag:oncology"),
            ),
            (
                "Cardiac ultrasound measurements with regression outcomes",
                ("area:heart", "modality:ultrasound", "ml_task:regression", "tag:cardiology"),
            ),
        ]
    )
    return examples


def prompt_training_examples() -> list[tuple[str, tuple[str, ...]]]:
    examples = dataset_training_examples()
    prompt_templates = (
        "Build a dataset of {tag} patients using {modality} of the {area} for {task}",
        "Combine my {area} datasets and create a {task} table about {tag}",
        "Собери датасет по теме {tag}: {area}, модальность {modality}, задача {task}",
    )
    generated: list[tuple[str, tuple[str, ...]]] = []
    combinations = zip(
        _AREA_TERMS.items(),
        _MODALITY_TERMS.items(),
        _TASK_TERMS.items(),
        _TAG_TERMS.items(),
        strict=False,
    )
    for index, (area, modality, task, tag) in enumerate(combinations):
        generated.append(
            (
                prompt_templates[index % len(prompt_templates)].format(
                    area=area[1][0],
                    modality=modality[1][0],
                    task=task[1][0],
                    tag=tag[1][0],
                ),
                (area[0], modality[0], task[0], tag[0]),
            )
        )
    return examples + generated * 6


_FIELD_TERMS: dict[str, tuple[str, ...]] = {
    "patient_id": (
        "patient_id",
        "patientid",
        "patient_key",
        "medical_record_hash",
        "id_patient",
        "идентификатор_пациента",
    ),
    "subject_id": ("subject_id", "subject_code", "participant_id", "case_id"),
    "study_id": ("study_id", "study_uid", "research_id", "series_study_id"),
    "encounter_id": ("encounter_id", "visit_id", "admission_id", "episode_id"),
    "image_id": ("image_id", "sop_instance_uid", "scan_id", "dicom_id"),
    "generic_id": ("id", "row_id", "record_id", "key", "identifier"),
    "age": ("age", "patient_age", "age_years", "возраст"),
    "sex": ("sex", "gender", "biological_sex", "пол"),
    "city": ("city", "town", "municipality", "город"),
    "location": ("location", "region", "country", "address_region", "местоположение"),
    "smoking_status": ("smoking_status", "smoker", "tobacco_use", "pack_years", "курение"),
    "diagnosis": ("diagnosis", "icd_code", "condition", "disease", "диагноз"),
    "cancer_status": ("cancer_status", "malignancy", "tumor_status", "oncology_label"),
    "timestamp": ("timestamp", "event_time", "study_date", "created_at", "дата"),
    "modality": ("modality", "scan_type", "imaging_method", "модальность"),
    "target": ("target", "label", "outcome", "class", "ground_truth"),
    "free_text": ("notes", "report", "comment", "description", "анамнез"),
    "numeric_measurement": ("measurement", "value", "diameter_mm", "weight", "score"),
    "categorical": ("category", "group", "stage", "status", "type"),
    "unknown": ("misc", "field", "attribute", "column", "data"),
}


def field_training_examples() -> list[tuple[str, str]]:
    examples: list[tuple[str, str]] = []
    type_cues = {
        "patient_id": "string uuid hash mostly unique",
        "subject_id": "string coded participant mostly unique",
        "study_id": "string uid study unique",
        "encounter_id": "string visit code mostly unique",
        "image_id": "string dicom uid unique",
        "generic_id": "integer or string unique key",
        "age": "numeric years bounded 0 120",
        "sex": "categorical female male unknown",
        "city": "categorical geographic city names",
        "location": "categorical region country location",
        "smoking_status": "categorical current former never smoker",
        "diagnosis": "categorical ICD diagnosis disease codes",
        "cancer_status": "binary malignant benign cancer",
        "timestamp": "date timestamp ISO calendar",
        "modality": "categorical CT MRI X-ray ultrasound",
        "target": "label outcome prediction target",
        "free_text": "long natural language text",
        "numeric_measurement": "continuous numeric measurement",
        "categorical": "low-cardinality categorical string",
        "unknown": "unrecognized mixed values",
    }
    for label, aliases in _FIELD_TERMS.items():
        for index, alias in enumerate(aliases):
            examples.append(
                (
                    f"column {alias}; physical type string; {type_cues[label]}; variant {index}",
                    label,
                )
            )
            examples.append(
                (
                    f"поле {alias}; {type_cues[label]}; schema column",
                    label,
                )
            )
    return examples


def training_payload() -> dict[str, object]:
    return {
        "label_catalog": LABEL_CATALOG,
        "datasets": dataset_training_examples(),
        "prompts": prompt_training_examples(),
        "fields": field_training_examples(),
    }
