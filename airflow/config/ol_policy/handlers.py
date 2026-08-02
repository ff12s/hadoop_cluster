"""Исключения политики."""


class NoEndpointsError(RuntimeError):
    """Резолвер не дал ни одного WebHDFS-эндпоинта."""
