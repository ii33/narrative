"""Thin graph-project entry points.

The legacy v3 Operations facade has been removed. Use GraphOperations for graph
projects and project_catalog for listing/creating/opening them.
"""

from __future__ import annotations

from pathlib import Path

from .graph_operations import GraphOperations
from .project_catalog import (
    create_graph_project,
    list_projects,
    open_graph_project,
)


class Operations:
    create_graph_project = staticmethod(create_graph_project)
    open_graph_project = staticmethod(open_graph_project)
    list_projects = staticmethod(list_projects)

    @staticmethod
    def open(path: str | Path) -> GraphOperations:
        return open_graph_project(path)
