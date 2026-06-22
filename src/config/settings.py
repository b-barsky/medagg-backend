"""
Django settings for the Medagg backend.
"""

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    raw_value = os.environ.get(name)

    if raw_value is None:
        value = default
    else:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"Environment variable {name} must be an integer."
            ) from exc

    if minimum is not None and value < minimum:
        raise ValueError(
            f"Environment variable {name} must be at least {minimum}."
        )

    return value


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
) -> float:
    raw_value = os.environ.get(name)

    if raw_value is None:
        value = default
    else:
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"Environment variable {name} must be a number."
            ) from exc

    if minimum is not None and value < minimum:
        raise ValueError(
            f"Environment variable {name} must be at least {minimum}."
        )

    return value


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)

    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"Environment variable {name} must be a boolean."
    )


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        value.strip().lower()
        for value in os.environ.get(name, default).split(",")
        if value.strip()
    )


def _env_optional_url(
    name: str,
    default: str | None,
) -> str | None:
    raw_value = os.environ.get(name)

    if raw_value is None:
        raw_value = default or ""

    normalized = raw_value.strip().rstrip("/")
    return normalized or None


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")

DEBUG = _env_bool("DJANGO_DEBUG_MODE", False)

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS",
        "127.0.0.1,localhost",
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party apps.
    "rest_framework",
    # Local apps.
    "apps.catalog",
    "apps.datasets",
    "apps.search",
    "apps.users",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PAGINATION_CLASS": (
        "rest_framework.pagination.PageNumberPagination"
    ),
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "login": os.environ.get("AUTH_LOGIN_RATE", "10/minute"),
        "registration": os.environ.get(
            "AUTH_REGISTRATION_RATE",
            "5/hour",
        ),
    },
}

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = _env_bool(
    "DJANGO_SESSION_COOKIE_SECURE",
    False,
)
SESSION_COOKIE_AGE = _env_int(
    "DJANGO_SESSION_COOKIE_AGE_SECONDS",
    14 * 24 * 60 * 60,
    minimum=60,
)
SESSION_SAVE_EVERY_REQUEST = False

# The React client reads the CSRF cookie and mirrors it into X-CSRFToken.
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = _env_bool(
    "DJANGO_CSRF_COOKIE_SECURE",
    False,
)
_default_csrf_trusted_origins = (
    "http://localhost:5173,http://127.0.0.1:5173"
    if DEBUG
    else ""
)
CSRF_TRUSTED_ORIGINS = [
    value.strip()
    for value in os.environ.get(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        _default_csrf_trusted_origins,
    ).split(",")
    if value.strip()
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.{}".format(
            os.environ.get("DB_ENGINE", "sqlite3")
        ),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", 5432),
        "NAME": os.environ.get("DB_NAME"),
        "USER": os.environ.get("DB_USER"),
        "PASSWORD": os.environ.get("DB_PASSWORD"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Durable asynchronous execution
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL",
    "redis://redis:6379/0",
)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

# Search state is persisted in PostgreSQL, so Celery result tombstones are not
# needed in Redis.
CELERY_TASK_IGNORE_RESULT = True

# Tasks are idempotent and can be redelivered after worker loss.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT = _env_float(
    "CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT_SECONDS",
    30.0,
    minimum=0.0,
)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_TIMEOUT = _env_float(
    "CELERY_BROKER_CONNECTION_TIMEOUT_SECONDS",
    3.0,
    minimum=0.1,
)
CELERY_TASK_PUBLISH_RETRY = True
CELERY_TASK_PUBLISH_RETRY_POLICY = {
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.2,
    "interval_max": 0.5,
}
CELERY_VISIBILITY_TIMEOUT = _env_int(
    "CELERY_VISIBILITY_TIMEOUT_SECONDS",
    3600,
    minimum=1,
)
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": CELERY_VISIBILITY_TIMEOUT,
    "socket_connect_timeout": _env_float(
        "CELERY_REDIS_CONNECT_TIMEOUT_SECONDS",
        3.0,
        minimum=0.1,
    ),
    "socket_timeout": _env_float(
        "CELERY_REDIS_SOCKET_TIMEOUT_SECONDS",
        5.0,
        minimum=0.1,
    ),
    "retry_on_timeout": True,
}
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS = True

