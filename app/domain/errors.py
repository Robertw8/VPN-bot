class DomainError(Exception):
    """Safe, user-facing Russian message. Never wrap raw provider errors here."""


class Forbidden(DomainError):
    def __init__(self) -> None:
        super().__init__("Доступ запрещён.")


class InsufficientFunds(DomainError):
    def __init__(self) -> None:
        super().__init__("Недостаточно средств. Пополните баланс.")


class IntegrationUnavailable(DomainError):
    def __init__(self) -> None:
        super().__init__("Провайдер не настроен или временно недоступен.")


class AmbiguousResult(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "Результат операции уточняется. Не повторяйте оплату; обратитесь в поддержку."
        )
