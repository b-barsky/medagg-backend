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


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")

DEBUG = os.environ.get(
    "DJANGO_DEBUG_MODE",
    "false",
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

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
    "DEFAULT_PAGINATION_CLASS": (
        "rest_framework.pagination.PageNumberPagination"
    ),
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
}

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
    300,
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

if not SEARCH_TASK_QUEUE:
    raise ValueError("SEARCH_TASK_QUEUE cannot be blank.")

if not SEARCH_DETAIL_TASK_QUEUE:
    raise ValueError("SEARCH_DETAIL_TASK_QUEUE cannot be blank.")

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

minimum_visibility_timeout = max(
    SEARCH_RUN_TIMEOUT_SECONDS + 30,
    SEARCH_PROVIDER_RETRY_BACKOFF_MAX_SECONDS
    + SEARCH_PROVIDER_HARD_TIME_LIMIT_SECONDS
    + 30,
    SEARCH_DETAIL_RETRY_BACKOFF_MAX_SECONDS
    + SEARCH_DETAIL_HARD_TIME_LIMIT_SECONDS
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