SEARCH_TASK_QUEUE = os.environ.get(
    "SEARCH_TASK_QUEUE",
    "catalog-search",
).strip()
SEARCH_DETAIL_TASK_QUEUE = os.environ.get(
    "SEARCH_DETAIL_TASK_QUEUE",
    "catalog-detail",
).strip()
DATASET_IMPORT_TASK_QUEUE = os.environ.get(
    "DATASET_IMPORT_QUEUE",
    "dataset-import",
).strip()

if not SEARCH_TASK_QUEUE:
    raise ValueError("SEARCH_TASK_QUEUE cannot be blank.")

if not SEARCH_DETAIL_TASK_QUEUE:
    raise ValueError("SEARCH_DETAIL_TASK_QUEUE cannot be blank.")

if not DATASET_IMPORT_TASK_QUEUE:
    raise ValueError("DATASET_IMPORT_QUEUE cannot be blank.")

CELERY_TASK_DEFAULT_QUEUE = SEARCH_TASK_QUEUE
CELERY_TASK_ROUTES = {
    "search.provider": {
        "queue": SEARCH_TASK_QUEUE,
    },
    "search.result.enrich": {
        "queue": SEARCH_DETAIL_TASK_QUEUE,
    },
    "search.run.expire": {
        "queue": SEARCH_TASK_QUEUE,
    },
    "datasets.import": {
        "queue": DATASET_IMPORT_TASK_QUEUE,
    },
}

SEARCH_RUN_TIMEOUT_SECONDS = _env_int(
    "SEARCH_RUN_TIMEOUT_SECONDS",
    240,
    minimum=30,
)
SEARCH_PROVIDER_START_TIMEOUT_SECONDS = _env_int(
    "SEARCH_PROVIDER_START_TIMEOUT_SECONDS",
    60,
    minimum=5,
)
SEARCH_PROVIDER_SOFT_TIME_LIMIT_SECONDS = _env_int(
    "SEARCH_PROVIDER_SOFT_TIME_LIMIT_SECONDS",
    45,
    minimum=1,
)
SEARCH_PROVIDER_HARD_TIME_LIMIT_SECONDS = _env_int(
    "SEARCH_PROVIDER_HARD_TIME_LIMIT_SECONDS",
    60,
    minimum=2,
)
SEARCH_PROVIDER_MAX_RETRIES = _env_int(
    "SEARCH_PROVIDER_MAX_RETRIES",
    2,
    minimum=0,
)
SEARCH_PROVIDER_RETRY_BACKOFF_SECONDS = _env_int(
    "SEARCH_PROVIDER_RETRY_BACKOFF_SECONDS",
    5,
    minimum=1,
)
SEARCH_PROVIDER_RETRY_BACKOFF_MAX_SECONDS = _env_int(
    "SEARCH_PROVIDER_RETRY_BACKOFF_MAX_SECONDS",
    30,
    minimum=1,
)
SEARCH_PROVIDER_RATE_LIMIT = os.environ.get(
    "SEARCH_PROVIDER_RATE_LIMIT",
    "2/s",
)

SEARCH_DETAIL_SOFT_TIME_LIMIT_SECONDS = _env_int(
    "SEARCH_DETAIL_SOFT_TIME_LIMIT_SECONDS",
    45,
    minimum=1,
)
SEARCH_DETAIL_HARD_TIME_LIMIT_SECONDS = _env_int(
    "SEARCH_DETAIL_HARD_TIME_LIMIT_SECONDS",
    60,
    minimum=2,
)
SEARCH_DETAIL_MAX_RETRIES = _env_int(
    "SEARCH_DETAIL_MAX_RETRIES",
    2,
    minimum=0,
)
SEARCH_DETAIL_RETRY_BACKOFF_SECONDS = _env_int(
    "SEARCH_DETAIL_RETRY_BACKOFF_SECONDS",
    5,
    minimum=1,
)
SEARCH_DETAIL_RETRY_BACKOFF_MAX_SECONDS = _env_int(
    "SEARCH_DETAIL_RETRY_BACKOFF_MAX_SECONDS",
    30,
    minimum=1,
)
SEARCH_DETAIL_RATE_LIMIT = os.environ.get(
    "SEARCH_DETAIL_RATE_LIMIT",
    "30/m",
)

