"""Historical transition module retired in graph 5.

RunStore/list/events read historical files without importing historical writers.
Use pipeline.registry for current execution and candidate_import for explicit migration.
"""
from ..errors import ContractError

def __getattr__(name):
    if name.startswith("_"):
        raise AttributeError(name)
    raise ContractError("transitional execution is retired; use graph 5 registry or explicit candidate import")
