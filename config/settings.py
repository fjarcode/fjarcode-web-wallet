"""Django settings for FJAR Wallet."""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'replace-me-in-production')
DEBUG = os.getenv('DJANGO_DEBUG', 'False').lower() == 'true'

allowed_hosts = os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost,213.181.99.67,wallet.fjarcode.com')
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts.split(',') if host.strip()]


# Application definition

INSTALLED_APPS = [
    'django.contrib.sessions',
    'django.contrib.staticfiles',
    'wallet',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.dummy',
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'

ENABLE_HTTPS_SECURITY = os.getenv('ENABLE_HTTPS_SECURITY', 'False').lower() == 'true'

if ENABLE_HTTPS_SECURITY:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = int(os.getenv('SESSION_COOKIE_AGE_SECONDS', '2592000'))
SESSION_EXPIRE_AT_BROWSER_CLOSE = os.getenv('SESSION_EXPIRE_AT_BROWSER_CLOSE', 'False').lower() == 'true'

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'fjar_wallet_runtime',
        'TIMEOUT': int(os.getenv('CACHE_DEFAULT_TIMEOUT_SECONDS', '900')),
    }
}

WALLET_CACHE_TTL_SECONDS = int(os.getenv('WALLET_CACHE_TTL_SECONDS', '2592000'))
WALLET_UNLOCK_TTL_SECONDS = int(os.getenv('WALLET_UNLOCK_TTL_SECONDS', '900'))
WALLET_SEND_DEBUG = os.getenv('WALLET_SEND_DEBUG', 'False').lower() == 'true'


def _parse_electrum_servers(raw_servers):
    parsed = []
    for item in raw_servers.split(','):
        value = item.strip()
        if not value:
            continue
        parts = value.split(':')
        if len(parts) < 2:
            continue

        host = parts[0].strip()
        if not host:
            continue

        try:
            port = int(parts[1])
        except ValueError:
            continue

        use_ssl = True
        if len(parts) >= 3:
            use_ssl = parts[2].strip().lower() != 't'

        parsed.append({'host': host, 'port': port, 'ssl': use_ssl})

    return parsed


_default_electrum = '127.0.0.1:50001:t,electrumx01.fjarcode.com:50002:s,electrumx02.fjarcode.com:50002:s,electrumx03.fjarcode.com:50001:t'
ELECTRUM_SERVERS = _parse_electrum_servers(os.getenv('ELECTRUM_SERVERS', _default_electrum))
ELECTRUM_TIMEOUT_SECONDS = int(os.getenv('ELECTRUM_TIMEOUT_SECONDS', '5'))
