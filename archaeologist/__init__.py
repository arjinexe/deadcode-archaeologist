"""
DeadCode Archaeologist — Unearthing the code that time forgot.

A tool for mining git repositories for dead, deleted, and abandoned code.
"""

__version__ = "1.0.1"
__author__ = "DeadCode Archaeologist Contributors"
__license__ = "MIT"

from .analyzer import Analyzer
from .excavator import Excavator
from .models import Artifact, ArtifactType, ExcavationReport
from .reporter import make_reporter

__all__ = [
    "Artifact",
    "ArtifactType",
    "ExcavationReport",
    "Excavator",
    "Analyzer",
    "make_reporter",
]