SEARCH_POLL_INTERVAL_MS = _env_int(
    "SEARCH_POLL_INTERVAL_MS",
    1200,
    minimum=250,
)
SEARCH_RESULT_PAGE_SIZE = _env_int(
    "SEARCH_RESULT_PAGE_SIZE",
    20,
    minimum=1,
)
SEARCH_RESULT_MAX_PAGE_SIZE = _env_int(
    "SEARCH_RESULT_MAX_PAGE_SIZE",
    100,
    minimum=1,
)

if SEARCH_RESULT_MAX_PAGE_SIZE < SEARCH_RESULT_PAGE_SIZE:
    raise ValueError(
        "SEARCH_RESULT_MAX_PAGE_SIZE must be greater than or equal to "
        "SEARCH_RESULT_PAGE_SIZE."
    )

if (
    SEARCH_PROVIDER_HARD_TIME_LIMIT_SECONDS
    <= SEARCH_PROVIDER_SOFT_TIME_LIMIT_SECONDS
):
    raise ValueError(
        "SEARCH_PROVIDER_HARD_TIME_LIMIT_SECONDS must be greater than "
        "SEARCH_PROVIDER_SOFT_TIME_LIMIT_SECONDS."
    )

if (
    SEARCH_DETAIL_HARD_TIME_LIMIT_SECONDS
    <= SEARCH_DETAIL_SOFT_TIME_LIMIT_SECONDS
):
    raise ValueError(
        "SEARCH_DETAIL_HARD_TIME_LIMIT_SECONDS must be greater than "
        "SEARCH_DETAIL_SOFT_TIME_LIMIT_SECONDS."
    )

# ---------------------------------------------------------------------------
# Object storage and durable artifact ingestion
# ---------------------------------------------------------------------------

OBJECT_STORAGE_ACCESS_KEY = os.environ.get(
    "OBJECT_STORAGE_ACCESS_KEY",
    "medagg",
)
OBJECT_STORAGE_SECRET_KEY = os.environ.get(
    "OBJECT_STORAGE_SECRET_KEY",
    "medagg-development-only",
)
OBJECT_STORAGE_BUCKET = os.environ.get(
    "OBJECT_STORAGE_BUCKET",
    "medagg-datasets",
).strip()
OBJECT_STORAGE_REGION = os.environ.get(
    "OBJECT_STORAGE_REGION",
    "us-east-1",
).strip()
OBJECT_STORAGE_ENDPOINT_URL = _env_optional_url(
    "OBJECT_STORAGE_ENDPOINT_URL",
    "http://minio:9000",
)
OBJECT_STORAGE_PUBLIC_ENDPOINT_URL = _env_optional_url(
    "OBJECT_STORAGE_PUBLIC_ENDPOINT_URL",
    "http://127.0.0.1:9000",
)
OBJECT_STORAGE_ADDRESSING_STYLE = os.environ.get(
    "OBJECT_STORAGE_ADDRESSING_STYLE",
    "path",
).strip().lower()
OBJECT_STORAGE_PRESIGN_EXPIRY_SECONDS = _env_int(
    "OBJECT_STORAGE_PRESIGN_EXPIRY_SECONDS",
    900,
    minimum=60,
)
OBJECT_STORAGE_CONNECT_TIMEOUT_SECONDS = _env_float(
    "OBJECT_STORAGE_CONNECT_TIMEOUT_SECONDS",
    5.0,
    minimum=0.1,
)
OBJECT_STORAGE_READ_TIMEOUT_SECONDS = _env_float(
    "OBJECT_STORAGE_READ_TIMEOUT_SECONDS",
    120.0,
    minimum=1.0,
)
OBJECT_STORAGE_MAX_ATTEMPTS = _env_int(
    "OBJECT_STORAGE_MAX_ATTEMPTS",
    4,
    minimum=1,
)

if not OBJECT_STORAGE_BUCKET:
    raise ValueError("OBJECT_STORAGE_BUCKET cannot be blank.")

if OBJECT_STORAGE_ADDRESSING_STYLE not in {"auto", "path", "virtual"}:
    raise ValueError(
        "OBJECT_STORAGE_ADDRESSING_STYLE must be auto, path, or virtual."
    )

