"""Security-заголовки и жёсткий Content-Security-Policy для всех ответов.

Политика намеренно не содержит ``'unsafe-inline'`` и ``'unsafe-eval'``:
весь JavaScript и CSS приложения лежат во внешних same-origin файлах,
а обработчики событий и инлайн-стили вынесены в ``static/js`` и ``style.css``.
"""
from flask import Flask, Response

from engine.config import Config


def init_app(app: Flask) -> None:
    """Подключить заголовки ко всем ответам, включая статику и ошибки."""

    @app.after_request
    def apply_security_headers(response: Response) -> Response:
        headers = {
            'Content-Security-Policy': app.config.get(
                'SECURITY_CSP', Config.SECURITY_CSP),
            'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY',
            'Referrer-Policy': 'no-referrer',
            'Permissions-Policy': (
                'camera=(), microphone=(), geolocation=(), payment=()'),
        }
        # Браузер игнорирует HSTS на http, поэтому нет зависимости от
        # ProxyFix: достаточно включить его в конфигурации прода.
        if app.config.get('SECURITY_HSTS_ENABLED',
                          Config.SECURITY_HSTS_ENABLED):
            max_age = app.config.get(
                'SECURITY_HSTS_MAX_AGE', Config.SECURITY_HSTS_MAX_AGE)
            headers['Strict-Transport-Security'] = (
                f'max-age={max_age}; includeSubDomains')
        for name, value in headers.items():
            if name not in response.headers:
                response.headers[name] = value
        return response
