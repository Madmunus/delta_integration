"""Шифрование паролей подключения к 1С при хранении в базе данных.

Используется симметричный Fernet. При наличии переменной окружения
``DELTA_1C_ENCRYPTION_KEY`` берётся она; иначе ключ выводится из
``SECRET_KEY`` приложения (удобно для разработки и тестов).

Для плавной ротации задайте ``DELTA_1C_ENCRYPTION_KEY_OLD`` (можно список
через запятую): расшифровка пробует новый и все старые ключи, а при
запуске приложения значения перешифровываются текущим ключом.

Значения, записанные до внедрения шифрования, остаются обычным текстом:
``decrypt_secret()`` возвращает их без изменений, поэтому старые базы
продолжают работать. Новые и пересохранённые пароли получают префикс
``enc:v1:`` и хранятся в зашифрованном виде.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from flask import current_app

PREFIX = 'enc:v1:'


class SecretDecryptionError(RuntimeError):
    """Не удалось расшифровать пароль (например, сменился ключ)."""


def _derived_key(secret: str) -> str:
    """Ключ Fernet, детерминированно выведенный из строки-секрета."""
    digest = hashlib.sha256(
        ('delta-1c-password:' + secret).encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii')


def _configured_key():
    """Текущий ключ шифрования из конфигурации или окружения."""
    key = current_app.config.get('ONEC_ENCRYPTION_KEY')
    if not key:
        key = os.environ.get('DELTA_1C_ENCRYPTION_KEY')
    if not key:
        secret = current_app.config.get('SECRET_KEY') or 'delta-development-secret'
        return _derived_key(str(secret))
    return str(key)


def _old_keys():
    """Ключи, которыми ещё могут быть зашифрованы старые значения."""
    configured = current_app.config.get('ONEC_ENCRYPTION_KEY_OLD')
    if configured is None:
        configured = os.environ.get('DELTA_1C_ENCRYPTION_KEY_OLD') or ''
    if isinstance(configured, str):
        configured = configured.split(',')
    keys = [str(item).strip() for item in configured if str(item).strip()]

    # Переход с ключа, выведенного из SECRET_KEY, на явный ключ не должен
    # ломать уже сохранённые пароли, даже если OLD-переменную забыли задать.
    if current_app.config.get('ONEC_ENCRYPTION_KEY') or os.environ.get(
            'DELTA_1C_ENCRYPTION_KEY'):
        secret = current_app.config.get('SECRET_KEY') or 'delta-development-secret'
        keys.append(_derived_key(str(secret)))
    return keys


def _fernet_for(key):
    """Привести произвольную строку к корректному ключу Fernet."""
    try:
        raw = str(key).encode('ascii')
        Fernet(raw)  # проверка формата ключа
        return Fernet(raw)
    except (ValueError, UnicodeEncodeError):
        digest = hashlib.sha256(str(key).encode('utf-8')).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def _fernet():
    """Шифр только текущим ключом (для записи и проверки ротации)."""
    return _fernet_for(_configured_key())


def _fernets():
    """Шифр, который умеет читать текущий и старые ключи."""
    keys = [_configured_key()]
    for key in _old_keys():
        if key not in keys:
            keys.append(key)
    return MultiFernet([_fernet_for(key) for key in keys])


def is_encrypted(value):
    """Признак значения, зашифрованного текущей версией хранилища."""
    return bool(value) and value.startswith(PREFIX)


def _encrypt_plain(value) -> str:
    token = _fernet().encrypt(value.encode('utf-8')).decode('ascii')
    return PREFIX + token


def encrypt_secret(value):
    """Вернуть зашифрованное значение; пустое оставить пустым."""
    if not value:
        return ''
    if is_encrypted(value):
        return value
    return _encrypt_plain(value)


def needs_rotation(value) -> bool:
    """Только ли текущим ключом зашифровано значение ``enc:v1:``."""
    if not is_encrypted(value):
        return False
    try:
        _fernet().decrypt(value[len(PREFIX):].encode('ascii'))
    except (InvalidToken, UnicodeDecodeError, ValueError):
        return True
    return False


def reencrypt_secret(value) -> str:
    """Перешифровать значение текущим ключом, даже если оно уже ``enc:v1:``.

    Используется только миграцией ротации: обычное сохранение пароля не
    должно перешифровывать уже валидное значение.
    """
    if not value:
        return ''
    return _encrypt_plain(decrypt_secret(value))


def decrypt_secret(value):
    """Вернуть исходный пароль; старые незашифрованные значения не трогать."""
    if not value:
        return ''
    if not is_encrypted(value):
        return value
    token = value[len(PREFIX):].encode('ascii')
    try:
        return _fernets().decrypt(token).decode('utf-8')
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise SecretDecryptionError(
            'Не удалось расшифровать пароль 1С. '
            'Проверьте переменные DELTA_1C_ENCRYPTION_KEY '
            'и DELTA_1C_ENCRYPTION_KEY_OLD.'
        ) from exc
