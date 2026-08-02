"""A restricted expression evaluator for cross-field rules.

The rules in ``schema/classes/*.yaml`` are genuinely executable, not decorative. That matters
because a rule expressed in YAML and implemented separately in Python drifts, and the drift is
invisible until a rule silently stops meaning what it says.

Safety comes from an allow-list over the parsed AST rather than from sanitising input. Only
comparisons, boolean logic, arithmetic, membership tests, attribute access on known value
objects, and a fixed set of named functions are permitted. Anything else — calls to arbitrary
names, subscripting, comprehensions, lambdas, imports, dunder access — is rejected at compile
time, before evaluation. ``eval`` on unvalidated input would be the obvious way to build this
and is not acceptable in a pipeline that ingests supplier-controlled documents.

Three small extensions to Python syntax, because the rules read better with them and because
the people who maintain them think in YAML and SQL rather than Python:

* ``A implies B``      becomes  ``(not (A)) or (B)``
* ``null``             becomes  ``None``
* ``true`` / ``false`` become   ``True`` / ``False``

The literal casing is not cosmetic. Without it, ``lead_free_compliant == true`` parses as a
comparison against an undefined name, the rule is reported as malformed, and the most
important compliance check in the catalogue silently stops running while still appearing in
the rule list.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Attribute,
    ast.Call,
    ast.Tuple,
    ast.List,
    ast.Set,
    ast.IfExp,
)

# Attribute access is restricted to these names, so `x.__class__` and friends are impossible
# even though ast.Attribute is allowed.
_ALLOWED_ATTRIBUTES = frozenset(
    {"min", "max", "minimum", "maximum", "magnitude", "unit", "value"}
)


class ExpressionError(ValueError):
    """Raised when an expression is malformed or uses something not on the allow-list."""


class SkipRule(Exception):  # noqa: N818 - control flow signal, not an error condition
    """Raised during evaluation when a rule cannot be decided.

    A rule about values you do not have is *not* a failure — reporting it as one would
    manufacture violations out of incomplete data. It is skipped and reported as skipped.

    Deliberately not named ``SkipRuleError``, despite the usual convention. This is a control
    flow signal in the same family as ``StopIteration``: nothing has gone wrong, the
    evaluator is reporting that a verdict is not available. Calling it an error would
    mislabel a normal outcome, and callers would start treating skipped rules as problems.
    """


@dataclass(frozen=True)
class Missing:
    """Sentinel for an attribute the record does not have."""

    code: str

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<missing {self.code}>"


_IMPLIES = re.compile(r"\bimplies\b")
# Bare literals only — a quoted 'true' is a string value and must survive untouched.
_LITERALS = re.compile(r"(?<![\w'\"])(null|true|false)(?![\w'\"])")
_LITERAL_MAP = {"null": "None", "true": "True", "false": "False"}


def desugar(expression: str) -> str:
    """Rewrite the syntax extensions into plain Python."""
    text = _LITERALS.sub(lambda m: _LITERAL_MAP[m.group(1)], expression)
    if _IMPLIES.search(text):
        parts = _IMPLIES.split(text)
        if len(parts) != 2:
            raise ExpressionError(
                f"only one 'implies' is supported per rule; got {len(parts) - 1} in "
                f"{expression!r}"
            )
        antecedent, consequent = (p.strip() for p in parts)
        if not antecedent or not consequent:
            raise ExpressionError(f"'implies' needs an expression on both sides: {expression!r}")
        text = f"(not ({antecedent})) or ({consequent})"
    return text


def compile_expression(
    expression: str, *, allowed_functions: frozenset[str] | set[str] | None = None
) -> ast.Expression:
    """Parse and allow-list check an expression. Raises before anything can execute.

    Passing ``allowed_functions`` rejects unknown calls at compile time rather than at
    evaluation. Evaluation-time rejection is already safe — an unlisted name is never bound,
    so nothing executes — but catching it earlier turns a typo'd function name in a schema
    into a load-time error instead of a per-record one.
    """
    source = desugar(expression)
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"could not parse {expression!r}: {exc.msg}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"{type(node).__name__} is not permitted in a rule expression "
                f"({expression!r})"
            )
        if isinstance(node, ast.Attribute) and node.attr not in _ALLOWED_ATTRIBUTES:
            raise ExpressionError(
                f"attribute access '.{node.attr}' is not permitted ({expression!r})"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExpressionError(
                    f"only direct function calls are permitted ({expression!r})"
                )
            if allowed_functions is not None and node.func.id not in allowed_functions:
                raise ExpressionError(
                    f"function '{node.func.id}' is not available to rule expressions "
                    f"({expression!r})"
                )
    return tree


def evaluate(
    expression: str,
    namespace: dict[str, Any],
    *,
    functions: dict[str, Any] | None = None,
) -> bool:
    """Evaluate a rule to a boolean.

    Raises :class:`SkipRule` when the expression touches a missing value, which the caller
    reports as skipped rather than failed.
    """
    available = functions or {}
    tree = compile_expression(expression, allowed_functions=frozenset(available))
    evaluator = _Evaluator(namespace, available)
    result = evaluator.visit(tree.body)
    if isinstance(result, Missing):
        raise SkipRule(f"expression depends on missing value '{result.code}'")
    return bool(result)


class _Evaluator:
    """Tree-walking interpreter. Explicit rather than using eval, so Missing can propagate."""

    def __init__(self, namespace: dict[str, Any], functions: dict[str, Any]) -> None:
        self._namespace = namespace
        self._functions = functions

    def visit(self, node: ast.AST) -> Any:  # noqa: PLR0911 - a dispatch table reads worse
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id)
        if isinstance(node, ast.Attribute):
            return self._resolve_attribute(node)
        if isinstance(node, ast.UnaryOp):
            return self._unary(node)
        if isinstance(node, ast.BinOp):
            return self._binary(node)
        if isinstance(node, ast.BoolOp):
            return self._boolean(node)
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            return [self.visit(e) for e in node.elts]
        if isinstance(node, ast.IfExp):
            return self.visit(node.body) if self.visit(node.test) else self.visit(node.orelse)
        raise ExpressionError(f"cannot evaluate {type(node).__name__}")

    def _resolve_name(self, name: str) -> Any:
        if name in self._namespace:
            resolved = self._namespace[name]
            # An absent value becomes Missing rather than None so it propagates through the
            # expression and skips the rule, instead of being compared and raising a type
            # error that would be reported as a data problem.
            return Missing(name) if resolved is None else resolved
        if name in self._functions:
            return self._functions[name]
        raise ExpressionError(f"unknown name '{name}' in rule expression")

    def _resolve_attribute(self, node: ast.Attribute) -> Any:
        target = self.visit(node.value)
        if isinstance(target, Missing):
            return target
        if target is None:
            return Missing(getattr(node.value, "id", "?"))
        # Range bounds are exposed under both the short and the model's own names, so rules
        # can read naturally as `.min` / `.max`.
        aliases = {"min": "minimum", "max": "maximum"}
        attr = aliases.get(node.attr, node.attr)
        if not hasattr(target, attr):
            raise ExpressionError(
                f"value of type {type(target).__name__} has no '{node.attr}'"
            )
        return getattr(target, attr)

    def _unary(self, node: ast.UnaryOp) -> Any:
        operand = self.visit(node.operand)
        if isinstance(operand, Missing):
            return operand
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand

    def _binary(self, node: ast.BinOp) -> Any:
        left, right = self.visit(node.left), self.visit(node.right)
        if isinstance(left, Missing):
            return left
        if isinstance(right, Missing):
            return right
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise SkipRule("division by zero in rule expression")
                return left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                return left**right
        except TypeError as exc:
            raise ExpressionError(f"cannot apply operator to those operands: {exc}") from exc
        raise ExpressionError(f"operator {type(node.op).__name__} is not supported")

    def _boolean(self, node: ast.BoolOp) -> Any:
        # Short-circuits like Python, which matters for `implies`: when the antecedent is
        # false the consequent is never evaluated, so a missing value there is harmless.
        if isinstance(node.op, ast.And):
            for operand in node.values:
                value = self.visit(operand)
                if isinstance(value, Missing):
                    return value
                if not value:
                    return False
            return True
        for operand in node.values:
            value = self.visit(operand)
            if isinstance(value, Missing):
                continue  # another branch may still satisfy the disjunction
            if value:
                return True
        return False

    def _compare(self, node: ast.Compare) -> Any:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = self.visit(comparator)

            # Identity comparisons are how a rule asks "is this value present at all", so
            # they must see None rather than propagate Missing. `nominal_size is not null`
            # is a legitimate question about an absent value, not a reason to skip.
            if isinstance(op, ast.Is | ast.IsNot):
                left_value = None if isinstance(left, Missing) else left
                right_value = None if isinstance(right, Missing) else right
                if not self._apply_comparison(op, left_value, right_value):
                    return False
                left = right
                continue

            if isinstance(left, Missing):
                return left
            if isinstance(right, Missing):
                return right
            if not self._apply_comparison(op, left, right):
                return False
            left = right
        return True

    @staticmethod
    def _apply_comparison(op: ast.cmpop, left: Any, right: Any) -> bool:  # noqa: PLR0911
        try:
            if isinstance(op, ast.Eq):
                return left == right
            if isinstance(op, ast.NotEq):
                return left != right
            if isinstance(op, ast.Lt):
                return left < right
            if isinstance(op, ast.LtE):
                return left <= right
            if isinstance(op, ast.Gt):
                return left > right
            if isinstance(op, ast.GtE):
                return left >= right
            if isinstance(op, ast.Is):
                return left is right
            if isinstance(op, ast.IsNot):
                return left is not right
            if isinstance(op, ast.In):
                return _contains(right, left)
            if isinstance(op, ast.NotIn):
                return not _contains(right, left)
        except TypeError as exc:
            raise ExpressionError(f"cannot compare those values: {exc}") from exc
        raise ExpressionError(f"comparison {type(op).__name__} is not supported")

    def _call(self, node: ast.Call) -> Any:
        name = node.func.id  # guaranteed a Name by compile_expression
        function = self._functions.get(name)
        if function is None:
            raise ExpressionError(f"unknown function '{name}' in rule expression")
        args = [self.visit(a) for a in node.args]
        if any(isinstance(a, Missing) for a in args):
            return next(a for a in args if isinstance(a, Missing))
        return function(*args)


def _contains(container: Any, member: Any) -> bool:
    """Membership that works for a multi-valued attribute on either side.

    ``'NSF-61' in approvals`` must work when ``approvals`` is a list, and
    ``end_connection in THREADED_TYPES`` must work when the left side is a scalar. Without
    this, every rule would need to know which shape it was dealing with.
    """
    if container is None:
        return False
    if isinstance(member, list | tuple | set):
        return any(_contains(container, m) for m in member)
    if isinstance(container, str):
        return member == container
    try:
        return member in container
    except TypeError:
        return False
