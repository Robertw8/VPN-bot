"""Russian labels shared by administrative and user screens."""

STATUS_LABELS = {
    "PENDING": "Ожидает обработки",
    "PAID": "Оплачено",
    "CANCELLED": "Отменено",
    "EXPIRED": "Истёк срок",
    "FAILED": "Ошибка",
    "APPROVED": "Одобрено",
    "REJECTED": "Отклонено",
    "ACTIVE": "Активна",
    "DISABLED": "Отключена",
    "DELETED": "Удалена",
    "ERROR": "Ожидает восстановления",
}


def status_label(value: str) -> str:
    return STATUS_LABELS.get(value, "Неизвестный статус")
