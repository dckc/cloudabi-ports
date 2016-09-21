# Copyright (c) 2015 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

from itertools import filterfalse, tee
# Use the Path type only; the constructor is ambient authority
from pathlib import Path as PathT, PurePath, PurePosixPath
from shutil import copyfileobj
from subprocess import PIPE, Popen as PopenT
from typing import (Any, AnyStr, BinaryIO, Callable, Dict, Generic, Iterator,
                    List, Tuple, Type, Union, cast)
import gzip
import hashlib
import ssl

UrlopenFn = Callable[..., BinaryIO]

_SubPath = Union [str, PurePath]


class RunCommand(object):
    def Popen(self, args: List[str], **kwargs) -> PopenT:
        raise NotImplementedError

    def check_call(self, args: List[str], **kwargs) -> int:
        raise NotImplementedError

    def check_output(self, args: str, **kwargs) -> Any:
        raise NotImplementedError


class PathExt(PathT):
    # fix lack of parameter in PurePath type decl
    # ref https://github.com/python/typeshed/issues/553
    def pathjoin(self, *key: _SubPath) -> 'PathExt':
        raise NotImplementedError

    def __truediv__(self, key: _SubPath) -> 'PathExt':
        raise NotImplementedError

    def iterdir(self) -> Iterator['PathExt']:  # type: ignore
        raise NotImplementedError

    def relative_to(self, *other: _SubPath) -> 'PathExt':
        raise NotImplementedError

    def resolve(self) -> 'PathExt':
        raise NotImplementedError

    def with_name(self, name: str) -> 'PathExt':   # type: ignore
        raise NotImplementedError

    parent = None  # type: PathExt

    def __add__(self, suffix: str) -> 'PathExt':
        raise NotImplementedError

    def copy(self, target: PathT):
        raise NotImplementedError

    def copystat(self, target: PathT):
        raise NotImplementedError

    def copymode(self, target: PathT):
        raise NotImplementedError

    def rmtree(self) -> None:
        raise NotImplementedError

    def readlink(self) -> AnyStr:
        raise NotImplementedError

    def link(self, dst: PathT):
        raise NotImplementedError

    def platform(self) -> Callable[[object], 'PathExt']:
        raise NotImplementedError


def mix_shutil_path(concrete: Type[PurePosixPath],
                    config_path: Callable[[str], str],
                    shutil, os_link) -> Type[PathExt]:
    class PathWithShUtil(concrete, PathExt):   # type: ignore
        def __add__(self, suffix: str) -> PathExt:
            return self.with_name(self.name + suffix)

        def copy(self, target: PathExt):
            shutil.copy(str(self), str(target))

        def copystat(self, target: PathExt):
            shutil.copystat(str(self), str(target))

        def copymode(self, target: PathExt):
            shutil.copymode(str(self), str(target))

        def rmtree(self):
            shutil.rmtree(str(self))

        def readlink(self):
            # KLUDGE: peek into undocumented pathlib API
            return self._accessor.readlink(str(self))

        def link(self, dst: PathExt):
            os_link(str(self), str(dst))

        def platform(self) -> Callable[[object], PathExt]:
            allfiles = self / '/'  # KLUDGE
            def get(key):
                return allfiles / config_path(key)

    return PathWithShUtil


def copy_file(source: PathExt, target: PathExt, preserve_attributes: bool):
    if target.exists():
        raise Exception('About to overwrite %s with %s' % (source, target))
    if source.is_symlink():
        # Preserve symbolic links.
        destination = source.readlink()
        if PurePosixPath(destination).is_absolute():
            raise Exception(
                '%s points to absolute location %s',
                source, destination)
        target.symlink_to(destination)
    elif source.is_file():
        # Copy regular files.
        source.copy(target)
        if preserve_attributes:
            source.copystat(target)
    else:
        # Bail out on anything else.
        raise Exception(source + ' is of an unsupported type')


