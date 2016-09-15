# Copyright (c) 2015 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

from itertools import filterfalse, tee
from pathlib import PurePosixPath
from shutil import copyfileobj
import gzip
import hashlib
import ssl


def mix_shutil_path(concrete, shutil, os_link):
    class PathWithShUtil(concrete):
        def __add__(self, suffix):
            return self.with_name(self.name + suffix)

        def copy(self, target):
            shutil.copy(str(self), str(target))

        def copystat(self, target):
            shutil.copystat(str(self), str(target))

        def copymode(self, target):
            shutil.copymode(str(self), str(target))

        def rmtree(self):
            shutil.rmtree(str(self))

        def readlink(self):
            # KLUDGE: peek into undocumented pathlib API
            return self._accessor.readlink(str(self))

        def link(self, dst):
            os_link(str(self), str(dst))

    return PathWithShUtil


def copy_file(source, target, preserve_attributes):
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


def diff(orig_dir, patched_dir, patch,
         #@@grep
         subprocess):
    proc = subprocess.Popen(['diff', '-urN', str(orig_dir), str(patched_dir)],
                            stdout=subprocess.PIPE)
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


def file_contents_equal(path1, path2):
    # Compare file contents.
    with path1.open('rb') as f1, path2.open('rb') as f2:
        while True:
            b1 = f1.read(16384)
            b2 = f2.read(16384)
            if b1 != b2:
                return False
            elif not b1:
                return True


def gzip_file(source, target):
    with source.open('rb') as f1, target.open('wb') as ft, gzip.GzipFile(
            fileobj=ft, mode='wb', mtime=0) as f2:
        copyfileobj(f1, f2)


def unsafe_fetch(url, urlopen):
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


def lchmod(path, mode):
    path.lchmod(mode)


def make_dir(path):
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        pass


def make_parent_dir(path):
    make_dir(path.parent)


def _remove(path):
    try:
        path.rmtree()
    except FileNotFoundError:
        pass
    except (NotADirectoryError, OSError):
        path.unlink()


def remove(path):
    try:
        # First try to remove the file or directory directly.
        _remove(path)
    except PermissionError:
        # If that fails, add write permissions to the directories stored
        # inside and retry.
        for root, dirs, files in walk(path):
            root.chmod(0o755)
        _remove(path)


def remove_and_make_dir(path):
    try:
        remove(path)
    except FileNotFoundError:
        pass
    make_dir(path)


def hash_file(path, checksum):
    if path.is_symlink():
        checksum.update(bytes(path.readlink(), encoding='ASCII'))
    else:
        with path.open('rb') as f:
            while True:
                data = f.read(16384)
                if not data:
                    break
                checksum.update(data)


def sha256(path):
    checksum = hashlib.sha256()
    hash_file(path, checksum)
    return checksum


def sha512(path):
    checksum = hashlib.sha512()
    hash_file(path, checksum)
    return checksum


def md5(path):
    checksum = hashlib.md5()
    hash_file(path, checksum)
    return checksum


def walk_files(path):
    if path.is_dir():
        for sub in path.iterdir():
            yield from walk_files(sub)
        # Return all symbolic links to directories as well.
        if path.is_symlink():
            yield path.resolve()
    elif path.exists():
        yield path


def walk(path):
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


def walk_files_concurrently(source, target):
    for source_file in walk_files(source):
        target_file = (target / source_file.relative_to(source)).resolve()
        yield source_file, target_file
