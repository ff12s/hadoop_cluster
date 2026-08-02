"""Логгер политики с дедупликацией сообщений."""

from __future__ import annotations

import logging

logger = logging.getLogger("ol_policy")

_warned: set[tuple[str, ...]] = set()


def warn_once(key: tuple[str, ...], msg: str, *args: object, exc_info: bool = False) -> None:
    """Пишет warning один раз на процесс для каждого ключа дедупликации.

    :param key: ключ дедупликации.
    :param msg: шаблон сообщения для logging.
    :param args: аргументы шаблона; значения из Variable сюда не передаются.
    :param exc_info: писать ли traceback текущего исключения.
    :return: None.
    """
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(msg, *args, exc_info=exc_info)


def reset() -> None:
    """Сбрасывает дедупликацию warning'ов.

    :return: None.
    """
    _warned.clear()