def diff(orig_dir: PathExt, patched_dir: PathExt, patch: PathExt,
         subprocess: RunCommand):
    proc = subprocess.Popen(['diff', '-urN', str(orig_dir), str(patched_dir)],
                            stdout=PIPE)
    minline = bytes('--- %s/' % orig_dir, encoding='ASCII')
    plusline = bytes('+++ %s/' % patched_dir, encoding='ASCII')
    with patch.open('wb') as f:
        for l in proc.stdout.readlines():
            if l.startswith(b'diff '):
                # Omit lines that start with 'diff'. They serve
                # no purpose.
                pass
            elif l.startswith(minline):
                # Remove directory name and timestamp.
                f.write(b'--- ' + l[len(minline):].split(b'\t', 1)[0] +
                        b'\n')
            elif l.startswith(plusline):
                # Remove directory name and timestamp.
                f.write(b'+++ ' + l[len(plusline):].split(b'\t', 1)[0] +
                        b'\n')
                pass
            else:
                f.write(l)


def file_contents_equal(path1: PathT, path2: PathT) -> bool:
    # Compare file contents.
    with path1.open('rb') as f1, path2.open('rb') as f2:
        while True:
            b1 = f1.read(16384)
            b2 = f2.read(16384)
            if b1 != b2:
                return False
            elif not b1:
                return True


def gzip_file(source: PathT, target: PathT):
    with source.open('rb') as f1, target.open('wb') as ft, gzip.GzipFile(
            fileobj=ft, mode='wb', mtime=0) as f2:
        copyfileobj(f1, cast(BinaryIO, f2))


def unsafe_fetch(url: str, urlopen: UrlopenFn) -> BinaryIO:
    # Fetch a file over HTTP, HTTPS or FTP. For HTTPS, we don't do any
    # certificate checking. The caller should validate the authenticity
    # of the result.
    try:
        # Python >= 3.4.3.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return urlopen(url, context=ctx)
    except TypeError:
        # Python < 3.4.3.
        return urlopen(url)


def lchmod(path: PathT, mode: int):
    path.lchmod(mode)


def make_dir(path: PathT):
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        pass


def make_parent_dir(path: PathExt):
    make_dir(path.parent)


def _remove(path: PathExt):
    try:
        path.rmtree()
    except FileNotFoundError:
        pass
    except (NotADirectoryError, OSError):
        path.unlink()


def remove(path: PathExt):
    try:
        # First try to remove the file or directory directly.
        _remove(path)
    except PermissionError:
        # If that fails, add write permissions to the directories stored
        # inside and retry.
        for root, dirs, files in walk(path):
            root.chmod(0o755)
        _remove(path)


def remove_and_make_dir(path: PathExt):
    try:
        remove(path)
    except FileNotFoundError:
        pass
    make_dir(path)


def hash_file(path: PathExt, checksum: 'hashlib.Hash'):
    if path.is_symlink():
        checksum.update(bytes(path.readlink(), encoding='ASCII'))
    else:
        with path.open('rb') as f:
            while True:
                data = f.read(16384)
                if not data:
                    break
                checksum.update(data)


def sha256(path: PathExt) -> 'hashlib.Hash':
    checksum = hashlib.sha256()
    hash_file(path, checksum)
    return checksum


def sha512(path: PathExt) -> 'hashlib.Hash':
    checksum = hashlib.sha512()
    hash_file(path, checksum)
    return checksum


def md5(path: PathExt) -> 'hashlib.Hash':
    checksum = hashlib.md5()
    hash_file(path, checksum)
    return checksum


def walk_files(path: PathExt) -> Iterator[PathExt]:
    if path.is_dir():
        for sub in path.iterdir():
            yield from walk_files(sub)
        # Return all symbolic links to directories as well.
        if path.is_symlink():
            yield path.resolve()
    elif path.exists():
        yield path


def walk(path: PathExt) -> Iterator[Tuple[PathExt, List[PathExt],
                                          List[PathExt]]]:
    def is_dir(p):
        return p.is_dir()
    if path.is_dir():
        root = path
        dirs, files = tee(root.iterdir())
        dirs = list(filter(is_dir, dirs))
        files = list(filterfalse(is_dir, files))
        yield root, dirs, files
        for subdir in dirs:
            yield from walk(subdir)


def walk_files_concurrently(source: PathExt, target: PathExt) -> Iterator[Tuple[PathExt, PathExt]]:
    for source_file in walk_files(source):
        target_file = (target / source_file.relative_to(str(source))).resolve()
        yield source_file, target_file