DATASET_IMPORT_MAX_BYTES = _env_int(
    "DATASET_IMPORT_MAX_BYTES",
    20 * 1024 * 1024 * 1024,
    minimum=1,
)
DATASET_IMPORT_SOFT_TIME_LIMIT_SECONDS = _env_int(
    "DATASET_IMPORT_SOFT_TIME_LIMIT_SECONDS",
    1800,
    minimum=1,
)
DATASET_IMPORT_HARD_TIME_LIMIT_SECONDS = _env_int(
    "DATASET_IMPORT_HARD_TIME_LIMIT_SECONDS",
    1860,
    minimum=2,
)
DATASET_IMPORT_MAX_RETRIES = _env_int(
    "DATASET_IMPORT_MAX_RETRIES",
    2,
    minimum=0,
)
DATASET_IMPORT_RETRY_BACKOFF_SECONDS = _env_int(
    "DATASET_IMPORT_RETRY_BACKOFF_SECONDS",
    30,
    minimum=1,
)
DATASET_IMPORT_RETRY_BACKOFF_MAX_SECONDS = _env_int(
    "DATASET_IMPORT_RETRY_BACKOFF_MAX_SECONDS",
    300,
    minimum=1,
)
DATASET_IMPORT_RATE_LIMIT = os.environ.get(
    "DATASET_IMPORT_RATE_LIMIT",
    "12/h",
)
DATASET_IMPORT_POLL_INTERVAL_MS = _env_int(
    "DATASET_IMPORT_POLL_INTERVAL_MS",
    1500,
    minimum=250,
)
DATASET_IMPORT_REQUIRE_AUTHENTICATION = _env_bool(
    "DATASET_IMPORT_REQUIRE_AUTHENTICATION",
    True,
)
DATASET_IMPORT_ALLOW_PRIVATE = _env_bool(
    "DATASET_IMPORT_ALLOW_PRIVATE",
    False,
)
DATASET_IMPORT_ALLOWED_LICENSES = frozenset(
    _env_csv(
        "DATASET_IMPORT_ALLOWED_LICENSES",
        (
            "cc0-1.0,cc-by-4.0,cc-by-sa-4.0,pddl-1.0,"
            "odc-by-1.0,odbl-1.0,apache-2.0,mit"
        ),
    )
)
DATASET_IMPORTED_VISIBILITY = os.environ.get(
    "DATASET_IMPORTED_VISIBILITY",
    "internal",
).strip().lower()

if (
    DATASET_IMPORT_HARD_TIME_LIMIT_SECONDS
    <= DATASET_IMPORT_SOFT_TIME_LIMIT_SECONDS
):
    raise ValueError(
        "DATASET_IMPORT_HARD_TIME_LIMIT_SECONDS must be greater than "
        "DATASET_IMPORT_SOFT_TIME_LIMIT_SECONDS."
    )

if DATASET_IMPORTED_VISIBILITY not in {
    "public",
    "internal",
    "private",
}:
    raise ValueError(
        "DATASET_IMPORTED_VISIBILITY must be public, internal, or private."
    )

minimum_visibility_timeout = max(
    SEARCH_RUN_TIMEOUT_SECONDS + 30,
    SEARCH_PROVIDER_RETRY_BACKOFF_MAX_SECONDS
    + SEARCH_PROVIDER_HARD_TIME_LIMIT_SECONDS
    + 30,
    SEARCH_DETAIL_RETRY_BACKOFF_MAX_SECONDS
    + SEARCH_DETAIL_HARD_TIME_LIMIT_SECONDS
    + 30,
    DATASET_IMPORT_RETRY_BACKOFF_MAX_SECONDS
    + DATASET_IMPORT_HARD_TIME_LIMIT_SECONDS
    + 30,
)

if CELERY_VISIBILITY_TIMEOUT < minimum_visibility_timeout:
    raise ValueError(
        "CELERY_VISIBILITY_TIMEOUT_SECONDS must be at least "
        f"{minimum_visibility_timeout} for the configured search deadline, "
        "retry delay, and hard task time limits."
    )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "config.logging.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": os.environ.get("CELERY_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": os.environ.get("APP_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
