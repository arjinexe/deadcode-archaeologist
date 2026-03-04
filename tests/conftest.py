"""
Shared fixtures for DeadCode Archaeologist tests.
"""

from pathlib import Path

import pytest

try:
    import git

    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False


@pytest.fixture
def tmp_python_repo(tmp_path: Path):
    """
    Create a temporary git repository with some Python files
    that have various dead code patterns.
    """
    if not GIT_AVAILABLE:
        pytest.skip("GitPython not available")

    repo = git.Repo.init(str(tmp_path))
    repo.config_writer().set_value("user", "name", "Test Dev").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    # Initial commit with a Python file
    main_py = tmp_path / "main.py"
    main_py.write_text(
        "import os\n"
        "import sys\n"
        "import json  # ghost import - never used\n\n"
        "def main():\n"
        "    x = 42  # lone variable\n"
        "    print('Hello')\n\n"
        "def dead_function():\n"
        '    """This function is never called."""\n'
        "    return 'I am alone'\n\n"
        "# TODO: refactor this entire module\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )

    repo.index.add(["main.py"])
    repo.index.commit("Initial commit")

    # Second commit: add some code
    utils_py = tmp_path / "utils.py"
    utils_py.write_text(
        "def helper_a(x, y):\n"
        "    return x + y\n\n"
        "def helper_b(data):\n"
        "    # Process the data using process_data() function\n"
        "    return sorted(data)\n\n"
        "def experimental_feature():\n"
        '    """An experimental thing that never shipped."""\n'
        "    import subprocess\n"
        "    result = subprocess.run(['ls'], capture_output=True)\n"
        "    return result.stdout\n"
    )

    repo.index.add(["utils.py"])
    repo.index.commit("Add utility functions")

    # Third commit: delete the experimental feature
    utils_py.write_text(
        "def helper_a(x, y):\n    return x + y\n\ndef helper_b(data):\n    return sorted(data)\n"
    )

    repo.index.add(["utils.py"])
    repo.index.commit("Remove experimental feature (broke prod)")

    return tmp_path


@pytest.fixture
def tmp_python_files(tmp_path: Path):
    """
    Create a temporary directory with Python files
    for static analysis tests (no git).
    """
    # File with dead function
    (tmp_path / "dead.py").write_text(
        "def alive_function():\n"
        "    return dead_function()  # Wait, this calls dead_function!\n\n"
        "def dead_function():\n"
        '    """Dead."""\n'
        "    return 42\n\n"
        "def truly_dead():\n"
        '    """Truly never called."""\n'
        "    return 'ghost'\n"
    )

    # File with ghost imports
    (tmp_path / "imports.py").write_text(
        "import os\n"
        "import sys\n"
        "import json  # ghost\n"
        "from pathlib import Path  # ghost\n"
        "from typing import List\n\n"
        "def main():\n"
        "    path = os.path.join('a', 'b')\n"
        "    args: List[str] = sys.argv\n"
        "    return path, args\n"
    )

    # File with lone variables
    (tmp_path / "vars.py").write_text(
        "def process():\n"
        "    result = compute()  # lone variable\n"
        "    final = 42\n"
        "    return final\n\n"
        "def compute():\n"
        "    return 100\n"
    )

    return tmp_path
