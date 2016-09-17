# Copyright (c) 2015 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

from hashlib import Hash
from pathlib import PurePosixPath
from random import Random as RandomT
from shutil import copyfileobj
from typing import Any, NamedTuple, cast
from urllib.error import URLError
import logging

from . import config
from . import util
from .util import PathExt

log = logging.getLogger(__name__)


class Distfile:
    Access = NamedTuple('D_Access', [
        ('urlopen', util.UrlopenFn),
        ('subprocess', util.RunCommand),
        ('random', RandomT)])

    def __init__(self, distdir: PathExt, io: Access, name: str, checksum: Hash,
                 master_sites: List[str],
                 patches: List[PathExt],
                 unsafe_string_sources: List[str]) -> None:
        for patch in patches:
            if not patch.exists():
                raise Exception('Patch %s does not exist' % patch)

        self._distdir = distdir
        self._io = io
        self._name = name
        self._checksum = checksum
        self._patches = patches
        self._unsafe_string_sources = unsafe_string_sources
        self._pathname = distdir / self._name  # type: PathExt

        # Compute distfile URLs based on the provided list of sites.
        # Also add fallback URLs in case the master sites are down.
        self._urls = {
            site + PurePosixPath(self._name).name for site in master_sites
        } | {
            site + self._name for site in config.FALLBACK_MIRRORS
        }

    @staticmethod
    def _apply_patch(patch: PathExt, target: PathExt,
                     subprocess: util.RunCommand):
        # Automatically determine the patchlevel by taking a look at the
        # first filename in the patch.
        patchlevel = 0
        with patch.open('rb') as f:
            for l in f.readlines():
                if l.startswith(b'--- '):
                    filename = str(l[4:-1].split(b'\t', 1)[0], encoding='ASCII')
                    while True:
                        if (target / filename).exists():
                            # Correct patchlevel determined.
                            break
                        # Increment patchlevel once more.
                        s = filename.split('/', 1)
                        if len(s) < 2:
                            # File does not seem to exist at all. Don't
                            # compute the patchlevel for this patch.
                            patchlevel = 0
                            break
                        filename = s[1]
                        patchlevel += 1
                    break

        # Apply the patch.
        with patch.open() as f:
            subprocess.check_call(
                ['patch', '-d', str(target), '-tsp%d' % patchlevel], stdin=f)

        # Delete .orig files that patch leaves behind.
        for path in util.walk_files(target):
            if path.suffix == '.orig':
                path.unlink()

    def _extract_unpatched(self, target: PathExt) -> PathExt:
        io = self._io

        # Fetch and extract tarball.
        self._fetch()
        platform = target.platform()
        tar = platform(config.DIR_BUILDROOT) / 'bin/bsdtar'
        util.make_dir(target)
        io.subprocess.check_call([str(tar) if tar.exists() else 'tar',
                                  '-xC', str(target),
                                  '-f', str(self._pathname)])

        # Remove leading directory names.
        while True:
            entries = list(target.iterdir())
            if len(entries) != 1:
                return target
            subdir = cast(PathExt, entries[0])
            if not subdir.is_dir():
                return target
            target = subdir

    def _fetch(self) -> None:
        io = self._io
        for i in range(10):
            log.info('CHECKSUM %s', self._pathname)
            # Validate the existing file on disk.
            try:
                if util.sha256(self._pathname).hexdigest() == self._checksum:
                    return
            except FileNotFoundError as e:
                log.warning(str(e))

            url = io.random.sample(self._urls, 1)[0]
            log.info('FETCH %s', url)
            try:
                util.make_parent_dir(self._pathname)
                with util.unsafe_fetch(url, io.urlopen) as fin, \
                     self._pathname.open('wb') as fout:
                    copyfileobj(fin, fout)
            except ConnectionResetError as e:
                log.warning(str(e))
            except URLError as e:
                log.warning(str(e))
        raise Exception('Failed to fetch %s' % self._name)

    def extract(self, target: PathExt):
        io = self._io
        target = self._extract_unpatched(target)
        # Apply patches.
        for patch in self._patches:
            self._apply_patch(patch, target, io.subprocess)
        # Add markers to sources that depend on unsafe string sources.
        for filename in self._unsafe_string_sources:
            path = target / filename
            with path.open('rb') as fin, (path + '.new').open('wb') as fout:
                fout.write(
                    bytes('#define _CLOUDLIBC_UNSAFE_STRING_FUNCTIONS\n',
                          encoding='ASCII'))
                fout.write(fin.read())
            (path + '.new').rename(path)
        return target

    def fixup_patches(self, tmpdir: PathExt):
        io = self._io
        if not self._patches:
            return
        # Extract one copy of the code to diff against.
        util.remove(tmpdir)
        orig_dir = self._extract_unpatched(tmpdir / 'orig')
        for path in util.walk_files(orig_dir):
            if path.suffix == '.orig':
                path.unlink()

        for patch in sorted(self._patches):
            log.info('FIXUP %s', patch)
            # Apply individual patches to the code.
            patched_dir = tmpdir / 'patched'
            util.remove(patched_dir)
            patched_dir = self._extract_unpatched(patched_dir)
            self._apply_patch(patch, patched_dir, io.subprocess)

            # Generate a new patch.
            util.diff(orig_dir, patched_dir, patch, io.subprocess)
