"""动态法则库第一版编译器。

只负责校验作者/研究法则的结构、作用域、单位和约束可满足性；不执行剧情、不改写正文。
Pint 与 Z3 都是可选依赖，缺失时返回可解释的降级报告而不是伪造通过。
"""
from __future__ import annotations

import ast
import operator
import re
from typing import Any

from models.ledger import LawRecord

_ALLOWED_LAYERS = {
    "REALITY_BASE",
    "DOMAIN_MODEL",
    "AUTHOR_CANON",
    "PLUGIN_TRANSFORM",
    "SCENARIO_OVERRIDE",
}
_ALLOWED_STRENGTHS = {"hard", "soft", "advisory"}
_VAR_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class LawCompileError(ValueError):
    pass


_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_SAFE_CMPOPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _safe_eval_expression(expression: str, variables: dict[str, Any]) -> bool | float | int:
    """解析受限算术/比较表达式，拒绝调用、属性、索引和任意代码。"""
    if not expression.strip() or len(expression) > 512:
        raise LawCompileError("表达式为空或超过长度限制")

    def evaluate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
            return node.value
        if isinstance(node, ast.Name) and node.id in variables:
            return variables[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINOPS:
            return _SAFE_BINOPS[type(node.op)](evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left = evaluate(node.left)
            right = evaluate(node.comparators[0])
            operation = _SAFE_CMPOPS.get(type(node.ops[0]))
            if operation is None:
                raise LawCompileError("不支持的比较运算")
            return operation(left, right)
        raise LawCompileError("表达式包含不允许的语法")

    try:
        return evaluate(ast.parse(expression, mode="eval"))
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise LawCompileError(f"表达式无法安全求值: {exc}") from exc


class LawCompiler:
    def evaluate(self, law: LawRecord, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        context = dict(variables or {})
        for name, spec in law.variables.items():
            if isinstance(spec, dict) and "value" in spec:
                context[name] = spec["value"]
        if not law.expression:
            return {"valid": True, "evaluated": None, "reason": "未声明表达式"}
        try:
            value = _safe_eval_expression(law.expression, context)
            return {"valid": bool(value) if isinstance(value, bool) else True, "evaluated": value, "variables": context}
        except LawCompileError as exc:
            return {"valid": False, "evaluated": None, "error": str(exc), "variables": context}

    def validate(self, law: LawRecord) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        if law.layer not in _ALLOWED_LAYERS:
            errors.append(f"不支持的法则层: {law.layer}")
        if law.strength not in _ALLOWED_STRENGTHS:
            errors.append(f"不支持的法则强度: {law.strength}")
        if law.priority < 0:
            errors.append("priority 不能为负数")
        for name, spec in law.variables.items():
            if not _VAR_RE.fullmatch(str(name)):
                errors.append(f"变量名非法: {name}")
            if not isinstance(spec, dict):
                errors.append(f"变量 {name} 必须是对象定义")
                continue
            if "unit" not in spec:
                warnings.append(f"变量 {name} 未声明 unit，无法完成量纲检查")
            if "value" not in spec and "range" not in spec:
                errors.append(f"变量 {name} 必须声明 value 或 range")
            if isinstance(spec.get("range"), list) and len(spec["range"]) != 2:
                errors.append(f"变量 {name} 的 range 必须包含两个边界")
        if law.layer == "REALITY_BASE" and law.status == "active" and not law.source_evidence_ids:
            errors.append("REALITY_BASE 激活法则必须绑定 source_evidence_ids")
        if law.layer == "SCENARIO_OVERRIDE" and not law.scope:
            warnings.append("SCENARIO_OVERRIDE 未声明作用域，将按调用方局部范围处理")
        unit_report = self._validate_units(law)
        errors.extend(unit_report["errors"])
        warnings.extend(unit_report["warnings"])
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "layer": law.layer,
            "strength": law.strength,
            "solver": unit_report["solver"],
        }

    def _validate_units(self, law: LawRecord) -> dict[str, Any]:
        try:
            import pint  # type: ignore
        except ImportError:
            return {"solver": "unavailable", "errors": [], "warnings": ["Pint 未安装，跳过量纲检查"]}
        ureg = pint.UnitRegistry()
        errors: list[str] = []
        for name, spec in law.variables.items():
            if not isinstance(spec, dict) or "unit" not in spec or "value" not in spec:
                continue
            try:
                ureg.Quantity(spec["value"], spec["unit"])
            except Exception as exc:
                errors.append(f"变量 {name} 的单位/值无效: {exc}")
        return {"solver": "pint", "errors": errors, "warnings": []}

    def check_constraints(self, laws: list[LawRecord]) -> dict[str, Any]:
        """对同一作用域的简单硬约束做可满足性检查；复杂表达式留给后续插件。"""
        reports = [self.validate(law) for law in laws]
        errors = [error for report in reports for error in report["errors"]]
        warnings = [warning for report in reports for warning in report["warnings"]]
        z3_status = "unavailable"
        try:
            import z3  # type: ignore
        except ImportError:
            warnings.append("Z3 未安装，未执行表达式可满足性检查")
        else:
            z3_status = "checked"
            # 第一版只检查显式 numeric value/range 的相同变量冲突。
            for name in {key for law in laws for key in law.variables}:
                constraints = []
                for law in laws:
                    spec = law.variables.get(name)
                    if not isinstance(spec, dict) or law.strength != "hard":
                        continue
                    var = z3.Real(name)
                    if "value" in spec and isinstance(spec["value"], (int, float)):
                        constraints.append(var == spec["value"])
                    bounds = spec.get("range")
                    if isinstance(bounds, list) and len(bounds) == 2:
                        if isinstance(bounds[0], (int, float)):
                            constraints.append(var >= bounds[0])
                        if isinstance(bounds[1], (int, float)):
                            constraints.append(var <= bounds[1])
                if constraints and z3.solve(z3.And(*constraints)) is None:
                    errors.append(f"变量 {name} 的硬约束不可满足")
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "solver": z3_status,
            "law_count": len(laws),
        }
