from io import BytesIO, StringIO
from pathlib import PurePosixPath
from typing import AnyStr, BinaryIO, Dict, List, Optional
import logging

from src import config
from src import util
from src.builder import Access as BuildAccess
from src.distfile import Distfile, RandomT
from src.repository import Repository

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def test_get_target_packages() -> None:
    io = MockWorld(dict())

    target_packages = get_target_packages(io.cwd(), io.io_d(), io.io_b())

    assert len(target_packages) == len(config.ARCHITECTURES)

    an_arch = next(iter(config.ARCHITECTURES))
    assert ('everything', an_arch) in target_packages
    assert str(target_packages[('everything', an_arch)]) == (an_arch + '-everything 1.0')


def get_target_packages(cwd, io_d, io_b):  # TODO: move this to build_packages.py
    repo = Repository(cwd / '_obj/install', io_d, io_b)

    for file in util.walk_files(cwd / 'packages'):
        if file.name == 'BUILD':
            repo.add_build_file(file, cwd / '_obj/distfiles')
    return repo.get_target_packages()

    
def mock_random() -> RandomT:
    from random import Random
    return Random(1)


class _Writing(BytesIO):
    def __init__(self, finalize):
        BinaryIO.__init__(self)
        self._finalize = finalize

    def __del__(self):
        self._finalize(self.getvalue())


class MockWorld(object):
    class Path(util.PathExt):
        State = Dict[PurePosixPath, Optional[bytes]]

        # Override Path.__new__()
        def __new__(cls, _s, _p):
            self = object.__new__(cls)
            return self

        def __init__(self, state: State, ppp: PurePosixPath) -> None:
            self._ppp = ppp
            self._state = state

        def open(self, mode='r'):
            if 'w' in mode:
                if 'b' not in mode:
                    raise NotImplementedError(mode)
                def done(content):
                    self._state[self._ppp] = content
                return _Writing(done)
            else:
                try:
                    content = self._state[self._parts]
                except KeyError:
                    raise IOError(self._parts)
                return (BytesIO(content) if 'b' in mode
                        else StringIO(content))
            
        def joinpath(self, *other):
            return MockWorld.Path(self._state, self._ppp.joinpath(*other))

        def __truediv__(self, *other):
            return self.joinpath(*other)

        def is_dir(self):
            return self._state.get(self._ppp, 0) is None

        def exists(self):
            return self._ppp in self._state

    def __init__(self, init_fs: Path.State) -> None:
        root = MockWorld.Path(init_fs, PurePosixPath('/'))
        self._cwd =  root / 'tmp'
        self._environ = {}  # type: Dict[str, str]

    def io_d(self) -> Distfile.Access:
        return (self.urlopen, self.subprocess, mock_random())

    def io_b(self) -> BuildAccess:
        return (self.subprocess, self.chdir, self.getenv)

    def cwd(self) -> util.PathExt:
        return self._cwd

    def getenv(self, key: str) -> str:
        return self._environ[key]

    def chdir(self, path: util.PathExt) -> None:
        self._cwd = path

    def urlopen(self) -> BinaryIO:
        raise NotImplementedError


    class RC(util.RunCommand):
        pass

    subprocess = RC()


test_get_target_packages()
