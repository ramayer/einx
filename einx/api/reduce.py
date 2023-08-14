from einx.expr import stage1, stage2, stage3, solve, Condition
import einx
from . import util
from functools import partial

_any = any
_op_names = ["sum", "mean", "var", "std", "prod", "count_nonzero", "any", "all", "max", "min"]

@einx.lru_cache
def _parse(description, tensor_shape, conditions=[], output_shape=None, output_ndims=None, keepdims=None, cse=True, **parameters):
    if isinstance(description, tuple):
        if len(description) != 2:
            raise ValueError("Expected tuple of length 2")
        for k in parameters:
            if k in description[1]:
                raise ValueError("Parameter '{k}' is given twice")
        parameters.update(description[1])
        description = description[0]
    if not isinstance(description, str):
        raise ValueError("First argument must be an operation string")

    if "->" in description:
        if not keepdims is None:
            raise ValueError("keepdims cannot be given when using '->'")
        description = description.split("->")
        if len(description) != 2:
            raise ValueError("Operation cannot contain more than one '->'")
        expr_in, expr_out = description
        if "," in expr_in or "," in expr_out:
            raise ValueError("Expected single input and output description")

        # Drop unnecessary parameters
        exprs = [stage1.parse(expr) for expr in [expr_in, expr_out]]
        def is_necessary_parameter(k):
            for expr in exprs:
                if _any(var.name == k for var in expr.variables):
                    return True
            return False
        parameters = {k: v for k, v in parameters.items() if is_necessary_parameter(k)}

        exprs = solve(
               [Condition(expr=expr_in, value=tensor_shape, depth=0)] \
             + [Condition(expr=expr_out, value=output_shape, shape=(output_ndims,) if not output_ndims is None else None, depth=0)] \
             + [Condition(expr=k, value=[v]) for k, v in parameters.items()] \
             + list(conditions)
        )[:2]
    else:
        # Drop unnecessary parameters
        exprs = [stage1.parse(expr) for expr in [description]]
        def is_necessary_parameter(k):
            for expr in exprs:
                if _any(var.name == k for var in expr.variables):
                    return True
            return False
        parameters = {k: v for k, v in parameters.items() if is_necessary_parameter(k)}

        expr_in = solve(
               [Condition(expr=description, value=tensor_shape, depth=0)] \
             + [Condition(expr=k, value=[v]) for k, v in parameters.items()] \
             + list(conditions)
        )[0]
        def any_parent_is_marker(node):
            if isinstance(node, stage3.Group) and node.front == "[":
                return True
            elif node.parent is None:
                return False
            else:
                return any_parent_is_marker(node.parent)
        def remove(node):
            return (not keepdims or isinstance(node, stage3.Variable)) and any_parent_is_marker(node)
        expr_out = stage3.remove(expr_in, remove, keepdims=keepdims)
        expr_out = stage3.prune_group(expr_out, lambda n: n.front == "[")
        expr_in = stage3.prune_group(expr_in, lambda n: n.front == "[")
        exprs = [expr_in, expr_out]
    for expr in exprs:
        for expr in expr.traverse():
            if isinstance(expr, stage3.Group) and not expr.front in ["", "(", "["]:
                raise ValueError(f"Found marker group {expr} which is not allowed")

    if cse:
        exprs = einx.expr.cse.mark_common_subexpressions(exprs)
    expr_in, expr_out = exprs

    return expr_in, expr_out

def reduce(description, tensor, op, conditions=[], output_shape=None, output_ndims=None, keepdims=None, return_named=False, cse=True, **parameters):
    backend = einx.backend.get([tensor])
    tensor = tensor if util.is_tensor_factory(tensor) else backend.to_tensor(tensor)

    expr_in, expr_out = _parse(description, util.get_shape(tensor), conditions, output_shape, output_ndims, keepdims, cse=cse, **parameters)

    tensor_in = einx.op.Tensor(tensor, expr_in, backend=backend)

    tensor_out = einx.op.reduce(tensor_in, expr_out, op=op, backend=backend)

    return tensor_out if return_named else tensor_out.value
reduce.parse = _parse
reduce.op_names = _op_names

def _make(name):
    def func(*args, **kwargs):
        return reduce(*args, op=name, **kwargs)
    func.__name__ = name
    func.parse = partial(_parse, op=name)
    globals()[name] = func

for name in _op_names:
    _make(name)