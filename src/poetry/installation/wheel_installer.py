from __future__ import annotations

import platform
import sys
import os

from pathlib import Path
from typing import TYPE_CHECKING

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.sources import WheelFile
from installer.sources import _WheelFileValidationError
import installer.scripts

from poetry.__version__ import __version__
from poetry.utils._compat import WINDOWS
import installer.scripts

if TYPE_CHECKING:
    from typing import BinaryIO

    from installer.records import RecordEntry
    from installer.scripts import LauncherKind
    from installer.utils import Scheme

    from poetry.utils.env import Env

shebang = '#!/usr/bin/env python'.encode("utf-8")

def _build_shebang(executable: str, forlauncher: bool) -> bytes:
    return shebang

# monkey patch _build_shebang implementation
installer.scripts._build_shebang = _build_shebang

class WheelDestination(SchemeDictionaryDestination):
    """ """

    def write_to_fs(
        self,
        scheme: Scheme,
        path: str,
        stream: BinaryIO,
        is_executable: bool,
    ) -> RecordEntry:
        from installer.records import Hash
        from installer.records import RecordEntry
        from installer.utils import copyfileobj_with_hashing
        from installer.utils import make_file_executable

        basepath = self.get_base_path(scheme)
        target_path = Path(basepath) / path
        
        if target_path.exists():
            # Contrary to the base library we don't raise an error
            # here since it can break namespace packages (like Poetry's)
            pass

        parent_folder = target_path.parent
        if not parent_folder.exists():
            # Due to the parallel installation it can happen
            # that two threads try to create the directory.
            parent_folder.mkdir(parents=True, exist_ok=True)

        with target_path.open("wb") as f:
            hash_, size = copyfileobj_with_hashing(stream, f, self.hash_algorithm)

        if is_executable:
            make_file_executable(target_path)

        return RecordEntry(path, Hash(self.hash_algorithm, hash_), size)
    
    # method override to insert custom path mapping logic similar to in
    # write_to_fs
    def _path_with_destdir(self, scheme: Scheme, path: str) -> str:
        basepath = self.get_base_path(scheme)

        file = os.path.join(basepath, path)
        if self.destdir is not None:
            file_path = Path(file)
            rel_path = file_path.relative_to(file_path.anchor)
            return os.path.join(self.destdir, rel_path)
        return file

    def get_base_path(self, scheme: Scheme) -> str:
        basepath = None
        if os.getenv("POETRY_USE_USER_SITE") == "1":
            if scheme in ["platlib", "purelib"] and "usersite" in self.scheme_dict:
                basepath = self.scheme_dict["usersite"]
            elif scheme == "data" and "userbase" in self.scheme_dict:
                basepath = self.scheme_dict["userbase"]
            elif scheme == "scripts" and "userbase" in self.scheme_dict:
                basepath = self.scheme_dict["userbase"] + "/bin"
            elif scheme == "headers" and "userbase" in self.scheme_dict:
                headers_path = "%s/include/python-%s.%s" % (
                  self.scheme_dict["userbase"], 
                  sys.version_info.major, 
                  sys.version_info.minor
                )
                source_distribution = self.scheme_dict["headers"].split("/")[-1]
                basepath = "%s/%s" % (headers_path, source_distribution)
        if basepath is None:
             basepath = self.scheme_dict[scheme]
        return basepath

    def for_source(self, source: WheelFile) -> WheelDestination:
        scheme_dict = self.scheme_dict.copy()

        scheme_dict["headers"] = str(Path(scheme_dict["headers"]) / source.distribution)

        return self.__class__(
            scheme_dict,
            interpreter=self.interpreter,
            script_kind=self.script_kind,
            bytecode_optimization_levels=self.bytecode_optimization_levels,
        )


class WheelInstaller:
    def __init__(self, env: Env) -> None:
        self._env = env

        script_kind: LauncherKind
        if not WINDOWS:
            script_kind = "posix"
        else:
            if platform.uname()[4].startswith("arm"):
                script_kind = "win-arm64" if sys.maxsize > 2**32 else "win-arm"
            else:
                script_kind = "win-amd64" if sys.maxsize > 2**32 else "win-ia32"

        schemes = self._env.paths
        schemes["headers"] = schemes["include"]

        self._destination = WheelDestination(
            schemes, interpreter=str(self._env.python), script_kind=script_kind
        )

        self.invalid_wheels: dict[Path, list[str]] = {}

    def enable_bytecode_compilation(self, enable: bool = True) -> None:
        self._destination.bytecode_optimization_levels = (-1,) if enable else ()

    def install(self, wheel: Path) -> None:
        with WheelFile.open(wheel) as source:
            try:
                # Content validation is temporarily disabled because of
                # pypa/installer's out of memory issues with big wheels. See
                # https://github.com/python-poetry/poetry/issues/7983
                source.validate_record(validate_contents=False)
            except _WheelFileValidationError as e:
                self.invalid_wheels[wheel] = e.issues
            install(
                source=source,
                destination=self._destination.for_source(source),
                # Additional metadata that is generated by the installation tool.
                additional_metadata={
                    "INSTALLER": f"Poetry {__version__}".encode(),
                },
            )
