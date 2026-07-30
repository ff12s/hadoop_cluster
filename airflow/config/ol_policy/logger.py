import logging
from typing import Dict, Tuple

from . import utils

logger = logging.getLogger("ol_policy")

_warned: Dict[Tuple[str, ...], float] = {}
_WARN_TTL_SEC = 300.0

def warn_once(key: Tuple[str, ...], msg: str, *args: object, exc_info: bool = False) -> None:
    """Пишет warning не чаще одного раза в ``_WARN_TTL_SEC`` по ключу дедупликации.

    Причины, зависящие от таски, дедуплицируются ключом ``(причина, dag_id, task_id)``,
    общие на процесс — ключом ``(причина,)``: так каждая пропущенная таска попадает
    в лог собственной строкой, а процессные причины не спамят.

    :param key: ключ дедупликации.
    :param msg: шаблон сообщения для logging.
    :param args: аргументы шаблона; значения из Variable сюда не передаются.
    :param exc_info: писать ли traceback текущего исключения.
    :return: None.
    """
    now = utils.now()
    last = _warned.get(key)
    if last is not None and now - last < _WARN_TTL_SEC:
        return
    _warned[key] = now
    logger.warning(msg, *args, exc_info=exc_info)