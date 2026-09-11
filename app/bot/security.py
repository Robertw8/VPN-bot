from app.config import Settings
from app.domain.errors import Forbidden


def require_admin(settings: Settings, telegram_id: int) -> None:
    if telegram_id not in settings.admin_telegram_ids:
        raise Forbidden()
