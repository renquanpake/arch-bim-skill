# compliance 包：规范合规规则库 + 审查入口
from .rules_accessible import (
    Rule,
    Violation,
    ComplianceContext,
    RULES,
    check,
    summarize,
    to_dict,
    context_from_brief,
    context_from_layout,
)
