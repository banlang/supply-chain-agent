from pydantic import BaseModel, Field
from typing import List, Literal


class SKURisk(BaseModel):
    sku: str = Field(description="SKU code, e.g. SKU0")
    product_type: str = Field(description="haircare, skincare, or cosmetics")
    risk_level: Literal["Critical", "High", "Medium"]
    score: int = Field(ge=0, le=7, description="Composite risk score 0-7")
    supplier: str
    flags: List[str] = Field(
        description="Specific risk drivers e.g. ['lead_time=29d', 'defect=4.1%', 'inspection=Fail']"
    )
    recommendation: str = Field(
        default="",
        description="Required action — populate for Critical SKUs only, empty string otherwise"
    )


class RiskDetectionOutput(BaseModel):
    critical_count: int = Field(ge=0)
    high_count: int = Field(ge=0)
    medium_count: int = Field(ge=0)
    sku_risks: List[SKURisk]


class ReorderDecision(BaseModel):
    sku: str = Field(description="SKU code, e.g. SKU3")
    product_type: str = Field(description="haircare, skincare, or cosmetics")
    action: Literal["URGENT_REORDER", "REORDER", "MONITOR"] = Field(
        description=(
            "URGENT_REORDER = Critical risk, act within 24h; "
            "REORDER = High risk, act this week; "
            "MONITOR = Medium risk, watch next cycle"
        )
    )
    recommended_supplier: str = Field(
        description="Supplier name with the lowest composite score from the comparison tool"
    )
    order_quantity: int = Field(
        gt=0,
        description="ceil(daily_demand x recommended_supplier_lead_time x 1.5)"
    )
    reason: str = Field(
        description="One sentence combining risk level, supplier score, and why this quantity"
    )
    rejected_suppliers: List[str] = Field(
        description="All other suppliers that were considered but not recommended"
    )


class ReorderReport(BaseModel):
    decisions: List[ReorderDecision]
