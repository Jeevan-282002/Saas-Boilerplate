from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class VerifiedSubscriptionEvent:
    event_id: str
    subscription_reference: str
    status: str
    period_start: datetime
    period_end: datetime


class PaymentProvider(Protocol):
    def create_checkout_session(self, *, subscription_reference: str, return_url: str) -> str:
        """Create a provider checkout session; this never marks a subscription paid."""
        ...

    def verify_webhook(self, *, payload: bytes, signature: str) -> VerifiedSubscriptionEvent:
        """Return an event only after validating provider authenticity and payment state."""
        ...

    def cancel_subscription(self, *, subscription_reference: str) -> None:
        """Request cancellation from the provider."""
        ...
