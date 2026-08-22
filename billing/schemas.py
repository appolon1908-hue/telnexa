from decimal import Decimal
from pydantic import BaseModel, Field


class SendRequest(BaseModel):
    billing_account_id: str
    destination: str = Field(pattern=r"^\+[1-9][0-9]{5,18}$")
    sender: str = Field(min_length=1, max_length=20)
    content: str = Field(min_length=1, max_length=5000)
    category: str = Field(default="transactional", pattern="^(transactional|service|marketing)$")
    client_reference: str | None = Field(default=None, max_length=120)
    simulator_outcome: str = Field(
        default="delivered",
        pattern="^(submitted|sent|delivered|failed|expired|undeliverable|reject|submission_failed)$",
    )


class CreditRequest(BaseModel):
    billing_account_id: str
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=200)
